import logging
from sentence_transformers import CrossEncoder
from app.config import settings

logger = logging.getLogger("financial_rag.hybrid_search")

# Global Cross-Encoder Reranker instance
_cross_encoder: CrossEncoder | None = None

def get_cross_encoder() -> CrossEncoder:
    global _cross_encoder
    if _cross_encoder is None:
        logger.info(f"Loading Cross-Encoder model: {settings.CROSS_ENCODER_MODEL_NAME}")
        _cross_encoder = CrossEncoder(settings.CROSS_ENCODER_MODEL_NAME)
    return _cross_encoder


def execute_hybrid_rrf_search(
    conn,
    query_text: str,
    query_vector: list[float],
    user_roles: list[str],
    top_k: int = 10,
    rrf_k: int = 60
) -> list[dict]:
    """
    Executes 2-Stage Hybrid Search & Reranking:
    
    1. Stage 1: Dense Vector Search (HNSW) + Full-Text Search (GIN) filtered by RBAC metadata (allowed_roles && user_roles).
       Merges rankings using Reciprocal Rank Fusion (RRF k=60) in PostgreSQL SQL.
    2. Stage 2: Cross-Encoder Reranking over candidate results to re-score precise query-chunk relevance.
    """
    # Strict RBAC Guardrail: Sanitize & validate user roles to prevent unauthorized data access
    clean_roles = [r.strip().lower() for r in user_roles if r and isinstance(r, str) and r.strip()]
    if not clean_roles:
        logger.warning("Search request rejected: Empty or invalid RBAC user_roles provided.")
        return []

    with conn.cursor() as cursor:
        query = """
        WITH vector_search AS (
            SELECT chunk_id, doc_id, ticker_symbol, parent_section, content, chunk_type, allowed_roles,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS rank_vec
            FROM financial_chunks
            WHERE allowed_roles && %s::text[]
            LIMIT 20
        ),
        fts_search AS (
            SELECT chunk_id, doc_id, ticker_symbol, parent_section, content, chunk_type, allowed_roles,
                   ROW_NUMBER() OVER (ORDER BY ts_rank_cd(content_tsvector, plainto_tsquery('english', %s)) DESC) AS rank_fts
            FROM financial_chunks
            WHERE content_tsvector @@ plainto_tsquery('english', %s)
              AND allowed_roles && %s::text[]
            LIMIT 20
        ),
        combined AS (
            SELECT
                COALESCE(v.chunk_id, f.chunk_id) AS chunk_id,
                COALESCE(v.doc_id, f.doc_id) AS doc_id,
                COALESCE(v.ticker_symbol, f.ticker_symbol) AS ticker_symbol,
                COALESCE(v.parent_section, f.parent_section) AS parent_section,
                COALESCE(v.content, f.content) AS content,
                COALESCE(v.chunk_type, f.chunk_type) AS chunk_type,
                COALESCE(v.allowed_roles, f.allowed_roles) AS allowed_roles,
                (COALESCE(1.0 / (%s + v.rank_vec), 0.0) + COALESCE(1.0 / (%s + f.rank_fts), 0.0))::float AS rrf_score
            FROM vector_search v
            FULL OUTER JOIN fts_search f ON v.chunk_id = f.chunk_id
        )
        SELECT chunk_id, doc_id, ticker_symbol, parent_section, content, chunk_type, allowed_roles, rrf_score
        FROM combined
        ORDER BY rrf_score DESC
        LIMIT 20;
        """

        params = [
            str(query_vector),
            clean_roles,
            query_text,
            query_text,
            clean_roles,
            rrf_k,
            rrf_k
        ]

        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

        candidates = [dict(zip(columns, row)) for row in rows]

        if not candidates:
            logger.info("No candidates found in Stage 1 Hybrid Search.")
            return []

        # Stage 2: Cross-Encoder Reranking
        logger.info(f"Stage 2 Reranking {len(candidates)} candidates using Cross-Encoder...")
        cross_encoder = get_cross_encoder()
        pairs = [[query_text, c["content"]] for c in candidates]
        scores = cross_encoder.predict(pairs)

        for candidate, score in zip(candidates, scores):
            candidate["rerank_score"] = float(score)

        # Sort candidate results by Cross-Encoder rerank score descending
        reranked_results = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
        top_results = reranked_results[:top_k]

        logger.info(f"Hybrid RRF + Cross-Encoder Reranking completed. Returning top {len(top_results)} results.")
        return top_results
