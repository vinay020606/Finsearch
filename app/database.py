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
