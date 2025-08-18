import os
import json
import boto3
from time import time, sleep
from botocore.exceptions import ClientError

class SensorInstaller:
    def __init__(self):
        self.region = os.getenv('REGION', "us-east-1")
        self.ssm_client = boto3.Session(profile_name="exam").client('ssm', region_name=self.region)
        self.ec2_client = boto3.Session(profile_name="exam").client('ec2', region_name=self.region)
        self.s3_bucket_name = os.getenv("S3_BUCKET_NAME")
        self.timeout = int(os.getenv("RETRY_TIMEOUT", 600))
        self.interval = int(os.getenv("RETRY_WAIT_INTERVAL", 5))
        self.debug = bool(os.getenv("DEBUG", "False"))

    @staticmethod
    def get_tag_value(tag_name, tags):
        for tag in tags:
            if tag["Key"] == tag_name:
                return tag["Value"]

    def get_current_running_instances(self):
        print("INFO :: Fetching all running instances")
        instances = []
        paginator = self.ec2_client.get_paginator("describe_instances")
        for page in paginator.paginate():
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    if instance["State"]["Name"] == "running":
                        current_tags = instance.get('Tags', [])
                        instances.append({"instance_id": instance["InstanceId"], "Tags": current_tags})
        return instances

    def send_ssm_command(self, instance_id: str, commands: list, document_name: str):
        try:
            resp = self.ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName=document_name,
                Parameters={'commands': commands},
            )
            cmd_id = resp['Command']['CommandId']
            print(f"INFO :: Sent command {cmd_id!r} to instance '{instance_id}'")
            return cmd_id
        except ClientError as e:
            print(f"ERROR :: Failed to send command - {e}")
            raise

    def wait_for_command_completion(self, instance_id, command_id, timeout, interval):
        deadline = time() + timeout
        terminal_states = {'Success', 'Cancelled', 'Failed', 'TimedOut', 'Undeliverable', 'Terminated'}
        while time() < deadline:
            try:
                inv = self.ssm_client.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
                status = inv['Status']
                if status in terminal_states:
                    return inv
                print(f"INFO :: {instance_id} status: {status!r}; retrying in {interval}s…")
            except ClientError as e:
                if 'InvocationDoesNotExist' in str(e):
                    print(f"INFO :: Waiting for invocation to be created for {instance_id}…")
                else:
                    print(f"ERROR :: Failed to fetch status for {instance_id}: {e}")
                    break
            sleep(interval)

        print(f"ERROR :: Timeout waiting for {command_id!r} on '{instance_id}'")
        return {'Status': 'Timeout', 'StandardOutputContent': '', 'StandardErrorContent': ''}

    def release_isolation(self, instance_id, security_groups, instance_profile):
        sg_rollback = False
        ip_rollback = False
        try:
            if security_groups != "None":
                security_group_ids = [sg["GroupId"] for sg in json.loads(security_groups.replace("'", "\""))]
                self.ec2_client.modify_instance_attribute(InstanceId=instance_id, Groups=security_group_ids)
                print(f"INFO :: Changed back security groups for '{instance_id}'")
                sg_rollback = True
            else:
                print(f"INFO :: The instance '{instance_id}' had no security groups before isolation")
                sg_rollback = True
        except ClientError as e:
            print(f"ERROR :: Failed to bring security groups back - {e}")

        try:
            associations = self.ec2_client.describe_iam_instance_profile_associations(
                Filters=[{'Name': 'instance-id', 'Values': [instance_id]}]
            )
            if "IamInstanceProfileAssociations" in associations and len(
                    associations["IamInstanceProfileAssociations"]) > 0:
                for association in associations['IamInstanceProfileAssociations']:
                    association_id = association['AssociationId']
                    self.ec2_client.disassociate_iam_instance_profile(AssociationId=association_id)
                    sleep(10)

            if instance_profile != "None":
                current_instance_profile = json.loads(instance_profile.replace("'", "\""))["Arn"].split("/")[-1]
                self.ec2_client.associate_iam_instance_profile(
                    InstanceId=instance_id,
                    IamInstanceProfile={
                        'Name': current_instance_profile
                    }
                )
                ip_rollback = True
                print(f"INFO :: Changed back instance profile for '{instance_id}'")
            else:
                print(f"INFO :: The instance '{instance_id}' had no instance profile before isolation")
                ip_rollback = True
        except ClientError as e:
            print(f"ERROR :: Failed to bring instance profile back - {e}")

        if sg_rollback and ip_rollback:
            return True
        else:
            return False

    def main(self):
        current_instance_tags = self.get_current_running_instances()
        if len(current_instance_tags) > 0:
            for instance in current_instance_tags:
                instance_id = instance["instance_id"]
                tags = instance.get("Tags", [])
                ssm_access = self.get_tag_value("ssm_access", tags)
                sensor_installed = self.get_tag_value("sensor_installed", tags) if not self.debug else False
                platform = self.get_tag_value("platform_details", tags).lower()

                if ssm_access == "False":
                    print(f"ERROR :: the instance '{instance_id}' has no SSM access")
                    exit(1)
                elif sensor_installed == "True":
                    print(f"INFO :: Sensor already installed on '{instance_id}'")
                    exit(0)
                else:
                    commands = []
                    document_name = None
                    if "linux" in platform:
                        commands = ["whoami"]
                        document_name = "AWS-RunShellScript"

                    elif "windows" in platform:
                        document_name = "AWS-RunPowerShellScript"
                        commands = [
                            "New-Item -Path \"C:\\tools\" -ItemType Directory",

                            # Installing AWS CLI Tool
                            "$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {'-arm64'} else {''}",
                            "$msiName = if ($env:AWSCLI_VERSION) { \"AWSCLIV2-$env:AWSCLI_VERSION$arch.msi\" } else { if ($arch) { \"AWSCLIV2$arch.msi\" } else { \"AWSCLIV2.msi\" } }",
                            "$url = \"https://awscli.amazonaws.com/$msiName\"",
                            "$msi = Join-Path $env:TEMP $msiName",
                            "Invoke-WebRequest -Uri $url -OutFile $msi",
                            "$log = Join-Path $env:TEMP 'AWSCLI-install.log'",
                            "Start-Process msiexec.exe -ArgumentList \"/i `\"$msi`\" /qn /norestart /log `\"$log`\"\" -Wait",
                            "& 'C:\\Program Files\\Amazon\\AWSCLIV2\\aws.exe' --version",

                            # Installing Certificates
                            f"& 'C:\\Program Files\\Amazon\\AWSCLIV2\\aws.exe' s3 cp s3://{self.s3_bucket_name}/DeveloperCertificates.zip C:\\tools\\",
                            "Expand-Archive -Path \"C:\\tools\\DeveloperCertificates.zip\" -DestinationPath \"C:\\tools\" -Force;",
                            "Start-Process -FilePath \"C:\\tools\\DeveloperCertificates\\InstallCaCert.bat\" -Wait -NoNewWindow;",
                            "Start-Process -FilePath \"C:\\tools\\DeveloperCertificates\\InstallCert.bat\" -Wait -NoNewWindow;",

                            # Installing Sensor
                            f"& 'C:\\Program Files\\Amazon\\AWSCLIV2\\aws.exe' s3 cp s3://{self.s3_bucket_name}/CybereasonSensor64.exe C:\\tools\\;",
                            f"C:\\tools\\CybereasonSensor64.exe /install /quiet /norestart -l InstallLogs.txt AP_POLICIES_INITIAL_POLICY_ID={os.getenv('INITIAL_POLICY_ID', '3d859d0c-a94f-419a-913a-907fa17e8bee')};",

                            # Installing DLLs
                            f"& 'C:\\Program Files\\Amazon\\AWSCLIV2\\aws.exe' s3 cp s3://{self.s3_bucket_name}/dlls.zip C:\\tools\\;",
                            "Expand-Archive -Path \"C:\\tools\\dlls.zip\" -DestinationPath \"C:\\tools\" -Force;",
                            "Start-Process -FilePath \"C:\\tools\\mitredlls\\switch_dlls.bat\" -Wait -NoNewWindow;",

                            # "Start-Sleep -Seconds 60",
                            # "Restart-Computer -Force"
                        ]


                    if len(commands) > 0 and document_name is not None:
                        cmd_id = self.send_ssm_command(instance_id, commands, document_name)
                        result = self.wait_for_command_completion(instance_id, cmd_id, timeout=self.timeout, interval=self.interval)

                        status = result.get('Status')
                        error_message = result.get('StandardErrorContent').strip()
                        output_message = result.get('StandardOutputContent').strip()
                        if self.debug:
                            status = 'Success'
                        if status == 'Success' and len(error_message) == 0:
                            self.ec2_client.create_tags(
                                Resources=[instance_id],
                                Tags=[
                                    {'Key': "sensor_installed", 'Value': "True"},
                                    {'Key': "last_edited_by", 'Value': "SensorInstallation"}
                                ]
                            )
                            print(f"INFO :: Sensor successfully installed on '{instance_id}'")
                            if self.debug:
                                print("WARNING :: Debug is on - not attempting to release isolation")
                                print()
                                print(output_message or '[no output]')
                                print()
                            else:
                                security_groups = self.get_tag_value("security_groups", tags)
                                instance_profile = self.get_tag_value("previous_instance_profile", tags)
                                released_isolation = self.release_isolation(instance_id, security_groups, instance_profile)

                                if released_isolation:
                                    self.ec2_client.create_tags(
                                        Resources=[instance_id],
                                        Tags=[
                                            {'Key': "security_groups", 'Value': ""},
                                            {'Key': "previous_instance_profile", 'Value': ""},
                                            {'Key': "isolated", 'Value': "False"},
                                            {'Key': "last_edited_by", 'Value': "SensorInstallation"}
                                        ]
                                    )
                                else:
                                    print(f"ERROR :: Failed to release '{instance_id}' from isolation")
                                    exit(1)
                        else:
                            print(f"ERROR :: Failed to install sensor - {error_message or '[no error output]'}")

                    else:
                        print("INFO :: Failed to identify platform")
        else:
            print("INFO :: No instances with SSM access found")

def lambda_handler(event, context):
    SensorInstaller().main()

if __name__ == "__main__":
    os.environ["DEBUG"] = "True"
    os.environ['S3_BUCKET_NAME'] = 'cybereason-resources-9ji7oj16'
    lambda_handler(None, None)