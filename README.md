# Production-Grade Financial Document Search & Indexing Engine

An asynchronous, production-ready financial document search engine and indexing pipeline built with **Python**, **FastAPI**, **PostgreSQL (pgvector)**, and **Redis Queue (RQ)**.

---

## ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             CLIENT APPLICATIONS                             │
└──────────────────────┬───────────────────────────────┬──────────────────────┘
                       │ Upload Document               │ Search Query
                       ▼                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           FASTAPI API GATEWAY                               │
│   • Validates incoming payloads (RBAC, file structures, query parameters)   │
│   • L1 Query Cache: Checks Redis before touching the database               │
└──────────────────────┬───────────────────────────────┬──────────────────────┘
                       │ Enqueue Job                   │ Cache Miss -> Read Alias
                       ▼                               ▼
┌────────────────────────────────┐         ┌──────────────────────────────────┐
│    REDIS QUEUE (BG WORKER)     │         │       POSTGRESQL + PGVECTOR      │
│  1. Content Hash Verification  │         │                                  │
│  2. Table-Aware Text Parsing   │         │  ┌────────────────────────────┐  │
│  3. Vector Embedding Generation│         │  │     index_aliases          │  │
│  4. Staging Table Ingestion    │         │  │   'rag_index_live' ────────┼─┐│
│  5. Blue-Green Alias Swap      │         │  └────────────────────────────┘ ││
└────────────────────────────────┘         │                                 ││
                                           │  ┌──────────────┐ ┌───────────┐ ││
                                           │  │ rag_index_a  │ │rag_index_b│ ││
                                           │  │ (Active)     │ │(Staging)  │ ││
                                           │  └──────┬───────┘ └───────────┘ ││
                                           │         │                       ││
                                           │         └──◄────────────────────┘│
                                           │  (Hybrid Vector + FTS RRF Search)│
                                           └──────────────────────────────────┘
```

---

## CORE BACKEND SOFTWARE ENGINEERING PRINCIPLES

### 1. Zero-Downtime Blue-Green Database Swaps
- Maintains twin indexing tables (`rag_index_a` and `rag_index_b`).
- An `index_aliases` registry table points the live pointer `rag_index_live` to the active table.
- Background workers build new indices on the staging table without locking read operations.
- Once ingestion and vector embedding complete, an atomic pointer swap switches traffic instantaneously to the staging table.

### 2. Hybrid Search Engine with Reciprocal Rank Fusion (RRF)
- Combines **Dense Vector Cosine Similarity Search** (via `pgvector` HNSW indexes) and **PostgreSQL Full-Text Search (FTS)** (via GIN indexes on `to_tsvector`).
- Merges vector and text keyword search ranks using **RRF** ($k=60$):
  $$RRF\_Score = \frac{1}{60 + Rank_{vector}} + \frac{1}{60 + Rank_{text}}$$

### 3. Table-Aware Financial Chunker
- Preserves HTML and Markdown tabular data (`| ... |` and `<table>`) as atomic, unbroken table chunks (`chunk_type="table"`).
- Splits prose text by semantic headers (`#`, `##`, `Item 1A`, etc.) while tracking hierarchy context (`parent_section`).

### 4. Content-Hash Gatekeeping
- Computes SHA-256 content hashes of incoming financial reports.
- If a document with identical SHA-256 hash exists in `document_registry`, re-embedding is skipped automatically to avoid expensive LLM/embedding compute costs.

### 5. Two-Tier Caching & Role-Based Access Control (RBAC)
- **L1 Exact-Match Redis Cache**: Caches serialized search JSON responses for fast retrieval (TTL: 600s). Invalidated automatically on database blue-green swaps.
- **RBAC**: Database array overlapping query operator (`allowed_roles && user_roles::text[]`) filters chunks strictly based on user authorizations.

---

## REPOSITORY STRUCTURE

```
financial-rag-backend/
├── app/
│   ├── __init__.py
│   ├── main.py                # FastAPI Application & REST Endpoints
│   ├── config.py              # System Configuration & Pydantic BaseSettings
│   ├── database.py            # PostgreSQL Connection Pool & Redis Client
│   ├── chunker.py             # Table-Aware Financial Text Splitter
│   ├── hybrid_search.py       # SQL Queries for Hybrid RRF Search
│   ├── worker.py              # Redis Queue (RQ) Background Ingestion Worker
│   └── models.py              # Pydantic Schemas for Requests & Responses
├── sql/
│   └── schema.sql             # PostgreSQL DDL (Tables, HNSW Indexes, FTS GIN Indexes)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## QUICK START GUIDE (DOCKER COMPOSE)

### 1. Launch Services
Run all services (Postgres with `pgvector`, Redis, FastAPI app, and RQ worker):

```bash
docker-compose up --build -d
```

### 2. Verify Health Status
Check container health and live table alias:

```bash
curl http://localhost:8000/health
```

Expected output:
```json
{
  "status": "ok",
  "database": "healthy",
  "redis": "healthy",
  "active_index_alias": "rag_index_a"
}
```

---

## API REFERENCE & USAGE EXAMPLES

### 1. Upload Financial Document (Asynchronous Ingestion)
`POST /api/v1/documents/upload`

Enqueues document ingestion in background Redis Queue worker.

```bash
curl -X POST "http://localhost:8000/api/v1/documents/upload" \
     -H "Content-Type: application/json" \
     -d '{
       "doc_id": "DOC-AAPL-10K-2024",
       "ticker_symbol": "AAPL",
       "filename": "aapl_2024_10k.md",
       "content": "# Item 1. Business\nApple Inc. designs, manufactures, and markets smartphones, personal computers, tablets, wearables, and accessories.\n\n| Segment | 2024 Revenue ($M) |\n|---|---|\n| iPhone | 201183 |\n| Services | 96169 |\n| Wearables & Home | 37005 |",
       "allowed_roles": ["analyst", "admin"]
     }'
```

Response (`202 Accepted`):
```json
{
  "job_id": "c1f7b82e-9d22-481e-84b2-04e3abf105e1",
  "status": "queued",
  "message": "Document '\''DOC-AAPL-10K-2024'\'' enqueued successfully for background processing."
}
```

### 2. Check Ingestion Task Status
`GET /api/v1/jobs/{job_id}`

```bash
curl http://localhost:8000/api/v1/jobs/c1f7b82e-9d22-481e-84b2-04e3abf105e1
```

Response when finished:
```json
{
  "job_id": "c1f7b82e-9d22-481e-84b2-04e3abf105e1",
  "status": "finished",
  "created_at": "2026-08-05T17:55:00Z",
  "ended_at": "2026-08-05T17:55:02Z",
  "result": {
    "status": "finished",
    "doc_id": "DOC-AAPL-10K-2024",
    "version": 1,
    "chunks_ingested": 2,
    "active_table": "rag_index_b",
    "content_hash": "a1b2c3..."
  }
}
```

### 3. Hybrid RRF Search
`POST /api/v1/search`

Executes Hybrid Vector + Full-Text Search with Reciprocal Rank Fusion.

```bash
curl -X POST "http://localhost:8000/api/v1/search" \
     -H "Content-Type: application/json" \
     -d '{
       "query": "What was Apple revenue for iPhone segment in 2024?",
       "user_roles": ["analyst"],
       "top_k": 5
     }'
```

Response:
```json
{
  "query": "What was Apple revenue for iPhone segment in 2024?",
  "cached": false,
  "total_results": 1,
  "results": [
    {
      "chunk_id": "DOC-AAPL-10K-2024_c1_a9b8c7d6",
      "doc_id": "DOC-AAPL-10K-2024",
      "ticker_symbol": "AAPL",
      "parent_section": "Item 1. Business",
      "content": "| Segment | 2024 Revenue ($M) |\n|---|---|\n| iPhone | 201183 |\n| Services | 96169 |\n| Wearables & Home | 37005 |",
      "chunk_type": "table",
      "allowed_roles": ["analyst", "admin"],
      "rrf_score": 0.03278688524590164
    }
  ]
}
```

---

## LOCAL DEVELOPMENT & TESTING

### 1. Setup Virtual Environment
```bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Run Database & Redis via Docker
```bash
docker-compose up -d postgres redis
```

### 3. Run RQ Worker locally
```bash
python -m app.worker
```

### 4. Run API Server locally
```bash
uvicorn app.main:app --reload --port 8000
```
