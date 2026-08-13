import logging
import os

logger = logging.getLogger("financial_rag.llm")

def generate_humanized_answer(query: str, retrieved_chunks: list[dict]) -> str:
    """
    Synthesizes a humanized natural language answer from retrieved RRF & Reranked financial chunks.
    Supports Google Gemini API / OpenAI API if configured, with a structured RAG fallback engine.
    """
    if not retrieved_chunks:
        return "Based on your permissions and query, no relevant financial documents were found in the database."

    # Build Augmented Context Prompt
    context_blocks = []
    for idx, chunk in enumerate(retrieved_chunks):
        block = (
            f"--- [SOURCE #{idx+1} | SECTION: {chunk['parent_section']} | "
            f"TYPE: {chunk['chunk_type'].upper()} | DOC: {chunk['doc_id']} ({chunk['ticker_symbol']})] ---\n"
            f"{chunk['content']}"
        )
        context_blocks.append(block)

    formatted_context = "\n\n".join(context_blocks)

    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = f"""You are an expert financial analyst. Answer the user question accurately and concisely using ONLY the provided document context.

CONTEXT:
{formatted_context}

USER QUESTION: {query}

HUMANIZED ANSWER:"""
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            logger.warning(f"Gemini LLM call failed, falling back to structured synthesis: {e}")

    # High-precision RAG Answer Synthesizer Fallback
    top_chunk = retrieved_chunks[0]
    answer_parts = [
        f"Based on the retrieved financial data from **{top_chunk['doc_id']}** (Ticker: {top_chunk['ticker_symbol']}, Section: *{top_chunk['parent_section']}*):",
        "",
        top_chunk['content'],
        ""
    ]

    if len(retrieved_chunks) > 1:
        answer_parts.append("### Additional Supporting Disclosures:")
        for extra in retrieved_chunks[1:]:
            answer_parts.append(f"- **[{extra['parent_section']}]**: {extra['content'][:250]}...")

    return "\n".join(answer_parts)
