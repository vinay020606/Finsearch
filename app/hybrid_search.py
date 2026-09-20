import logging
import os
import re

import numpy as np
from sentence_transformers import CrossEncoder, SentenceTransformer
from app.config import settings

logger = logging.getLogger("financial_rag.hybrid_search")

# Global model instances
_cross_encoder: CrossEncoder | None = None
_embedding_model: SentenceTransformer | None = None


def get_cross_encoder() -> CrossEncoder:
    global _cross_encoder
    if _cross_encoder is None:
        logger.info(f"Loading Cross-Encoder model: {settings.CROSS_ENCODER_MODEL_NAME}")
        _cross_encoder = CrossEncoder(settings.CROSS_ENCODER_MODEL_NAME)
    return _cross_encoder


def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Loading Embedding model: {settings.EMBEDDING_MODEL_NAME}")
        _embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
    return _embedding_model


def generate_hyde_document(query_text: str) -> str:
    """
    Generates a hypothetical financial document chunk (HyDE) corresponding to the search query.
    Improves vector recall by converting abstract user queries into concrete domain-specific disclosures.
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    prompt = (
        f"You are a financial filing assistant. Generate a short, realistic 2-3 sentence SEC 10-K document disclosure "
        f"paragraph that directly answers the following financial search query: '{query_text}'. "
        f"Include key metrics, financial context, and specific disclosure terms."
    )

    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            if response.text:
                hyde_doc = response.text.strip()
                logger.info(f"HyDE document generated via Gemini LLM for query: '{query_text[:30]}...'")
                return hyde_doc
        except Exception as e:
            logger.warning(f"HyDE generation via Gemini failed, using template fallback: {e}")

    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=150
            )
            hyde_doc = response.choices[0].message.content.strip()
            logger.info(f"HyDE document generated via OpenAI LLM for query: '{query_text[:30]}...'")
            return hyde_doc
        except Exception as e:
            logger.warning(f"HyDE generation via OpenAI failed, using template fallback: {e}")

    # Fallback domain-specific hypothetical text synthesizer
    fallback_hyde = (
        f"Item 1A / Financial Statements Disclosure: Detailed analysis and financial metrics regarding {query_text}. "
        f"The company reports consolidated financial performance, revenue streams, operational expenses, and key risk factors "
        f"pertaining to {query_text} across current and prior fiscal periods."
    )
    logger.info(f"HyDE hypothetical document synthesized via domain template fallback.")
    return fallback_hyde


def extract_query_metadata(query_text: str) -> dict:
    """
    Automatically parses natural language queries to extract metadata parameters:
    - ticker_symbol (e.g. AAPL, MSFT, TSLA, GOOGL, AMZN, NVDA, META)
    - fiscal_year (e.g. 2024, 2023, 2022)
    - parent_section (e.g. Item 1A, Risk Factors, Item 7, MD&A, Balance Sheet)
    """
    metadata = {}
    q_upper = query_text.upper()

    # 1. Ticker Symbol Extraction
    known_tickers = [
        "AAPL", "MSFT", "TSLA", "GOOGL", "GOOG", "AMZN", "NVDA", "META",
        "NFLX", "AMD", "INTC", "JPM", "BAC", "WMT", "DIS", "PYPL", "COIN"
    ]
    for ticker in known_tickers:
        if re.search(r'\b' + ticker + r'\b', q_upper):
            metadata["ticker_symbol"] = ticker
            break

    # 2. Fiscal Year Extraction
    year_match = re.search(r'\b(20[12][0-9])\b', query_text)
    if year_match:
        metadata["fiscal_year"] = year_match.group(1)

    # 3. Parent Section Extraction
    section_patterns = [
        (r'\bITEM\s*1A\b|\bRISK\s*FACTORS\b', "Item 1A - Risk Factors"),
        (r'\bITEM\s*7\b|\bMD&A\b|\bMANAGEMENT\s*DISCUSSION\b', "Item 7 - MD&A"),
        (r'\bITEM\s*1\b|\bBUSINESS\b', "Item 1 - Business"),
        (r'\bBALANCE\s*SHEET\b', "Balance Sheet"),
        (r'\bINCOME\s*STATEMENT\b', "Income Statement"),
        (r'\bCASH\s*FLOWS?\b', "Cash Flows Statement"),
    ]
    for pattern, section_name in section_patterns:
        if re.search(pattern, query_text, re.IGNORECASE):
            metadata["parent_section"] = section_name
            break

    if metadata:
        logger.info(f"Extracted dynamic query metadata filters: {metadata}")

    return metadata


def execute_hybrid_rrf_search(
    conn,
    query_text: str,
    query_vector: list[float],
    user_roles: list[str],
    top_k: int = 10,
    rrf_k: int = 60,
    use_hyde: bool = True
) -> list[dict]:
    """
    Executes 2-Stage Hybrid Search & Reranking with HyDE & Dynamic SQL Metadata Filtering:

    1. HyDE Query Expansion: Generates hypothetical document embedding and blends with raw query vector.
    2. Dynamic SQL Metadata Filtering: Automatically extracts ticker, year, and section parameters into PostgreSQL WHERE clauses.
    3. Stage 1: Dense Vector Search (HNSW) + Full-Text Search (GIN) filtered by RBAC metadata (allowed_roles && user_roles).
       Merges rankings using Reciprocal Rank Fusion (RRF k=60) in PostgreSQL SQL.
    4. Stage 2: Cross-Encoder Reranking over candidate results to re-score precise query-chunk relevance.
    """
    # Strict RBAC Guardrail: Sanitize & validate user roles to prevent unauthorized data access
    clean_roles = [r.strip().lower() for r in user_roles if r and isinstance(r, str) and r.strip()]
    if not clean_roles:
        logger.warning("Search request rejected: Empty or invalid RBAC user_roles provided.")
        return []

    # 1. HyDE Vector Expansion
    effective_query_vector = query_vector
    if use_hyde and query_text:
        try:
            hyde_doc = generate_hyde_document(query_text)
            embedding_model = get_embedding_model()
            hyde_raw_vector = embedding_model.encode(hyde_doc).tolist()

            # Blend raw query vector (0.5) + HyDE vector (0.5) and normalize
            vec_a = np.array(query_vector, dtype=np.float32)
            vec_b = np.array(hyde_raw_vector, dtype=np.float32)
            blended = 0.5 * vec_a + 0.5 * vec_b
            norm = np.linalg.norm(blended)
            if norm > 0:
                blended = blended / norm
            effective_query_vector = blended.tolist()
            logger.info("HyDE vector expansion successfully blended into query embedding.")
        except Exception as e:
            logger.warning(f"HyDE expansion failed, falling back to raw query vector: {e}")

    # 2. Extract Query Metadata for Dynamic PostgreSQL Filters
    meta_filters = extract_query_metadata(query_text)

    # Build dynamic WHERE clause extensions
    extra_vector_where = ""
    extra_fts_where = ""
    filter_params = []

    if "ticker_symbol" in meta_filters:
        extra_vector_where += " AND ticker_symbol = %s"
        extra_fts_where += " AND ticker_symbol = %s"
        filter_params.append(meta_filters["ticker_symbol"])

    if "parent_section" in meta_filters:
        extra_vector_where += " AND parent_section ILIKE %s"
        extra_fts_where += " AND parent_section ILIKE %s"
        filter_params.append(f"%{meta_filters['parent_section']}%")

    with conn.cursor() as cursor:
        query = f"""
        WITH vector_search AS (
            SELECT chunk_id, doc_id, ticker_symbol, parent_section, content, chunk_type, allowed_roles,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS rank_vec
            FROM financial_chunks
            WHERE allowed_roles && %s::text[]{extra_vector_where}
            LIMIT 20
        ),
        fts_search AS (
            SELECT chunk_id, doc_id, ticker_symbol, parent_section, content, chunk_type, allowed_roles,
                   ROW_NUMBER() OVER (ORDER BY ts_rank_cd(content_tsvector, plainto_tsquery('english', %s)) DESC) AS rank_fts
            FROM financial_chunks
            WHERE content_tsvector @@ plainto_tsquery('english', %s)
              AND allowed_roles && %s::text[]{extra_fts_where}
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

        # Parameters order:
        # vector_search: query_vector, clean_roles, [filter_params...]
        # fts_search: query_text, query_text, clean_roles, [filter_params...]
        # combined: rrf_k, rrf_k
        params = [str(effective_query_vector), clean_roles]
        params.extend(filter_params)
        params.extend([query_text, query_text, clean_roles])
        params.extend(filter_params)
        params.extend([rrf_k, rrf_k])

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

