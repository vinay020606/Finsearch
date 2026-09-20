import logging
import os
from contextlib import contextmanager
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from pgvector.psycopg2 import register_vector

from app.config import settings

logger = logging.getLogger("financial_rag.database")

# Global PostgreSQL Connection Pool
_db_pool: ThreadedConnectionPool | None = None

def init_db_pool():
    global _db_pool
    if _db_pool is None:
        try:
            logger.info("Initializing PostgreSQL Connection Pool...")
            _db_pool = ThreadedConnectionPool(
                minconn=settings.DB_POOL_MIN,
                maxconn=settings.DB_POOL_MAX,
                host=settings.POSTGRES_HOST,
                port=settings.POSTGRES_PORT,
                dbname=settings.POSTGRES_DB,
                user=settings.POSTGRES_USER,
                password=settings.POSTGRES_PASSWORD
            )
            logger.info("PostgreSQL Connection Pool initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL Connection Pool: {e}")
            raise e

def close_db_pool():
    global _db_pool
    if _db_pool is not None:
        logger.info("Closing PostgreSQL Connection Pool...")
        _db_pool.closeall()
        _db_pool = None

@contextmanager
def get_db_connection():
    """
    Context manager for acquiring DB connection from pool,
    registering pgvector adapter, and handling clean rollback/commit.
    """
    global _db_pool
    if _db_pool is None:
        init_db_pool()

    conn = _db_pool.getconn()
    try:
        register_vector(conn)
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error, rolled back transaction: {e}")
        raise e
    finally:
        _db_pool.putconn(conn)

def init_db():
    """
    Runs schema DDL script to initialize tables, extensions, and indexes.
    """
    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sql", "schema.sql")
    if not os.path.exists(schema_path):
        logger.warning(f"Schema file not found at {schema_path}")
        return

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(schema_sql)
            logger.info("Database schema initialized successfully.")


# In-memory / Redis Semantic Vector Cache Helper
_redis_client = None
_in_memory_vector_cache = []  # Fallback vector cache if Redis server is unavailable

def get_redis_client():
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", 6379))
            _redis_client = redis.Redis(host=redis_host, port=redis_port, db=0, socket_timeout=2)
            _redis_client.ping()
            logger.info("Redis client connected successfully.")
        except Exception as e:
            logger.warning(f"Redis connection unavailable ({e}), using in-memory semantic cache fallback.")
            _redis_client = False
    return _redis_client


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Calculates cosine similarity between two dense float vectors."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def get_semantic_cache(query_vector: list[float], threshold: float = 0.92) -> dict | None:
    """
    Searches Redis / In-Memory Semantic Vector Cache for previously computed query responses
    with Cosine Similarity >= threshold (default 0.92).
    Returns cached response payload if hit, else None.
    """
    try:
        r = get_redis_client()
        if r:
            import json
            cache_keys = r.keys("semantic_cache:*")
            for key in cache_keys:
                raw_val = r.get(key)
                if raw_val:
                    cached_obj = json.loads(raw_val.decode("utf-8"))
                    cached_vec = cached_obj.get("query_vector")
                    if cached_vec:
                        sim = _cosine_similarity(query_vector, cached_vec)
                        if sim >= threshold:
                            logger.info(f"[CACHE HIT] Semantic query cache matched key '{key}' (Similarity: {sim:.4f})")
                            return cached_obj.get("data")
        else:
            # Check in-memory fallback cache
            for cached_obj in _in_memory_vector_cache:
                sim = _cosine_similarity(query_vector, cached_obj["query_vector"])
                if sim >= threshold:
                    logger.info(f"[CACHE HIT] In-memory semantic query cache matched (Similarity: {sim:.4f})")
                    return cached_obj["data"]
    except Exception as e:
        logger.warning(f"Semantic cache retrieval error: {e}")
    return None


def set_semantic_cache(query_text: str, query_vector: list[float], response_data: dict | str, ttl_seconds: int = 3600):
    """
    Stores query vector and response payload into Redis / In-Memory Semantic Vector Cache.
    """
    try:
        cache_entry = {
            "query_text": query_text,
            "query_vector": query_vector,
            "data": response_data
        }
        r = get_redis_client()
        if r:
            import json
            import hashlib
            query_hash = hashlib.md5(query_text.encode("utf-8")).hexdigest()
            cache_key = f"semantic_cache:{query_hash}"
            r.setex(cache_key, ttl_seconds, json.dumps(cache_entry))
            logger.info(f"Saved entry to Redis semantic query cache (TTL {ttl_seconds}s): {cache_key}")
        else:
            _in_memory_vector_cache.append(cache_entry)
            if len(_in_memory_vector_cache) > 100:
                _in_memory_vector_cache.pop(0)
            logger.info(f"Saved entry to in-memory fallback semantic query cache.")
    except Exception as e:
        logger.warning(f"Failed to set semantic query cache: {e}")

