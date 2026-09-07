import logging
import os
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from app.config import settings

logger = logging.getLogger("financial_rag.s3")


def get_s3_client():
    """
    Constructs boto3 S3 client using environment configuration.
    Supports AWS S3 as well as local MinIO / LocalStack endpoints.
    """
    kwargs = {
        "region_name": settings.AWS_REGION
    }
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY

    if settings.S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL

    return boto3.client("s3", **kwargs)


def upload_document_to_s3(doc_id: str, filename: str, content_bytes: bytes, bucket_name: str | None = None) -> str:
    """
    Uploads document binary bytes to S3 bucket.
    Returns the S3 URI string: s3://<bucket>/documents/<doc_id>/<filename>
    """
    target_bucket = bucket_name or settings.AWS_S3_BUCKET
    object_key = f"documents/{doc_id}/{filename}"

    try:
        s3 = get_s3_client()
        s3.put_object(
            Bucket=target_bucket,
            Key=object_key,
            Body=content_bytes,
            Metadata={
                "doc_id": doc_id,
                "filename": filename
            }
        )
        s3_uri = f"s3://{target_bucket}/{object_key}"
        logger.info(f"Successfully uploaded document '{doc_id}' to S3: {s3_uri}")
        return s3_uri
    except (BotoCoreError, ClientError) as e:
        logger.warning(f"S3 upload failed for '{doc_id}', falling back to local disk storage: {e}")
        # Local fallback if AWS credentials or bucket unavailable
        storage_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "documents")
        os.makedirs(storage_dir, exist_ok=True)
        local_filename = f"{doc_id}_{filename}"
        local_file_path = os.path.join(storage_dir, local_filename)
        with open(local_file_path, "wb") as f:
            f.write(content_bytes)
        return local_file_path


def download_document_from_s3(s3_uri_or_path: str) -> str:
    """
    Downloads document content from S3 URI (s3://bucket/key) or reads local file path.
    """
    if s3_uri_or_path.startswith("s3://"):
        parts = s3_uri_or_path.replace("s3://", "").split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""

        try:
            s3 = get_s3_client()
            response = s3.get_object(Bucket=bucket, Key=key)
            content_bytes = response["Body"].read()
            return content_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            logger.error(f"Failed to download object from S3 '{s3_uri_or_path}': {e}")
            raise RuntimeError(f"Could not fetch document from S3 URI: {e}")
    else:
        # Local disk file fallback
        with open(s3_uri_or_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
