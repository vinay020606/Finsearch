import hashlib
import json
import logging
import time
import uuid
import boto3
from sentence_transformers import SentenceTransformer

from app.config import settings
from app.database import (
    get_db_connection,
    init_db_pool,
    get_redis_client
)
from app.chunker import chunk_financial_document
from app.s3_utils import download_document_from_s3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("financial_rag.sqs_ingestor")

# Global Embedding Model Instance
_embedding_model: SentenceTransformer | None = None

def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Loading embedding model in SQS ingestor worker: {settings.EMBEDDING_MODEL_NAME}")
        _embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
    return _embedding_model


def compute_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def process_sqs_reindex_message(message_body: str) -> bool:
    """
    Processes a single document re-indexing message from SQS.
    1. Downloads updated file from S3 (or local path).
    2. Computes content hash to verify changes.
    3. Re-chunks document with table-aware splitter.
    4. Generates 768-dim BAAI/bge-base-en-v1.5 dense embeddings.
    5. Atomically re-indexes PostgreSQL database (updating HNSW vector index & BM25 tsvector GIN index).
    6. Flushes L1 Redis query cache.
    """
    try:
        data = json.loads(message_body)
        doc_id = data.get("doc_id")
        s3_uri = data.get("s3_uri") or f"s3://{data.get('s3_bucket')}/{data.get('s3_key')}"
        ticker_symbol = data.get("ticker_symbol", "DOC")
        filename = data.get("filename", f"{doc_id}.txt")
        allowed_roles = data.get("allowed_roles", ["admin", "analyst"])

        logger.info(f"Processing SQS re-indexing job for doc_id='{doc_id}' (URI: {s3_uri})")

        # 1. Fetch document content from S3
        content = download_document_from_s3(s3_uri)
        if not content or not content.strip():
            logger.warning(f"Downloaded empty document content for '{doc_id}'. Skipping.")
            return True

        content_hash = compute_sha256(content)

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 2. Check existing registry entry & versioning
                cursor.execute(
                    "SELECT content_hash, version FROM document_registry WHERE doc_id = %s;",
                    (doc_id,)
                )
                existing_doc = cursor.fetchone()

                if existing_doc:
                    existing_hash, existing_version = existing_doc[0], existing_doc[1]
                    if existing_hash == content_hash:
                        logger.info(f"Content hash for '{doc_id}' unchanged. Re-indexing skipped.")
                        return True
                    new_version = existing_version + 1
                else:
                    new_version = 1

                # 3. Table-Aware Financial Chunking
                chunks = chunk_financial_document(content)
                if not chunks:
                    logger.warning(f"No text/table chunks generated for '{doc_id}'.")
                    return True

                # 4. Generate 768-dim Dense Vectors
                model = get_embedding_model()
                chunk_texts = [c["content"] for c in chunks]
                embeddings = model.encode(chunk_texts, show_progress_bar=False).tolist()

                # 5. Upsert Document Registry with S3 URI
                cursor.execute(
                    """
                    INSERT INTO document_registry (doc_id, ticker_symbol, filename, file_path, content_hash, version, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (doc_id) DO UPDATE SET
                        ticker_symbol = EXCLUDED.ticker_symbol,
                        filename = EXCLUDED.filename,
                        file_path = EXCLUDED.file_path,
                        content_hash = EXCLUDED.content_hash,
                        version = EXCLUDED.version,
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    (doc_id, ticker_symbol, filename, s3_uri, content_hash, new_version)
                )

                # 6. Delete old chunks for doc_id
                cursor.execute("DELETE FROM financial_chunks WHERE doc_id = %s;", (doc_id,))

                # 7. Insert updated chunks into PostgreSQL (HNSW vector + tsvector BM25)
                for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                    chunk_id = f"{doc_id}_c{idx}_{uuid.uuid4().hex[:6]}"
                    cursor.execute(
                        """
                        INSERT INTO financial_chunks 
                        (chunk_id, doc_id, ticker_symbol, parent_section, content, chunk_type, embedding, allowed_roles)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s);
                        """,
                        (
                            chunk_id,
                            doc_id,
                            ticker_symbol,
                            chunk["parent_section"],
                            chunk["content"],
                            chunk["chunk_type"],
                            str(emb),
                            allowed_roles
                        )
                    )

                logger.info(f"Successfully re-indexed {len(chunks)} chunks for document '{doc_id}' (v{new_version}).")

                # Flush L1 Redis Query Cache
                try:
                    redis_client = get_redis_client()
                    cache_keys = redis_client.keys("query:*")
                    if cache_keys:
                        redis_client.delete(*cache_keys)
                        logger.info(f"Invalidated {len(cache_keys)} Redis query cache entries after SQS re-indexing.")
                except Exception as cache_err:
                    logger.warning(f"Redis cache flush warning: {cache_err}")

                return True

    except Exception as e:
        logger.error(f"Failed to process SQS re-indexing message: {e}")
        return False


def start_sqs_ingestor_worker():
    """
    Main SQS Worker Daemon Loop.
    Polls AWS SQS queue for S3 re-indexing event messages and executes re-indexing.
    """
    init_db_pool()
    queue_url = settings.AWS_SQS_QUEUE_URL

    kwargs = {"region_name": settings.AWS_REGION}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY

    sqs = boto3.client("sqs", **kwargs)
    logger.info(f"Starting SQS Ingestor Consumer Worker... Listening on Queue: {queue_url}")

    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=5,
                WaitTimeSeconds=10,
                MessageAttributeNames=['All']
            )

            messages = response.get('Messages', [])
            if not messages:
                time.sleep(1)
                continue

            for msg in messages:
                receipt_handle = msg['ReceiptHandle']
                body = msg['Body']

                success = process_sqs_reindex_message(body)
                if success:
                    sqs.delete_message(
                        QueueUrl=queue_url,
                        ReceiptHandle=receipt_handle
                    )
                    logger.info(f"Processed & deleted SQS message: {msg.get('MessageId')}")

        except KeyboardInterrupt:
            logger.info("Stopping SQS Ingestor Worker...")
            break
        except Exception as e:
            logger.warning(f"SQS Worker polling loop error (waiting 5s): {e}")
            time.sleep(5)


if __name__ == "__main__":
    start_sqs_ingestor_worker()
