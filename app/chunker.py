import re
import logging

logger = logging.getLogger("financial_rag.chunker")

# Regex to detect Markdown tables and HTML tables intact
TABLE_PATTERN = re.compile(
    r'(?:'
    r'<table[\s\S]*?</table>'
    r'|'
    r'(?:(?:^|\n)[ \t]*\|[^\n]+\|[ \t]*\r?\n[ \t]*\|[-:\s|]+\|[ \t]*\r?\n(?:[ \t]*\|[^\n]+\|[ \t]*(?:\r?\n|$))+)'
    r')',
    re.IGNORECASE | re.MULTILINE
)

# Regex to detect Markdown headers (# Header 1, ## Header 2, etc.) or Item headings (Item 1A., etc.)
HEADER_PATTERN = re.compile(
    r'^(?:'
    r'#{1,6}\s+(.+)'
    r'|'
    r'(?:ITEM\s+\d+[A-Z]?[\.:\s]+.+)'
    r'|'
    r'(?:SECTION\s+\d+[\.:\s]+.+)'
    r')$',
    re.IGNORECASE | re.MULTILINE
)

def chunk_financial_document(text: str, max_chunk_size: int = 800) -> list[dict]:
    """
    Table-aware financial document chunker.
    
    1. Extracts HTML/Markdown tables as atomic chunks (chunk_type="table").
    2. Splits non-table text by semantic section headers (#, ##, Item 1A, etc.).
    3. Keeps parent section context for each chunk.
    
    Returns a list of dicts:
    [
        {
            "parent_section": "Item 1A. Risk Factors",
            "content": "...",
            "chunk_type": "text" | "table"
        }
    ]
    """
    if not text or not text.strip():
        return []

    chunks = []
    current_section = "General"

    # Find all table regions in text
    table_matches = list(TABLE_PATTERN.finditer(text))
    
    last_idx = 0
    segments = []

    for match in table_matches:
        start, end = match.span()
        if start > last_idx:
            # Non-table text segment before table
            segments.append(("text", text[last_idx:start]))
        # Table text segment
        segments.append(("table", match.group(0)))
        last_idx = end

    if last_idx < len(text):
        segments.append(("text", text[last_idx:]))

    # Process segments
    for seg_type, seg_content in segments:
        if seg_type == "table":
            clean_table = seg_content.strip()
            if clean_table:
                chunks.append({
                    "parent_section": current_section,
                    "content": clean_table,
                    "chunk_type": "table"
                })
        else:
            # Process non-table prose block line-by-line / section-by-section
            lines = seg_content.splitlines(keepends=True)
            prose_buffer = []

            for line in lines:
                header_match = HEADER_PATTERN.match(line.strip())
                if header_match:
                    # Flush accumulated prose under previous section
                    if prose_buffer:
                        flushed_text = "".join(prose_buffer).strip()
                        if flushed_text:
                            _split_and_append_prose(chunks, current_section, flushed_text, max_chunk_size)
                        prose_buffer = []

                    # Update active section header
                    matched_hdr = line.strip().lstrip("#").strip()
                    current_section = matched_hdr if matched_hdr else "General"
                else:
                    prose_buffer.append(line)

            # Flush remaining buffer
            if prose_buffer:
                flushed_text = "".join(prose_buffer).strip()
                if flushed_text:
                    _split_and_append_prose(chunks, current_section, flushed_text, max_chunk_size)

    logger.info(f"Chunked document into {len(chunks)} chunks ({sum(1 for c in chunks if c['chunk_type'] == 'table')} tables, {sum(1 for c in chunks if c['chunk_type'] == 'text')} text).")
    return chunks


def _split_and_append_prose(chunks: list[dict], section: str, text: str, max_chunk_size: int):
    """
    Helper function to split long prose blocks into paragraph-sized chunks.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    
    current_chunk = []
    current_length = 0

    for para in paragraphs:
        if current_length + len(para) > max_chunk_size and current_chunk:
            combined = "\n\n".join(current_chunk)
            chunks.append({
                "parent_section": section,
                "content": combined,
                "chunk_type": "text"
            })
            current_chunk = [para]
            current_length = len(para)
        else:
            current_chunk.append(para)
            current_length += len(para)

    if current_chunk:
        combined = "\n\n".join(current_chunk)
        chunks.append({
            "parent_section": section,
            "content": combined,
            "chunk_type": "text"
        })
