import asyncio
import logging
import os
import time
from typing import AsyncGenerator

logger = logging.getLogger("financial_rag.llm")

DEFAULT_PROMPT_TEMPLATE = """You are FinSearch AI, a world-class financial analyst and research assistant. Answer the user question accurately, professionally, and concisely using ONLY the provided document disclosures below.

--- FINANCIAL CONTEXT DISCLOSURES ---
{context}
-------------------------------------

USER QUESTION: {query}

CRITICAL INSTRUCTIONS:
1. Ground every statement directly in the provided disclosures.
2. Highlight exact figures, revenue numbers, dates, segments, and ticker symbols whenever mentioned.
3. Cite sources inline using tags like [Source #1], [Source #2], etc., matching the section headers in the context.
4. If the provided context does not contain enough information to answer the user's question, clearly state what information is missing.

HUMANIZED FINANCIAL ANALYSIS:
"""


def load_prompt_template() -> str:
    """
    Reads the system prompt template from llmprompt.txt located in the project root directory.
    Falls back to DEFAULT_PROMPT_TEMPLATE if file is missing or unreadable.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_file_path = os.path.join(project_root, "llmprompt.txt")
    if os.path.exists(prompt_file_path):
        try:
            with open(prompt_file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content and "{context}" in content and "{query}" in content:
                    return content
                elif content:
                    # Append placeholder structures if missing
                    return content + "\n\n--- FINANCIAL CONTEXT DISCLOSURES ---\n{context}\n\nUSER QUESTION: {query}\n\nHUMANIZED FINANCIAL ANALYSIS:"
        except Exception as e:
            logger.warning(f"Failed to read llmprompt.txt, using default prompt template: {e}")
    return DEFAULT_PROMPT_TEMPLATE


def build_augmented_context(retrieved_chunks: list[dict]) -> str:
    """
    Formats retrieved chunks into clean context blocks for the prompt template.
    """
    if not retrieved_chunks:
        return "No relevant financial documents were retrieved."

    context_blocks = []
    for idx, chunk in enumerate(retrieved_chunks):
        block = (
            f"--- [Source #{idx+1} | Section: {chunk.get('parent_section', 'N/A')} | "
            f"Type: {chunk.get('chunk_type', 'text').upper()} | Doc: {chunk.get('doc_id', 'N/A')} ({chunk.get('ticker_symbol', 'N/A')})] ---\n"
            f"{chunk.get('content', '')}"
        )
        context_blocks.append(block)

    return "\n\n".join(context_blocks)


def generate_humanized_answer(query: str, retrieved_chunks: list[dict]) -> str:
    """
    Synthesizes a complete humanized natural language answer from retrieved financial chunks.
    Reads prompt template from llmprompt.txt.
    """
    if not retrieved_chunks:
        return "Based on your permissions and query, no relevant financial documents were found in the database."

    formatted_context = build_augmented_context(retrieved_chunks)
    template = load_prompt_template()
    prompt = template.format(context=formatted_context, query=query)

    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            if response.text:
                return response.text.strip()
        except Exception as e:
            logger.warning(f"Gemini API call failed, falling back to structured synthesis: {e}")

    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"OpenAI API call failed, falling back to structured synthesis: {e}")

    # Fallback High-Precision Structured RAG Synthesizer
    top_chunk = retrieved_chunks[0]
    answer_parts = [
        f"Based on the retrieved financial data from **{top_chunk['doc_id']}** (Ticker: {top_chunk['ticker_symbol']}, Section: *{top_chunk['parent_section']}*):",
        "",
        top_chunk['content'],
        ""
    ]

    if len(retrieved_chunks) > 1:
        answer_parts.append("### Additional Supporting Disclosures:")
        for idx, extra in enumerate(retrieved_chunks[1:], start=2):
            answer_parts.append(f"- **[Source #{idx} - {extra['parent_section']}]**: {extra['content'][:250]}...")

    return "\n".join(answer_parts)


def format_sse(event_type: str, data_payload: dict | list | str) -> str:
    """
    Formats data payload into Server-Sent Event (SSE) format.
    """
    if isinstance(data_payload, (dict, list)):
        data_str = json.dumps(data_payload)
    else:
        data_str = str(data_payload)
    return f"event: {event_type}\ndata: {data_str}\n\n"


async def stream_llm_answer(query: str, retrieved_chunks: list[dict]) -> AsyncGenerator[str, None]:
    """
    Async Generator that streams tokens of the LLM response formatted as SSE token events.
    """
    if not retrieved_chunks:
        yield format_sse("token", "Based on your permissions and query, no relevant financial documents were found in the database.")
        return

    formatted_context = build_augmented_context(retrieved_chunks)
    template = load_prompt_template()
    prompt = template.format(context=formatted_context, query=query)

    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt, stream=True)
            for chunk in response:
                if chunk.text:
                    yield format_sse("token", chunk.text)
                    await asyncio.sleep(0.01)
            return
        except Exception as e:
            logger.warning(f"Streaming Gemini API call failed, falling back: {e}")

    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            stream = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                stream=True
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield format_sse("token", chunk.choices[0].delta.content)
                    await asyncio.sleep(0.01)
            return
        except Exception as e:
            logger.warning(f"Streaming OpenAI API call failed, falling back: {e}")

    # Fallback Streamer: Yield structured answer line by line / token by token as SSE token events
    fallback_full_text = generate_humanized_answer(query, retrieved_chunks)
    words = fallback_full_text.split(" ")
    for idx, word in enumerate(words):
        token_text = word + (" " if idx < len(words) - 1 else "")
        yield format_sse("token", token_text)
        await asyncio.sleep(0.02)


