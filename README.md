# FinSearch: Production-Grade Financial Document Search & Indexing Engine

An enterprise microservices-based financial document search engine featuring a **Spring Boot API Gateway (Java 17)**, **Python AI RAG Engine**, **PostgreSQL (pgvector)**, **Table-Aware Text Chunking**, **Hybrid RRF Search**, and **Cross-Encoder Reranking**.

---

## ARCHITECTURE OVERVIEW

```
[User / Client]
       │
       │ Upload File (PDF / MD / TXT)
       ▼
┌────────────────────────────────────────────────────────┐
│ AWS S3 BUCKET (s3://financial-rag-documents)           │
│ • Raw Financial Document File Storage                  │
└───────────────────────────┬────────────────────────────┘
                            │ ObjectCreated Event
                            ▼
┌────────────────────────────────────────────────────────┐
│ AWS LAMBDA TRIGGER (aws_lambda/s3_sqs_trigger.py)      │
│ • Extracts S3 Bucket, Key & Document Metadata          │
│ • Pushes Event Payload to SQS Queue                    │
└───────────────────────────┬────────────────────────────┘
                            │ Send Message
                            ▼
┌────────────────────────────────────────────────────────┐
│ AWS SQS QUEUE (financial-ingestion-queue)              │
│ • Asynchronous Ingestion Job Buffer                    │
└───────────────────────────┬────────────────────────────┘
                            │ Poll Jobs
                            ▼
┌────────────────────────────────────────────────────────┐
│ SQS INGESTOR SERVICE (app/sqs_ingestor.py)             │
│ • Downloads Updated File from S3                       │
│ • Table-Aware Financial Chunker (app/chunker.py)       │
│ • BAAI/bge-base-en-v1.5 Embeddings (768-dim)          │
│ • PostgreSQL Vector HNSW + BM25 tsvector Re-Indexing   │
└───────────────────────────┬────────────────────────────┘
                            │ Upsert Chunks & Vectors
                            ▼
┌────────────────────────────────────────────────────────┐
│ POSTGRESQL + PGVECTOR (Port 5432)                      │
│ • HNSW Vector Cosine Index (768-dim)                   │
│ • GIN Full-Text Keyword Search Index (BM25)            │
│ • RBAC Metadata Array (allowed_roles)                  │
└────────────────────────────────────────────────────────┘
```

---

## KEY SYSTEM FEATURES

### 1. Event-Driven S3 + Lambda + SQS Re-Indexing
- Raw documents are stored in **AWS S3** (`s3://financial-rag-documents`).
- S3 `ObjectCreated` events trigger an **AWS Lambda function** (`aws_lambda/s3_sqs_trigger.py`), which pushes re-indexing payloads to an **AWS SQS Queue**.
- An **SQS Ingestor Service** (`app/sqs_ingestor.py`) continuously polls SQS, fetches updated documents from S3, re-chunks tables and text, computes 768-dim embeddings, and updates PostgreSQL vector and BM25 search indices atomically.

### 2. Spring Boot API Gateway (Java 17)
- Exposes public REST endpoints on **Port 8080** for document ingestion (`/api/v1/documents/upload`), hybrid search (`/api/v1/search`), streaming LLM answer synthesis (`/api/v1/generate-stream`), and health checks (`/health`).
- Implements request payload validation (`jakarta.validation`), DTO mapping, and microservice proxy routing.

### 3. 2-Stage Hybrid Search & Reranking Engine
- **Stage 1 (Hybrid RRF Search):** Combines **Dense Vector Search** (HNSW Cosine index, 768-dim `BAAI/bge-base-en-v1.5`) and **Full-Text Keyword Search** (GIN index) in PostgreSQL, merged natively in SQL via **Reciprocal Rank Fusion (RRF $k=60$)**.
- **Stage 2 (Cross-Encoder Reranking):** Uses `cross-encoder/ms-marco-MiniLM-L-6-v2` to re-score candidate chunks for maximum precision.


### 3. Table-Aware Financial Chunker
- Preserves HTML and Markdown tabular data (`| ... |` and `<table>`) as atomic, unbroken table chunks (`chunk_type="table"`).
- Splits prose text by section headers (`#`, `##`, `Item 1A`, etc.) while tracking `parent_section` context.

### 4. Metadata-Based Role-Based Access Control (RBAC)
- Filters chunks in PostgreSQL using array overlap matching (`allowed_roles && user_roles::text[]`) powered by GIN indexes.

---

## REPOSITORY STRUCTURE

```
FinSearch/
├── gateway-service/           # Spring Boot API Gateway (Java 17, Maven, Port 8080)
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
│   ├── main.py                # REST Engine
│   ├── config.py              # System Configuration
│   ├── database.py            # PostgreSQL Connection Pool
│   ├── chunker.py             # Table-Aware Financial Text Splitter
│   ├── hybrid_search.py       # Stage 1 SQL RRF Search & Stage 2 Cross-Encoder Reranker
│   └── models.py              # Pydantic Schemas
├── sql/
│   └── schema.sql             # PostgreSQL DDL (HNSW Vector & FTS GIN Indexes)
├── static/
│   └── index.html             # Minimalist High-Contrast Test Interface
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## QUICK START GUIDE (DOCKER COMPOSE)

### 1. Launch Services
Run all microservices using Docker Compose:

```bash
docker-compose up --build -d
```

This spins up:
- **PostgreSQL** with `pgvector` enabled (Port `5432`)
- **Python AI Search Service** (Port `8000`)
- **Spring Boot API Gateway** (Port `8080`)

### 2. Verify Health Status
Check container health via the Spring Boot API Gateway:

```bash
curl http://localhost:8080/health
```

---

## API REFERENCE & USAGE EXAMPLES

### 1. Upload Financial Document
`POST http://localhost:8080/api/v1/documents/upload`

```bash
curl -X POST "http://localhost:8080/api/v1/documents/upload" \
     -H "Content-Type: application/json" \
     -d '{
       "doc_id": "DOC-AAPL-10K-2024",
       "ticker_symbol": "AAPL",
       "filename": "aapl_2024_10k.md",
       "content": "# Item 1. Business\nApple Inc. designs smartphones...\n\n| Segment | 2024 Revenue ($M) |\n|---|---|\n| iPhone | 201183 |\n| Services | 96169 |",
       "allowed_roles": ["analyst", "admin"]
     }'
```

### 2. 2-Stage Hybrid Search & Rerank Query
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
