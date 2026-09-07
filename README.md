# FinSearch: Production-Grade Financial Document Search & Indexing Engine

An enterprise microservices-based financial document search, indexing, and synthesis engine featuring a **Spring Boot API Gateway (Java 17)**, **Python AI RAG Engine (FastAPI)**, **PostgreSQL (`pgvector` + GIN)**, **AWS S3 Document Storage**, **AWS Lambda + SQS Event-Driven Re-Indexing Pipeline**, **Table-Aware Financial Text Chunking**, **`BAAI/bge-base-en-v1.5` Vector Embeddings (768-dim)**, **Stage 1 Hybrid SQL RRF Search**, **Stage 2 Cross-Encoder Reranking**, **`llmprompt.txt` System Prompt Engine**, and **Server-Sent Events (SSE) Live Token Streaming UI**.

---

## 🏗️ SYSTEM ARCHITECTURE & SERVICE INTERACTION

The architecture is divided into two decoupled data pipelines:
1. **Asynchronous Ingestion & Re-Indexing Write Path** (S3 $\rightarrow$ Lambda $\rightarrow$ SQS $\rightarrow$ Ingestor Worker $\rightarrow$ PostgreSQL)
2. **Synchronous & SSE Streaming Search/Synthesis Read Path** (Client $\rightarrow$ Spring Gateway $\rightarrow$ FastAPI Engine $\rightarrow$ Hybrid RRF + Reranker $\rightarrow$ LLM Stream)

### Architecture Diagram

```
                                  =================================================
                                  1. ASYNCHRONOUS INGESTION & RE-INDEXING WRITE PATH
                                  =================================================

┌───────────────────────┐  1. Upload Document   ┌─────────────────────────────────────────┐
│ User / API Client     │ ────────────────────► │ AWS S3 BUCKET                           │
└───────────────────────┘                       │ (s3://financial-rag-documents)          │
                                                └────────────────────┬────────────────────┘
                                                                     │ 2. ObjectCreated Event
                                                                     ▼
┌───────────────────────────────────────┐  3. Send Message   ┌─────────────────────────────────────────┐
│ AWS SQS QUEUE                         │ ◄───────────────── │ AWS LAMBDA EVENT HANDLER                │
│ (financial-ingestion-queue)           │                    │ (aws_lambda/s3_sqs_trigger.py)          │
└───────────────────┬───────────────────┘                    └─────────────────────────────────────────┘
                    │ 4. Poll Jobs
                    ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ SQS INGESTOR SERVICE (app/sqs_ingestor.py)                                                           │
│ • Downloads Document Bytes from S3                                                                   │
│ • Verifies SHA-256 Content Hash (Skips Unchanged Docs, Increments Version on Updates)                │
│ • Table-Aware Chunker (app/chunker.py - Preserves Tables & Tracks Parent Section Context)            │
│ • Generates 768-dim Vector Embeddings (BAAI/bge-base-en-v1.5)                                         │
└───────────────────────────────────┬──────────────────────────────────────────────────────────────────┘
                                    │ 5. Upsert Document & Chunks
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ POSTGRESQL + PGVECTOR DATABASE (Port 5432)                                                           │
│ • document_registry: doc_id, s3_file_path, content_hash, version, timestamp                           │
│ • financial_chunks: 768-dim Vector (HNSW Cosine Index), tsvector BM25 Text (GIN Index), RBAC Roles   │
└──────────────────────────────────────────────────────────────────────────────────────────────────────┘


                                  =================================================
                                  2. SYNCHRONOUS & SSE STREAMING SEARCH READ PATH
                                  =================================================

┌───────────────────────┐  1. HTTP POST Request  ┌─────────────────────────────────────────┐
│ Browser Client / UI   │ ─────────────────────► │ SPRING BOOT API GATEWAY (Port 8080)     │
│ (static/index.html)   │ ◄───────────────────── │ • DTO Payload Validation (@Valid)       │
└───────────────────────┘  8. Real-Time Stream   │ • Reverse Proxying & Stream Forwarding  │
                                                 └───────────────────┬─────────────────────┘
                                                                     │ 2. Forward Request
                                                                     ▼
                                                 ┌─────────────────────────────────────────┐
                                                 │ PYTHON FASTAPI SEARCH SERVICE (Port 8000)│
                                                 │ • Embedding Model (BAAI/bge-base-en)    │
                                                 │ • Cross-Encoder Reranker               │
                                                 │ • SSE Generator Engine                  │
                                                 └───────────────────┬─────────────────────┘
                                                                     │ 3. Execute Vector + BM25 Query
                                                                     ▼
                                                 ┌─────────────────────────────────────────┐
                                                 │ POSTGRESQL HYBRID SEARCH (Port 5432)    │
                                                 │ • Dense Vector Cosine Similarity        │
                                                 │ • Full-Text Search ts_rank_cd (BM25)    │
                                                 │ • Reciprocal Rank Fusion (RRF k=60)     │
                                                 │ • Metadata Array RBAC Overlap Matching  │
                                                 └───────────────────┬─────────────────────┘
                                                                     │ 4. Top 20 Candidates
                                                                     ▼
┌───────────────────────────────────────┐  6. Context Prompt │ 5. Stage 2 Cross-Encoder Reranking
│ SYSTEM PROMPT TEMPLATE                │ ─────────────────► │ (cross-encoder/ms-marco-MiniLM-L-6-v2)
│ (llmprompt.txt)                       │                    └───────────────────┬─────────────────────┘
└───────────────────────────────────────┘                                        │ 7. SSE Events & Token Stream
                                                                                 ▼
                                                             [ Real-Time SSE Response Output Stream ]
```

---

## 🔄 END-TO-END STEP-BY-STEP READ & WRITE PATHS

### 1. Ingestion Write Path (Detailed Execution Steps)

1. **Document Upload**:
   - The user or client submits a document (PDF, Markdown, Text, CSV, JSON) via `POST /api/v1/documents/upload` or `/upload-file`.
2. **S3 Storage Persistence**:
   - [`app/s3_utils.py`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/app/s3_utils.py) uploads raw binary content to AWS S3 (`s3://financial-rag-documents/documents/<doc_id>/<filename>`).
3. **AWS S3 Event Trigger**:
   - S3 fires an `ObjectCreated:*` event notification to [`aws_lambda/s3_sqs_trigger.py`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/aws_lambda/s3_sqs_trigger.py).
4. **AWS SQS Message Dispatch**:
   - The Lambda function parses document metadata, builds a JSON re-indexing payload, and publishes it to the AWS SQS queue (`financial-ingestion-queue`).
5. **SQS Ingestor Processing**:
   - [`app/sqs_ingestor.py`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/app/sqs_ingestor.py) continuously polls SQS, fetches the document from S3, and computes a **SHA-256 content hash**.
   - If the SHA-256 hash matches the database registry, re-indexing is skipped. If updated, document version is incremented (`version = version + 1`).
6. **Table-Aware Financial Text Chunking & Numeric Normalization**:
   - [`app/chunker.py`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/app/chunker.py) normalizes financial accounting parentheses notation e.g. `(12,345)` $\rightarrow$ `(12,345) [-12345]` to ensure BM25 tsvector keyword search and vector search index financial losses accurately.
   - Extracts HTML/Markdown tables intact (`chunk_type="table"`) and stitches multi-page continuation tables split across page boundaries (`--- Page N ---`).
   - Prose text is segmented under semantic headers (`#`, `##`, `Item 1A`, etc.) while tracking `parent_section` context.
7. **Dense Vector Embedding Generation**:
   - Chunks are embedded into **768-dimensional dense vectors** using `BAAI/bge-base-en-v1.5`.
8. **Atomic Database Upsert**:
   - Database metadata is updated in `document_registry`.
   - Chunks, vectors, roles (`allowed_roles`), and generated tsvectors (`content_tsvector`) are saved in PostgreSQL `financial_chunks`.
9. **Cache Invalidation**:
   - L1 Redis query cache (`query:*`) is flushed.

---

### 2. Search & LLM Synthesis Read Path (Detailed Execution Steps)

1. **User Query Submission**:
   - The user enters a question in the UI (e.g. *"What was Apple revenue for iPhone segment in 2024?"*).
2. **Spring Boot Gateway Proxying**:
   - Spring Boot API Gateway ([`GatewayController.java`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/gateway-service/src/main/java/com/financialrag/gateway/controller/GatewayController.java)) validates request DTOs and proxies the request to Python FastAPI on Port 8000.
3. **Real-Time SSE Event Step 1 (Embedding Computation)**:
   - FastAPI emits SSE event `event: status` $\rightarrow$ `Step 1: Computing 768-dim query vector (BAAI/bge-base-en-v1.5)`.
4. **Real-Time SSE Event Step 2 (Stage 1 Hybrid SQL Search)**:
   - FastAPI emits `event: status` $\rightarrow$ `Step 2: Executing Stage 1 Hybrid SQL RRF Search`.
   - PostgreSQL runs parallel candidate retrieval:
     - **Dense Vector Search**: Cosine distance (`embedding <=> query_vector`) using HNSW index.
     - **Full-Text Search (BM25)**: Keyword search (`content_tsvector @@ plainto_tsquery('english', query)`) using GIN index.
     - **RBAC Filter**: Array overlap matching (`allowed_roles && user_roles::text[]`).
     - **RRF Fusion**: Combines rankings in SQL: $\text{RRF Score} = \frac{1}{60 + \text{rank}_{\text{vec}}} + \frac{1}{60 + \text{rank}_{\text{fts}}}$.
5. **Real-Time SSE Event Step 3 (Stage 2 Cross-Encoder Reranking)**:
   - FastAPI emits `event: status` $\rightarrow$ `Step 3: Stage 2 Cross-Encoder Reranking`.
   - Top candidates are re-scored in Python using `cross-encoder/ms-marco-MiniLM-L-6-v2` for precise relevance.
6. **Real-Time SSE Event Step 4 (Context Prompt Assembly)**:
   - FastAPI emits `event: status` $\rightarrow$ `Step 4: Populating prompt context template from llmprompt.txt`.
   - Reads system prompt template from [`llmprompt.txt`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/llmprompt.txt) and substitutes `{context}` (formatted source blocks) and `{query}`.
7. **Real-Time SSE Event Step 5 (Live Token Streaming)**:
   - FastAPI emits `event: sources` containing retrieved document chunks metadata for the UI drawer.
   - FastAPI emits continuous `event: token` messages streaming answer tokens in real time to the browser UI.

---

## 🛠️ SERVICE & COMPONENT BREAKDOWN

| Component / Module | Technology | File Path | Function / Responsibility |
|---|---|---|---|
| **API Gateway** | Spring Boot 3 (Java 17) | [`gateway-service/`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/gateway-service) | Public REST entry point (Port 8080), payload validation, SSE stream proxying. |
| **RAG Search Microservice** | Python FastAPI / PyTorch | [`app/main.py`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/app/main.py) | REST AI engine (Port 8000), embedding pipeline, SSE status generator. |
| **Financial Chunker** | Python Regex | [`app/chunker.py`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/app/chunker.py) | Table-aware document parser, header context tracking. |
| **Hybrid Search Engine** | PostgreSQL SQL / Cross-Encoder | [`app/hybrid_search.py`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/app/hybrid_search.py) | 2-Stage retrieval: SQL RRF (Vector + BM25) + Cross-Encoder reranking. |
| **LLM Synthesizer** | Python / Gemini / OpenAI | [`app/llm_synthesizer.py`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/app/llm_synthesizer.py) | Reads `llmprompt.txt`, streams token chunks formatted as SSE events. |
| **S3 Storage Helper** | boto3 SDK | [`app/s3_utils.py`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/app/s3_utils.py) | AWS S3 object upload/download manager with local disk fallback. |
| **AWS Lambda Handler** | AWS Lambda Python 3.11 | [`aws_lambda/s3_sqs_trigger.py`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/aws_lambda/s3_sqs_trigger.py) | Captures S3 `ObjectCreated` events, pushes re-indexing jobs to SQS. |
| **SQS Ingestor Worker** | Python Daemon / boto3 | [`app/sqs_ingestor.py`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/app/sqs_ingestor.py) | SQS queue consumer, automated S3 document re-indexing daemon. |
| **Database & Indices** | PostgreSQL + `pgvector` | [`sql/schema.sql`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/sql/schema.sql) | 768-dim HNSW vector index, GIN BM25 tsvector index, RBAC array index. |
| **Minimalist Frontend** | Vanilla HTML5/JS / Inter Font | [`static/index.html`](file:///c:/Users/jvina/.gemini/antigravity-ide/scratch/financial-rag-backend/static/index.html) | High-contrast professional dashboard with real-time SSE step tracker. |

---

## 📁 REPOSITORY STRUCTURE

```
FinSearch/
├── gateway-service/           # Spring Boot API Gateway (Java 17, Port 8080)
│   ├── Dockerfile
│   ├── pom.xml
│   └── src/
│       └── main/
│           ├── java/com/financialrag/gateway/
│           │   ├── GatewayApplication.java
│           │   ├── controller/GatewayController.java
│           │   ├── dto/
│           │   └── service/RagService.java
│           └── resources/application.properties
├── app/                       # Python AI Search Microservice (Port 8000)
│   ├── __init__.py
│   ├── main.py                # FastAPI REST API & SSE Gateway
│   ├── config.py              # Pydantic Settings & AWS Configuration
│   ├── database.py            # PostgreSQL Connection Pool & Redis Client
│   ├── chunker.py             # Table-Aware Financial Text Splitter
│   ├── hybrid_search.py       # Stage 1 SQL RRF Search & Stage 2 Cross-Encoder Reranker
│   ├── llm_synthesizer.py     # Prompt Engine & SSE Token Streamer
│   ├── s3_utils.py            # AWS S3 Storage Helpers
│   ├── sqs_ingestor.py        # SQS Automated Re-Indexing Daemon
│   └── models.py              # Pydantic Request/Response DTOs
├── aws_lambda/
│   └── s3_sqs_trigger.py      # AWS Lambda Handler for S3 ObjectCreated Events
├── sql/
│   └── schema.sql             # PostgreSQL DDL (768-dim HNSW & GIN Indexes)
├── static/
│   └── index.html             # Minimalist Professional UI with SSE Step Tracker
├── llmprompt.txt              # System Prompt Template with {context} & {query}
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## 📡 API REFERENCE & USAGE EXAMPLES

### 1. System Health Check
`GET http://localhost:8080/health`

```bash
curl http://localhost:8080/health
```

**Response**:
```json
{
  "gateway_status": "healthy",
  "python_service": "{\"status\":\"ok\",\"database\":\"healthy\"}"
}
```

---

### 2. Upload Document (JSON Payload to S3 & Postgres)
`POST http://localhost:8080/api/v1/documents/upload`

```bash
curl -X POST "http://localhost:8080/api/v1/documents/upload" \
     -H "Content-Type: application/json" \
     -d '{
       "doc_id": "DOC-AAPL-2024",
       "ticker_symbol": "AAPL",
       "filename": "aapl_2024_10k.md",
       "content": "# Item 1. Business\nApple Inc. designs smartphones...\n\n| Segment | 2024 Revenue ($M) |\n|---|---|\n| iPhone | 201183 |\n| Services | 96169 |",
       "allowed_roles": ["analyst", "admin"]
     }'
```

---

### 3. 2-Stage Hybrid Search & Rerank Query
`POST http://localhost:8080/api/v1/search`

```bash
curl -X POST "http://localhost:8080/api/v1/search" \
     -H "Content-Type: application/json" \
     -d '{
       "query": "What was Apple revenue for iPhone segment in 2024?",
       "user_roles": ["analyst"],
       "top_k": 5
     }'
```

---

### 4. Real-Time SSE Token & Pipeline Status Stream
`POST http://localhost:8080/api/v1/generate-stream`

```bash
curl -N -X POST "http://localhost:8080/api/v1/generate-stream" \
     -H "Content-Type: application/json" \
     -d '{
       "query": "What was Apple revenue for iPhone segment in 2024?",
       "user_roles": ["analyst"],
       "top_k": 5
     }'
```

**Stream Output**:
```http
event: status
data: {"step": 1, "message": "Computing 768-dim query vector (BAAI/bge-base-en-v1.5)..."}

event: status
data: {"step": 2, "message": "Executing Stage 1 Hybrid SQL RRF Search (Vector + BM25 tsvector)..."}

event: status
data: {"step": 3, "message": "Stage 2 Cross-Encoder reranked top 5 relevant disclosures."}

event: status
data: {"step": 4, "message": "Populating context prompt template from llmprompt.txt..."}

event: sources
data: [{"chunk_id":"DOC-AAPL-2024_c0_f1a2b3","doc_id":"DOC-AAPL-2024","rerank_score":3.841}]

event: status
data: {"step": 5, "message": "Synthesizing financial analysis..."}

event: token
data: Based 

event: token
data: on 

event: token
data: the 

event: token
data: Apple 
```

---

## 🚀 QUICK START & DEPLOYMENT

### Launch Services via Docker Compose

```bash
docker-compose up --build -d
```

Spins up:
- **PostgreSQL 15** with `pgvector` enabled (Port `5432`)
- **Python AI Search Service** (Port `8000`)
- **Spring Boot API Gateway** (Port `8080`)

### Environment Configuration (.env)

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=financial_rag
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

EMBEDDING_MODEL_NAME=BAAI/bge-base-en-v1.5
CROSS_ENCODER_MODEL_NAME=cross-encoder/ms-marco-MiniLM-L-6-v2
EMBEDDING_DIMENSION=768

AWS_REGION=us-east-1
AWS_S3_BUCKET=financial-rag-documents
AWS_SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/financial-ingestion-queue
# Optional: GEMINI_API_KEY=your_key_here
```
