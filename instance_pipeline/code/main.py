import os
import time
import json
import boto3
from botocore.exceptions import ClientError


class InstancePipeline:
    def __init__(self, event, region, instance_id):
        self.instance_id = instance_id
        self.event = event
        self.iam_client = boto3.client('iam')
        self.ec2_client = boto3.client('ec2', region_name=region)
        self.ssm_policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
        self.approved_images = os.getenv("APPROVED_IMAGES", "")
        self.s3_bucket_name = os.getenv("S3_BUCKET_NAME")

    def check_approved_ami(self, current_instance):
        for instance in current_instance:
            cur_image_id = instance["ImageId"]
            approved_images = [image.strip() for image in self.approved_images.split(",")]
            if cur_image_id not in approved_images:
                self.ec2_client.stop_instances(InstanceIds=[self.instance_id], Force=True)
                return False
            else:
                print(f" INFO   :: Instance '{self.instance_id}' is using an approved image")
                return True

    def isolating_instance_role_permissions(self):
        associations = self.ec2_client.describe_iam_instance_profile_associations(
            Filters=[{'Name': 'instance-id', 'Values': [self.instance_id]}]
        )
        if "IamInstanceProfileAssociations" in associations and len(associations["IamInstanceProfileAssociations"]) > 0:
            for association in associations['IamInstanceProfileAssociations']:
                association_id = association['AssociationId']
                self.ec2_client.disassociate_iam_instance_profile(AssociationId=association_id)
                print(f" INFO   :: Role was detached from instance '{self.instance_id}'")
                time.sleep(10)
        else:
            print(" INFO   :: Instance doesn't have a role attached")

    def revoke_security_group(self, security_group_name):
        describe_sg = self.ec2_client.describe_security_groups(GroupNames=[security_group_name])["SecurityGroups"][0]

        sg_id = describe_sg["GroupId"]
        existing_sg_ingress_permissions = describe_sg.get("IpPermissions", [])
        existing_sg_egress_permissions = describe_sg.get("IpPermissionsEgress", [])

        if len(existing_sg_ingress_permissions) > 0:
            print(f"WARNING :: Found existing ingress rules in '{security_group_name}'")
            self.ec2_client.revoke_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=existing_sg_ingress_permissions
            )
            print(f" INFO   :: SG ingress rules were revoked from instance '{self.instance_id}'")
        if len(existing_sg_egress_permissions) > 0:
            print(f"WARNING :: Found existing egress rules in '{security_group_name}'")
            self.ec2_client.revoke_security_group_egress(
                GroupId=sg_id,
                IpPermissions=existing_sg_egress_permissions
            )
            print(f" INFO   :: SG egress rules were revoked from instance '{self.instance_id}'")

        try:
            """
            Adding port 443 to maintain SSM connection
            """
            self.ec2_client.authorize_security_group_egress(
                GroupId=sg_id,
                IpPermissions=[{
                    'IpProtocol': "tcp",
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                    'FromPort': 443,
                    'ToPort': 443,
                }]
            )
            print(" INFO   :: Added egress to port 443 for SSM")
        except ClientError as e:
            if "InvalidPermission.Duplicate" in str(e):
                print(f"WARNING :: Security group egress rule for 443 already exists")

    def isolating_instance_network_access(self, vpc_id):
        print(f" INFO   :: Isolating '{self.instance_id}' for network access")
        expected_security_group_name = f"Isolated SG - {self.instance_id}"

        try:
            ### Creating a new isolated security group to replace the existing one
            new_isolated_sg_id = self.ec2_client.create_security_group(
                Description=f"Temp Isolated Security Group for {self.instance_id}",
                GroupName=expected_security_group_name,
                VpcId=vpc_id)["GroupId"]
            time.sleep(5)
            self.revoke_security_group(expected_security_group_name)
            print(" INFO   :: New isolated Security Group was created")

            self.ec2_client.modify_instance_attribute(InstanceId=self.instance_id, Groups=[new_isolated_sg_id])
            print(f" INFO   :: Isolated Security Group was attached to '{self.instance_id}'")

        except ClientError as e:
            if "InvalidGroup.Duplicate" in str(e):
                print(f"WARNING :: The security group '{expected_security_group_name}' already exist")
                describe_sg = self.ec2_client.describe_security_groups(GroupNames=[expected_security_group_name])["SecurityGroups"][0]
                sg_id = describe_sg["GroupId"]
                self.ec2_client.modify_instance_attribute(InstanceId=self.instance_id, Groups=[sg_id])
                print(f" INFO   :: Existing isolated Security Group was attached to '{self.instance_id}'")

    def tag_instance(self, tags, current_tags):
        existing_tags_keys = [tag["Key"] for tag in current_tags]
        for tag in tags:
            if tag["Key"] not in existing_tags_keys:
                try:
                    self.ec2_client.create_tags(
                        Resources=[self.instance_id],
                        Tags=[{'Key': tag["Key"], 'Value': str(tag["Value"])}]
                    )
                    print(f" INFO   :: Updated the '{tag["Key"]}' tag for instance '{self.instance_id}' have been updated")
                except ClientError as e:
                    print(f"ERROR :: Failed creating tags for instance '{self.instance_id}' - {e}")

    @staticmethod
    def instance_existing_tags(current_tags):
        existing_tags_keys = [tag["Key"] for tag in current_tags]
        isolation_status = False
        sensor_installed = False
        if "isolation_status" and "sensor_installed" in existing_tags_keys:
            for tag in current_tags:
                if tag["Key"] in "isolated":
                    isolation_status = False if tag["Value"] == "False" else True
                elif tag["Key"] in "sensor_installed":
                    sensor_installed = False if tag["Value"] == "False" else True

        return isolation_status, sensor_installed

    def temporary_isolate_instance(self, current_instance):
        for instance in current_instance:
            current_tags = instance.get('Tags', [])
            security_groups = instance.get("SecurityGroups", "None")
            vpc_id = instance["VpcId"]
            new_tags = [
                {"Key": "last_edited_by", "Value": "InstancePipeline"},
                {"Key": "architecture", "Value": instance["Architecture"]},
                {"Key": "platform_details", "Value": instance["PlatformDetails"]},
                {"Key": "previous_instance_profile", "Value": instance.get("IamInstanceProfile", "None")},
                {"Key": "security_groups", "Value": security_groups},
                {"Key": "sensor_installed", "Value": "False"},
                {"Key": "ssm_access", "Value": "False"},
                {"Key": "isolated", "Value": "True"}
            ]

            isolated, sensor_installed = self.instance_existing_tags(current_tags)

            if not isolated and not sensor_installed:
                self.isolating_instance_role_permissions()
                self.isolating_instance_network_access(vpc_id)
                self.tag_instance(new_tags, current_tags)

            elif not isolated and sensor_installed:
                print(f" INFO   :: Instance '{self.instance_id}' have a sensor - skipping")
                exit(0)
            else:
                print(f" INFO   :: Instance '{self.instance_id}' is already isolated - skipping")
                exit(0)

    def create_role(self):
        cur_role_name = f'{self.instance_id}-default-role'

        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }
        try:
            self.iam_client.create_role(
                AssumeRolePolicyDocument=json.dumps(assume_role_policy),
                Path='/',
                RoleName=cur_role_name
            )
            print(" INFO   :: Created a new role")

            try:
                self.iam_client.attach_role_policy(RoleName=cur_role_name, PolicyArn=self.ssm_policy_arn)
                print(" INFO   :: Added SSM policy to role")
            except ClientError as e:
                print(e)

            try:
                policy_document = {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "s3:GetObject",
                                "s3:HeadObject"
                            ],
                            "Resource": [
                                f"arn:aws:s3:::{self.s3_bucket_name}/*"
                            ]
                        }
                    ]
                }
                policy_name = "S3-Access"
                self.iam_client.put_role_policy(
                    RoleName=cur_role_name,
                    PolicyName=policy_name,
                    PolicyDocument=json.dumps(policy_document)
                )
                print(f" INFO   ::  Inline policy '{policy_name}' successfully embedded in role '{cur_role_name}'")
            except ClientError as e:
                print(e)

        except ClientError as e:
            if "EntityAlreadyExists" in str(e):
                print(f"WARNING :: Role '{cur_role_name}' already exists")

        return cur_role_name

    def add_required_permissions(self, current_instance):
        for instance in current_instance:
            if "IamInstanceProfile" in instance:
                print(f" INFO   :: Instance '{self.instance_id}' already have a role")

            else:
                print(" INFO   :: Instance is without a role")
                ssm_role_name = self.create_role()
                try:
                    self.iam_client.create_instance_profile(InstanceProfileName=ssm_role_name)
                except ClientError as e:
                    if "EntityAlreadyExists" in str(e):
                        print(f"WARNING :: Instance Profile '{ssm_role_name}' already exists")
                time.sleep(10)

                try:
                    self.iam_client.add_role_to_instance_profile(
                        InstanceProfileName=ssm_role_name,
                        RoleName=ssm_role_name
                    )
                    print(" INFO   :: Added role to instance profile")
                except ClientError as e:
                    if "InstanceSessionsPerInstanceProfile: 1" in str(e):
                        existing_instance_profile_name = self.iam_client.list_instance_profiles_for_role(RoleName=ssm_role_name)["InstanceProfiles"][0]["InstanceProfileName"]
                        if existing_instance_profile_name == ssm_role_name:
                            pass
                        else:
                            exit(9)
                time.sleep(10)

                self.ec2_client.associate_iam_instance_profile(
                    InstanceId=self.instance_id,
                    IamInstanceProfile={
                        'Name': ssm_role_name
                    }
                )
                print(f" INFO   :: Attached role to '{self.instance_id}' instance")

    def describe_instance(self):
        described_instance = self.ec2_client.describe_instances(InstanceIds=[self.instance_id])
        if "Reservations" in described_instance \
                and len(described_instance["Reservations"]) == 1 \
                and "Instances" in described_instance["Reservations"][0]:
            current_instance = described_instance["Reservations"][0]["Instances"]
            return current_instance

    def main(self):
        current_instance = self.describe_instance()

        # STEP 1 - Checking if the AMI was pre-approved and terminating in the case it wasn't
        ami_is_approved = self.check_approved_ami(current_instance)

        if ami_is_approved:
            # STEP 2 - Isolation to all new instances
            self.temporary_isolate_instance(current_instance)

            # STEP 3 (if not isolated) - add required SSM permissions
            refreshed_current_instance = self.describe_instance()
            self.add_required_permissions(refreshed_current_instance)
        else:
            print(f"WARNING :: Instance {self.instance_id} was using unapproved image and was stopped")

def lambda_handler(event, context):
    region = event['region']
    instance_id = event["detail"]['instance-id']
    print(f" INFO   :: Detected a new running instance - '{instance_id}'")
    InstancePipeline(event, region, instance_id).main()

# For testing -->
#
# if __name__ == "__main__":
#     os.environ["S3_BUCKET_NAME"] = "arn:aws:s3:::test"
#     os.environ["APPROVED_IMAGES"] = "ami-084a7d336e816906b, ami-07041441b708acbd6, ami-Oc9fb5d338f1eec43"
#     e = {
#         "region": "us-east-1",
#         "detail": {
#             "instance-id": "i-0d39f490d0c03aa50"
#         }
#     }
#     lambda_handler(e, None)