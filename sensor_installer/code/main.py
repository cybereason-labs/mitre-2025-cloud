import os
import json
import boto3
from time import time, sleep
from botocore.exceptions import ClientError


class SensorInstaller:
    def __init__(self):
        self.region = os.getenv('REGION', "us-east-1")
        self.ssm_client = boto3.client('ssm', region_name=self.region)
        self.ec2_client = boto3.client('ec2', region_name=self.region)
        self.s3_bucket_name = os.getenv("S3_BUCKET_NAME")
        self.timeout = int(os.getenv("RETRY_TIMEOUT", 600))
        self.interval = int(os.getenv("RETRY_WAIT_INTERVAL", 5))
        debug = os.getenv("DEBUG")
        self.debug = True if debug == "True" else False

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

    def send_ssm_command(self, instance_ids: list, commands: list, document_name: str, platform: str):
        try:
            resp = self.ssm_client.send_command(
                InstanceIds=instance_ids,
                DocumentName=document_name,
                Parameters={'commands': commands},
            )
            cmd_id = resp['Command']['CommandId']
            print(f"INFO :: Sent command {cmd_id!r} for all found {platform.title()} platform")
            return cmd_id
        except ClientError as e:
            print(f"ERROR :: Failed to send command - {e}")
            raise

    def wait_for_command(self, command_id, instance_ids):
        def upsert_instance(instances, upsert_instance_id, new_status, output):
            for inst in instances:
                if inst["instance_id"] == upsert_instance_id:
                    inst["status"] = new_status
                    return
            instances.append({"instance_id": upsert_instance_id, "status": new_status, "output_content": output})

        print(f"INFO :: Waiting for command '{command_id}' to finish...")
        finished = {i: False for i in instance_ids}
        results = []
        while not all(finished.values()):
            sleep(self.interval)
            for instance_id in instance_ids:
                if finished[instance_id]:
                    continue

                result = self.ssm_client.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id
                )

                status = result["Status"]
                output_content = result["StandardOutputContent"]
                if status in ["Success", "Failed", "Cancelled", "TimedOut"]:
                    finished[instance_id] = True
                    print(f"INFO :: Instance '{instance_id}' - {str(status)}")
                    if result.get("StandardErrorContent"):
                        print("    ERROR ::\n", result["StandardErrorContent"])
                upsert_instance(results, instance_id, status, output_content)
        return results

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
        current_instances = self.get_current_running_instances()
        if len(current_instances) > 0:
            current_windows_instance_ids = []
            current_windows_instance_tags = {}

            current_linux_instance_id = []
            current_linux_instance_tags = {}

            for instance in current_instances:
                instance_id = instance["instance_id"]
                tags = instance.get("Tags", [])
                ssm_access = self.get_tag_value("ssm_access", tags)
                sensor_installed = self.get_tag_value("sensor_installed", tags)
                isolated = self.get_tag_value("isolated", tags)
                platform = self.get_tag_value("platform_details", tags).lower().strip()

                if ssm_access == "True" and sensor_installed == "False" and platform == "windows" and isolated == "True":
                    current_windows_instance_ids.append(instance_id)
                    current_windows_instance_tags.update({instance_id: tags})
                elif ssm_access == "True" and sensor_installed == "False" and platform == "linux" and isolated == "True":
                    current_linux_instance_id.append(instance_id)
                    current_linux_instance_tags.update({instance_id: tags})

            if len(current_windows_instance_ids) > 0:
                document_name = "AWS-RunPowerShellScript"
                sensor_commands = [
                    "New-Item -Path \"C:\\tools\" -ItemType Directory",

                    # Installing AWS CLI Tool
                    "$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {'-arm64'} else {''}",
                    "$msiName = if ($env:AWSCLI_VERSION) { \"AWSCLIV2-$env:AWSCLI_VERSION$arch.msi\" } else { if ($arch) { \"AWSCLIV2$arch.msi\" } else { \"AWSCLIV2.msi\" } }",
                    "$url = \"https://awscli.amazonaws.com/$msiName\"",
                    "$msi = Join-Path $env:TEMP $msiName",
                    "Invoke-WebRequest -Uri $url -OutFile $msi",
                    "$log = Join-Path $env:TEMP 'AWSCLI-install.log'",
                    "Start-Process msiexec.exe -ArgumentList \"/i `\"$msi`\" /qn /norestart /log `\"$log`\"\" -Wait",
                    "& \"C:\\Program Files\\Amazon\\AWSCLIV2\\aws.exe\" --version",

                    # Installing Certificates
                    f"& \"C:\\Program Files\\Amazon\\AWSCLIV2\\aws.exe\" s3 cp s3://{self.s3_bucket_name}/DeveloperCertificates.zip C:\\tools\\",
                    "Expand-Archive -Path \"C:\\tools\\DeveloperCertificates.zip\" -DestinationPath \"C:\\tools\" -Force;",
                    "Start-Process -FilePath \"C:\\tools\\DeveloperCertificates\\InstallCaCert.bat\" -Wait -NoNewWindow;",
                    "Start-Process -FilePath \"C:\\tools\\DeveloperCertificates\\InstallCert.bat\" -Wait -NoNewWindow;",

                    # Installing Sensor
                    f"& \"C:\\Program Files\\Amazon\\AWSCLIV2\\aws.exe\" s3 cp s3://{self.s3_bucket_name}/CybereasonSensor64.exe C:\\tools\\;",
                    "Start-Process -FilePath \"C:\\tools\\CybereasonSensor64.exe\" -ArgumentList \"/install\",\"/quiet\",\"/norestart\" -Wait -NoNewWindow;",

                    # Installing DLLs
                    f"& \"C:\\Program Files\\Amazon\\AWSCLIV2\\aws.exe\" s3 cp s3://{self.s3_bucket_name}/dlls.zip C:\\tools\\;",
                    "Expand-Archive -Path \"C:\\tools\\dlls.zip\" -DestinationPath \"C:\\tools\" -Force;",
                    "Start-Process -FilePath \"C:\\tools\\mitredlls\\switch_dlls.bat\" -Wait -NoNewWindow;",
                    "Remove-Item -Path \"C:\\tools\" -Recurse -Force;",

                    # Verifying Installation
                    "Start-Sleep -Seconds 60",
                    "if ((Get-Service -Name \"CybereasonActiveProbe\" -ErrorAction SilentlyContinue).Status -eq 'Running') { exit 0 } else { exit 1 }"
                ]

                windows_start_time = time()
                command_id = self.send_ssm_command(current_windows_instance_ids, sensor_commands, document_name,
                                                   "windows")
                results = self.wait_for_command(command_id, current_windows_instance_ids)
                windows_end_time = time()
                elapsed_seconds = int(windows_end_time - windows_start_time)
                minutes, seconds = divmod(elapsed_seconds, 60)
                print(f"INFO :: Windows command ran for {minutes}:{seconds:02d}")

                for result in results:
                    cur_instance_id = result["instance_id"]
                    cur_status = result["status"]

                    if cur_status == "Success":
                        if self.debug:
                            print(f"WARNING :: Debug is ON - Not attempting to release '{cur_instance_id}' isolation")
                        else:
                            print(f"INFO :: Sensor successfully installed on '{cur_instance_id}'")
                            self.ec2_client.create_tags(
                                Resources=[cur_instance_id],
                                Tags=[
                                    {'Key': "sensor_installed", 'Value': "True"},
                                    {'Key': "last_edited_by", 'Value': "SensorInstallation"}
                                ]
                            )
                            tags = current_windows_instance_tags[cur_instance_id]
                            security_groups = self.get_tag_value("security_groups", tags)
                            instance_profile = self.get_tag_value("previous_instance_profile", tags)
                            released_isolation = self.release_isolation(cur_instance_id, security_groups,
                                                                        instance_profile)

                            if released_isolation:
                                self.ec2_client.create_tags(
                                    Resources=[cur_instance_id],
                                    Tags=[
                                        {'Key': "security_groups", 'Value': "None"},
                                        {'Key': "previous_instance_profile", 'Value': "None"},
                                        {'Key': "ssm_access", 'Value': "N\\A"},
                                        {'Key': "isolated", 'Value': "False"},
                                        {'Key': "last_edited_by", 'Value': "SensorInstallation"}
                                    ]
                                )
                            else:
                                print(f"ERROR :: Failed to release '{cur_instance_id}' from isolation")
                                exit(1)
                    else:
                        print(f"INFO :: Sensor failed to install on '{cur_instance_id}'")

            else:
                print("INFO :: No Windows instances with SSM access found")
            if len(current_linux_instance_id) > 0:
                pass
            else:
                print("INFO :: No Linux instances with SSM access found")


def lambda_handler(event, context):
    SensorInstaller().main()


# For testing -->
#
# if __name__ == "__main__":
#     # os.environ["DEBUG"] = "True"
#     os.environ['S3_BUCKET_NAME'] = "bucket-name"
#     lambda_handler(None, None)
