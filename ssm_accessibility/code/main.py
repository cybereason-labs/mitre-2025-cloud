import json
import os
import boto3


class SsmAccessibility:
    def __init__(self):
        self.region = os.getenv('AWS_REGION', "us-east-1")
        self.ssm_client = boto3.client('ssm', region_name=self.region)
        self.ec2_client = boto3.client('ec2', region_name=self.region)
        self.parameter_name = os.getenv("PARAMETER_NAME")

    def get_ssm_instances(self):
        print("INFO :: Fetching all available instances visible in SSM")
        response = self.ssm_client.describe_instance_information()
        instance_ids = []
        if "InstanceInformationList" in response and len(response["InstanceInformationList"]) > 0:
            instance_ids = [instance["InstanceId"] for instance in response["InstanceInformationList"]]
        return instance_ids

    def get_current_instance_tags(self):
        pass

    def get_current_instances(self):
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

    @staticmethod
    def get_tag_value(tag_name, tags):
        for tag in tags:
            if tag["Key"] == tag_name:
                return tag["Value"]

    def main(self):
        current_instance_tags = self.get_current_instances()
        instances_visible_by_ssm = self.get_ssm_instances()

        for instance in current_instance_tags:
            instance_id = instance["instance_id"]
            ssm_access = self.get_tag_value("ssm_access", instance["Tags"])
            last_edited_by = self.get_tag_value("last_edited_by", instance["Tags"])

            if last_edited_by == "InstancePipeline" and instance_id in instances_visible_by_ssm and ssm_access == "False":
                print(f"INFO :: Instance '{instance_id}' is accessible by SSM but not in the remote parameter - updating tag")
                self.ec2_client.create_tags(
                    Resources=[instance_id],
                    Tags=[
                        {'Key': "ssm_access", 'Value': "True"},
                        {'Key': "last_edited_by", 'Value': "SsmAccessibility"}
                    ]
                )
            else:
                print(f"INFO :: No changes required for instance '{instance_id}'")


def lambda_handler(event, context):
    SsmAccessibility().main()
