import os
import gzip
import json
import boto3
from observe import Observe


def lambda_handler(event, context):
    bucket_name = event["Records"][0]["s3"]["bucket"]["name"]
    object_key = event["Records"][0]["s3"]["object"]["key"]
    file_path = f"/tmp/cur_log_file.json.gz"
    file_downloaded = False

    customer_id = os.getenv("CUSTOMER_ID")
    token = os.getenv("TOKEN")
    extra = os.getenv("EXTRA")
    shipper = Observe(customer_id=customer_id, token=token, extra=extra)

    try:
        s3_client = boto3.client("s3")
        response = s3_client.download_file(bucket_name, object_key, file_path)
        file_downloaded = True
    except Exception as e:
        print(e)

    if file_downloaded:
        with gzip.open(file_path, 'rt', encoding='utf-8') as f:
            file_content = json.load(f)
            if "Records" in file_content:
                try:
                    shipper.send_bulk(file_content["Records"])
                except Exception as e:
                    print(e)

