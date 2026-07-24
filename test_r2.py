"""
R2 Connection Diagnostic Script
Tests the connection and lists all objects in the bucket.
"""
import os
import sys
from pathlib import Path

# Load env
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / "backend" / ".env")

account_id   = os.environ.get("R2_ACCOUNT_ID", "")
access_key   = os.environ.get("R2_ACCESS_KEY_ID", "")
secret_key   = os.environ.get("R2_SECRET_ACCESS_KEY", "")
bucket_name  = os.environ.get("R2_BUCKET_NAME", "")

print(f"Account ID  : {account_id[:8]}..." if account_id else "Account ID  : NOT SET")
print(f"Access Key  : {access_key[:8]}..." if access_key else "Access Key  : NOT SET")
print(f"Secret Key  : {'SET (hidden)' if secret_key else 'NOT SET'}")
print(f"Bucket Name : {bucket_name}")
print(f"Endpoint    : https://{account_id}.r2.cloudflarestorage.com")
print()

import boto3
from botocore.exceptions import ClientError, EndpointResolutionError, NoCredentialsError

try:
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )

    print("Testing bucket list...")
    response = client.list_objects_v2(Bucket=bucket_name)
    objects = response.get("Contents", [])

    if not objects:
        print("Bucket is EMPTY — no files have been uploaded yet.")
    else:
        print(f"Found {len(objects)} object(s) in bucket:")
        for obj in objects:
            print(f"  {obj['Key']}  ({obj['Size']} bytes)  Last modified: {obj['LastModified']}")

    print()
    print("R2 connection: OK")

except ClientError as e:
    code = e.response["Error"]["Code"]
    msg  = e.response["Error"]["Message"]
    print(f"R2 ClientError [{code}]: {msg}")
    if code == "NoSuchBucket":
        print("The bucket does not exist or name is wrong.")
    elif code in ("InvalidAccessKeyId", "SignatureDoesNotMatch"):
        print("Credentials are wrong.")
    elif code == "AccessDenied":
        print("Credentials valid but no permission to list objects.")
except Exception as e:
    print(f"Connection failed: {type(e).__name__}: {e}")
