import hashlib
import io
import logging
import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sentence_transformers import SentenceTransformer
import pypdf


from app.config import settings
from app.database import (
    init_db_pool,
    close_db_pool,
    init_db,
    get_db_connection
)
from app.chunker import chunk_financial_document
from app.hybrid_search import execute_hybrid_rrf_search
from app.llm_synthesizer import generate_humanized_answer, stream_llm_answer, format_sse
from app.s3_utils import upload_document_to_s3
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
    description="FastAPI service with AWS S3 File Storage, Lambda SQS events, pgvector, RBAC metadata, Hybrid RRF Search, and LLM Answer Generation.",
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
    Accepts document data, saves raw file to AWS S3 storage (or local fallback), performs table-aware chunking & embedding, and stores in PostgreSQL.
    """
    try:
        content_hash = compute_sha256(payload.content)

        # 1. Upload Raw Document to AWS S3 Storage
        file_storage_path = upload_document_to_s3(
            doc_id=payload.doc_id,
            filename=payload.filename,
            content_bytes=payload.content.encode("utf-8")
        )

        logger.info(f"Saved raw document file to S3/Storage: {file_storage_path}")

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
                            file_path=file_storage_path,
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

                # 4. Vector Embedding Generation (BAAI/bge-base-en-v1.5 768-dim)
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
                    (payload.doc_id, payload.ticker_symbol, payload.filename, file_storage_path, content_hash, new_version)
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


def extract_text_from_file_bytes(file_bytes: bytes, filename: str) -> str:
    filename_lower = filename.lower()
    if filename_lower.endswith(".pdf"):
        try:
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            text_pages = []
            for idx, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    text_pages.append(f"--- Page {idx + 1} ---\n{page_text}")
            extracted = "\n\n".join(text_pages)
            if not extracted.strip():
                raise ValueError("PDF contains no readable text stream.")
            return extracted
        except Exception as e:
            logger.error(f"Failed to extract text from PDF {filename}: {e}")
            raise HTTPException(status_code=400, detail=f"Could not parse text from PDF file: {e}")
    else:
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_bytes.decode("latin-1", errors="replace")


@app.post(
    "/api/v1/documents/upload-file",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Document Ingestion"]
)
async def upload_document_file(
    file: UploadFile = File(...),
    doc_id: str = Form(...),
    ticker_symbol: str = Form(...),
    allowed_roles: str = Form("admin,analyst")
):
    """
    Accepts multipart/form-data file upload (PDF, TXT, MD, JSON, CSV), extracts content, and ingests into RAG pipeline.
    """
    try:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        extracted_content = extract_text_from_file_bytes(file_bytes, file.filename)
        roles = [r.strip() for r in allowed_roles.split(",") if r.strip()]

        payload = DocumentUploadRequest(
            doc_id=doc_id.strip(),
            ticker_symbol=ticker_symbol.strip(),
            filename=file.filename,
            content=extracted_content,
            allowed_roles=roles
        )
        return upload_document(payload)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to process uploaded file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Multipart file upload failed: {str(e)}"
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


async def _pipeline_sse_generator(payload: GenerateAnswerRequest):
    """
    Async generator producing step-by-step SSE status events followed by streamed LLM tokens.
    """
    # Step 1: Query Embedding
    yield format_sse("status", {"step": 1, "message": "Computing 768-dim query vector (BAAI/bge-base-en-v1.5)..."})
    await asyncio.sleep(0.1)

    try:
        model = get_embedding_model()
        query_vector = model.encode(payload.query, show_progress_bar=False).tolist()
    except Exception as emb_err:
        yield format_sse("error", f"Failed to compute embedding: {emb_err}")
        return

    # Step 2: Stage 1 & 2 Retrieval
    yield format_sse("status", {"step": 2, "message": "Executing Stage 1 Hybrid SQL RRF Search (Vector + BM25 tsvector)..."})
    await asyncio.sleep(0.1)

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
        yield format_sse("error", f"Database search failed: {db_err}")
        return

    # Step 3: Cross-Encoder Reranking Status
    yield format_sse("status", {"step": 3, "message": f"Stage 2 Cross-Encoder reranked top {len(raw_results)} relevant disclosures."})
    await asyncio.sleep(0.1)

    # Step 4: Prompt Context Template
    yield format_sse("status", {"step": 4, "message": "Populating context prompt template from llmprompt.txt..."})
    await asyncio.sleep(0.1)

    # Emit retrieved sources to UI
    sources_data = [
        {
            "chunk_id": r["chunk_id"],
            "doc_id": r["doc_id"],
            "ticker_symbol": r["ticker_symbol"],
            "parent_section": r["parent_section"],
            "chunk_type": r["chunk_type"],
            "allowed_roles": r["allowed_roles"],
            "rrf_score": float(r["rrf_score"]),
            "rerank_score": float(r["rerank_score"]),
            "content": r["content"]
        }
        for r in raw_results
    ]
    yield format_sse("sources", sources_data)

    # Step 5: Streaming Synthesis
    yield format_sse("status", {"step": 5, "message": "Synthesizing financial analysis..."})
    await asyncio.sleep(0.1)

    # Stream tokens from synthesizer
    async for sse_chunk in stream_llm_answer(payload.query, raw_results):
        yield sse_chunk


@app.post(
    "/api/v1/generate-stream",
    tags=["LLM Answer Synthesis"]
)
def generate_answer_stream(payload: GenerateAnswerRequest):
    """
    Retrieves financial context, populates llmprompt.txt context prompt,
    and streams pipeline SSE status events and LLM tokens in real time.
    """
    logger.info(f"Streaming LLM Answer with SSE events for query='{payload.query}' (Roles={payload.user_roles})")
    return StreamingResponse(
        _pipeline_sse_generator(payload),
        media_type="text/event-stream"
    )


