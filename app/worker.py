import hashlib
import logging
import uuid
from psycopg2 import sql
from sentence_transformers import SentenceTransformer
from rq import Worker, Queue, Connection

from app.config import settings
from app.database import get_db_connection, get_redis_client, init_db_pool
from app.chunker import chunk_financial_document

logger = logging.getLogger("financial_rag.worker")

# Global model instance for worker process
_embedding_model: SentenceTransformer | None = None

def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL_NAME}")
        _embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
    return _embedding_model


def compute_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def process_document_ingestion(
    doc_id: str,
    ticker_symbol: str,
    filename: str,
    content: str,
    allowed_roles: list[str]
) -> dict:
    """
    Asynchronous background document ingestion worker.
    
    1. Verifies SHA-256 content hash against document_registry (skip if unchanged).
    2. Determines active/staging index tables from index_aliases.
    3. Prepares staging table by copying existing active data.
    4. Chunks document with table-aware splitter.
    5. Generates 768-dim dense embeddings.
    6. Appends chunks into staging table.
    7. Atomically swaps live alias pointer to staging table (Blue-Green Swap).
    """
    logger.info(f"Starting document ingestion task for doc_id={doc_id}, ticker={ticker_symbol}")
    content_hash = compute_sha256(content)

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # 1. Content Hash Verification Gatekeeping
            cursor.execute(
                "SELECT content_hash, version FROM document_registry WHERE doc_id = %s;",
                (doc_id,)
            )
            existing_doc = cursor.fetchone()

            if existing_doc:
                existing_hash = existing_doc[0]
                existing_version = existing_doc[1]
                if existing_hash == content_hash:
                    logger.info(
                        f"Gatekeeper check: Document '{doc_id}' content hash '{content_hash[:10]}...' "
                        f"is identical to existing registry entry. Skipping ingestion."
                    )
                    return {
                        "status": "skipped",
                        "doc_id": doc_id,
                        "reason": "content_hash_unchanged",
                        "content_hash": content_hash
                    }
                new_version = existing_version + 1
            else:
                new_version = 1

            # 2. Determine Active and Staging Tables
            cursor.execute(
                "SELECT target_table FROM index_aliases WHERE alias_name = 'rag_index_live' FOR UPDATE;"
            )
            alias_row = cursor.fetchone()
            active_table = alias_row[0] if alias_row else "rag_index_a"
            staging_table = "rag_index_b" if active_table == "rag_index_a" else "rag_index_a"

            logger.info(f"Blue-Green Ingestion: Active='{active_table}', Staging='{staging_table}'")

            # 3. Truncate staging table and copy existing active index data (excluding target doc_id)
            cursor.execute(sql.SQL("TRUNCATE TABLE {staging};").format(staging=sql.Identifier(staging_table)))
            cursor.execute(
                sql.SQL("""
                INSERT INTO {staging} (chunk_id, doc_id, ticker_symbol, parent_section, content, chunk_type, embedding, allowed_roles)
                SELECT chunk_id, doc_id, ticker_symbol, parent_section, content, chunk_type, embedding, allowed_roles
                FROM {active}
                WHERE doc_id != %s;
                """).format(
                    staging=sql.Identifier(staging_table),
                    active=sql.Identifier(active_table)
                ),
                (doc_id,)
            )

            # 4. Table-Aware Text Parsing & Chunking
            chunks = chunk_financial_document(content)
            if not chunks:
                logger.warning(f"No chunks generated for document '{doc_id}'.")
                return {"status": "failed", "doc_id": doc_id, "reason": "empty_chunks"}

            # 5. Embedding Generation
            model = get_embedding_model()
            chunk_texts = [c["content"] for c in chunks]
            embeddings = model.encode(chunk_texts, show_progress_bar=False).tolist()

            # 6. Upsert Document Registry
            cursor.execute(
                """
                INSERT INTO document_registry (doc_id, ticker_symbol, filename, content_hash, version, updated_at)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (doc_id) DO UPDATE SET
                    ticker_symbol = EXCLUDED.ticker_symbol,
                    filename = EXCLUDED.filename,
                    content_hash = EXCLUDED.content_hash,
                    version = EXCLUDED.version,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (doc_id, ticker_symbol, filename, content_hash, new_version)
            )

            # 7. Insert Chunks into Staging Table
            for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                chunk_id = f"{doc_id}_c{idx}_{uuid.uuid4().hex[:8]}"
                cursor.execute(
                    sql.SQL("""
                    INSERT INTO {staging} 
                    (chunk_id, doc_id, ticker_symbol, parent_section, content, chunk_type, embedding, allowed_roles)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s);
                    """).format(staging=sql.Identifier(staging_table)),
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

            # 8. Atomic Blue-Green Alias Swap
            cursor.execute(
                """
                INSERT INTO index_aliases (alias_name, target_table, updated_at)
                VALUES ('rag_index_live', %s, CURRENT_TIMESTAMP)
                ON CONFLICT (alias_name) DO UPDATE SET
                    target_table = EXCLUDED.target_table,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (staging_table,)
            )

            cursor.execute(
                sql.SQL("CREATE OR REPLACE VIEW rag_index_live AS SELECT * FROM {staging};").format(
                    staging=sql.Identifier(staging_table)
                )
            )

            # Flush L1 Redis Query Cache on database update
            try:
                redis_client = get_redis_client()
                cache_keys = redis_client.keys("query:*")
                if cache_keys:
                    redis_client.delete(*cache_keys)
                    logger.info(f"Invalidated {len(cache_keys)} L1 Redis query cache entries after ingestion.")
            except Exception as cache_err:
                logger.warning(f"Failed to flush Redis query cache: {cache_err}")

            logger.info(
                f"Successfully completed blue-green swap! Live table is now '{staging_table}'. "
                f"Ingested {len(chunks)} chunks for document '{doc_id}' (v{new_version})."
            )

            return {
                "status": "finished",
                "doc_id": doc_id,
                "version": new_version,
                "chunks_ingested": len(chunks),
                "active_table": staging_table,
                "content_hash": content_hash
            }


if __name__ == "__main__":
    init_db_pool()
    redis_conn = get_redis_client()
    logger.info("Starting Redis Queue (RQ) Ingestion Worker...")
    with Connection(redis_conn):
        worker = Worker(['default'])
        worker.work()
