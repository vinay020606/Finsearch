-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Document Registry Table with local file storage path
CREATE TABLE IF NOT EXISTS document_registry (
    doc_id VARCHAR(255) PRIMARY KEY,
    ticker_symbol VARCHAR(50) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL DEFAULT '',
    content_hash VARCHAR(64) NOT NULL,
    version INT DEFAULT 1 NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Financial Chunks Table with RBAC Metadata Array
CREATE TABLE IF NOT EXISTS financial_chunks (
    chunk_id VARCHAR(255) PRIMARY KEY,
    doc_id VARCHAR(255) NOT NULL REFERENCES document_registry(doc_id) ON DELETE CASCADE,
    ticker_symbol VARCHAR(50) NOT NULL,
    parent_section TEXT NOT NULL,
    content TEXT NOT NULL,
    chunk_type VARCHAR(20) NOT NULL CHECK (chunk_type IN ('text', 'table')),
    embedding vector(768) NOT NULL,
    allowed_roles TEXT[] NOT NULL DEFAULT ARRAY['admin', 'analyst'],
    content_tsvector tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);

-- HNSW Vector Index using Cosine Distance
CREATE INDEX IF NOT EXISTS financial_chunks_embedding_hnsw 
    ON financial_chunks USING hnsw (embedding vector_cosine_ops);

-- Full-Text Search GIN Index
CREATE INDEX IF NOT EXISTS financial_chunks_fts_gin 
    ON financial_chunks USING gin (content_tsvector);

-- Role-Based Access Control GIN Index
CREATE INDEX IF NOT EXISTS financial_chunks_roles_idx 
    ON financial_chunks USING gin (allowed_roles);
