import hashlib
import logging
import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sentence_transformers import SentenceTransformer

from app.config import settings
from app.database import (
    init_db_pool,
    close_db_pool,
    init_db,
    get_db_connection
)
from app.chunker import chunk_financial_document
from app.hybrid_search import execute_hybrid_rrf_search
from app.llm_synthesizer import generate_humanized_answer
from app.models import (
    DocumentUploadRequest,
    DocumentUploadResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    GenerateAnswerRequest,
    GenerateAnswerResponse
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("financial_rag.api")

# Global Embedding Model Instance
_embedding_model: SentenceTransformer | None = None

def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL_NAME}")
        _embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
    return _embedding_model


def compute_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan Context Manager: Initialize Database, Storage Directory, and Connection Pool.
    """
    logger.info("Initializing Financial RAG Backend API...")
    storage_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "documents")
    os.makedirs(storage_dir, exist_ok=True)

    try:
        init_db_pool()
        init_db()
        logger.info("Database pool and local storage directory initialized successfully.")
    except Exception as e:
        logger.warning(f"Database initialization deferred or failed: {e}")
    yield
    logger.info("Shutting down API service...")
    close_db_pool()


app = FastAPI(
    title="Financial Document Search Engine (RAG Pipeline)",
    description="FastAPI service with Local File Storage, pgvector, RBAC metadata, Hybrid RRF Search, and LLM Answer Generation.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static frontend UI
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", include_in_schema=False)
def serve_index():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Financial RAG Backend API is running."}


@app.get("/health", tags=["System"])
def health_check():
    """
    System Health Check Endpoint.
    """
    db_status = "healthy"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM financial_chunks;")
    except Exception as e:
        db_status = f"unhealthy: {e}"

    return {
        "status": "ok" if db_status == "healthy" else "degraded",
        "database": db_status
    }


@app.post(
    "/api/v1/documents/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Document Ingestion"]
)
def upload_document(payload: DocumentUploadRequest):
    """
    Accepts document data, saves raw file to local storage, performs table-aware chunking & embedding, and stores in PostgreSQL.
    """
    try:
        content_hash = compute_sha256(payload.content)

        # 1. Save Raw File to Local Disk Storage
        storage_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "documents")
        os.makedirs(storage_dir, exist_ok=True)
        local_filename = f"{payload.doc_id}_{payload.filename}"
        local_file_path = os.path.join(storage_dir, local_filename)

        with open(local_file_path, "w", encoding="utf-8") as f:
            f.write(payload.content)

        logger.info(f"Saved raw document file to local storage: {local_file_path}")

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 2. Content Hash Verification
                cursor.execute(
                    "SELECT content_hash, version FROM document_registry WHERE doc_id = %s;",
                    (payload.doc_id,)
                )
                existing_doc = cursor.fetchone()

                if existing_doc:
                    existing_hash = existing_doc[0]
                    existing_version = existing_doc[1]
                    if existing_hash == content_hash:
                        logger.info(f"Document '{payload.doc_id}' content hash unchanged. Skipping ingestion.")
                        return DocumentUploadResponse(
                            doc_id=payload.doc_id,
                            status="skipped",
                            file_path=local_file_path,
                            chunks_ingested=0,
                            message="Content hash unchanged. Skipping re-embedding."
                        )
                    new_version = existing_version + 1
                else:
                    new_version = 1

                # 3. Table-Aware Chunking
                chunks = chunk_financial_document(payload.content)
                if not chunks:
                    raise HTTPException(status_code=400, detail="No valid text or table chunks found in document.")

                # 4. Vector Embedding Generation
                model = get_embedding_model()
                chunk_texts = [c["content"] for c in chunks]
                embeddings = model.encode(chunk_texts, show_progress_bar=False).tolist()

                # 5. Upsert Document Registry
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
                    (payload.doc_id, payload.ticker_symbol, payload.filename, local_file_path, content_hash, new_version)
                )

                # 6. Delete existing chunks if updating document
                cursor.execute("DELETE FROM financial_chunks WHERE doc_id = %s;", (payload.doc_id,))

                # 7. Insert new chunks & embeddings with RBAC metadata
                for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                    chunk_id = f"{payload.doc_id}_c{idx}_{uuid.uuid4().hex[:6]}"
                    cursor.execute(
                        """
                        INSERT INTO financial_chunks 
                        (chunk_id, doc_id, ticker_symbol, parent_section, content, chunk_type, embedding, allowed_roles)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s);
                        """,
                        (
                            chunk_id,
                            payload.doc_id,
                            payload.ticker_symbol,
                            chunk["parent_section"],
                            chunk["content"],
                            chunk["chunk_type"],
                            str(emb),
                            payload.allowed_roles
                        )
                    )

                logger.info(f"Ingested {len(chunks)} chunks for document '{payload.doc_id}' with roles {payload.allowed_roles}.")

                return DocumentUploadResponse(
                    doc_id=payload.doc_id,
                    status="success",
                    file_path=local_file_path,
                    chunks_ingested=len(chunks),
                    message=f"Successfully ingested {len(chunks)} chunks into search database."
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to ingest document: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document ingestion failed: {str(e)}"
        )


@app.post(
    "/api/v1/search",
    response_model=SearchResponse,
    tags=["Hybrid Search Engine"]
)
def search_documents(payload: SearchRequest):
    """
    Hybrid Search Engine Endpoint.
    1. Runs Stage 1 Hybrid Vector + FTS RRF Search with RBAC metadata filtering.
    2. Runs Stage 2 Cross-Encoder Reranking for high relevance precision.
    """
    logger.info(f"Executing Hybrid RRF + Cross-Encoder Search for query='{payload.query}' (Roles={payload.user_roles})")

    try:
        model = get_embedding_model()
        query_vector = model.encode(payload.query, show_progress_bar=False).tolist()
    except Exception as emb_err:
        logger.error(f"Failed to generate query embedding: {emb_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute embedding vector for query."
        )

    try:
        with get_db_connection() as conn:
            raw_results = execute_hybrid_rrf_search(
                conn=conn,
                query_text=payload.query,
                query_vector=query_vector,
                user_roles=payload.user_roles,
                top_k=payload.top_k
            )
    except Exception as db_err:
        logger.error(f"Hybrid RRF Search database execution error: {db_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database search execution failed: {str(db_err)}"
        )

    results_items = [
        SearchResultItem(
            chunk_id=r["chunk_id"],
            doc_id=r["doc_id"],
            ticker_symbol=r["ticker_symbol"],
            parent_section=r["parent_section"],
            content=r["content"],
            chunk_type=r["chunk_type"],
            allowed_roles=r["allowed_roles"],
            rrf_score=float(r["rrf_score"]),
            rerank_score=float(r["rerank_score"])
        )
        for r in raw_results
    ]

    return SearchResponse(
        query=payload.query,
        total_results=len(results_items),
        results=results_items
    )


@app.post(
    "/api/v1/generate",
    response_model=GenerateAnswerResponse,
    tags=["LLM Answer Synthesis"]
)
def generate_answer(payload: GenerateAnswerRequest):
    """
    Executes Hybrid RRF + Cross-Encoder Reranking retrieval, formats context prompt, and generates a humanized LLM answer.
    """
    logger.info(f"Generating LLM Answer for query='{payload.query}' (Roles={payload.user_roles})")

    try:
        model = get_embedding_model()
        query_vector = model.encode(payload.query, show_progress_bar=False).tolist()
    except Exception as emb_err:
        logger.error(f"Failed to generate query embedding: {emb_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute embedding vector for query."
        )

    try:
        with get_db_connection() as conn:
            raw_results = execute_hybrid_rrf_search(
                conn=conn,
                query_text=payload.query,
                query_vector=query_vector,
                user_roles=payload.user_roles,
                top_k=payload.top_k
            )
    except Exception as db_err:
        logger.error(f"Hybrid RRF Search database execution error: {db_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database search execution failed: {str(db_err)}"
        )

    # Call LLM Synthesizer
    humanized_answer = generate_humanized_answer(payload.query, raw_results)

    results_items = [
        SearchResultItem(
            chunk_id=r["chunk_id"],
            doc_id=r["doc_id"],
            ticker_symbol=r["ticker_symbol"],
            parent_section=r["parent_section"],
            content=r["content"],
            chunk_type=r["chunk_type"],
            allowed_roles=r["allowed_roles"],
            rrf_score=float(r["rrf_score"]),
            rerank_score=float(r["rerank_score"])
        )
        for r in raw_results
    ]

    return GenerateAnswerResponse(
        query=payload.query,
        humanized_answer=humanized_answer,
        total_sources=len(results_items),
        sources=results_items
    )
