import os
from functools import lru_cache

BUCKET = "nabucite-pdf"


@lru_cache
def objects():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=os.getenv(
            "S3_ENDPOINT", f"http://127.0.0.1:{os.getenv('S3_PORT', '59000')}"
        ),
        aws_access_key_id="nabucite",
        aws_secret_access_key=os.environ["S3_PASSWORD"],
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 2},
        ),
    )


def put_document(key, file, mime):
    client = objects()
    try:
        client.create_bucket(Bucket=BUCKET)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    client.upload_fileobj(file, BUCKET, key, ExtraArgs={"ContentType": mime})
