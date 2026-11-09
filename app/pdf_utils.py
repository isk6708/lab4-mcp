"""PDF text extraction and chunking.

Kept separate from the API so it can be tested on its own and later reused by
an embedding job. The chunks it produces are what a RAG pipeline will embed.
"""
import io
import re
from dataclasses import dataclass
from typing import List

from pypdf import PdfReader

PDF_MAGIC = b"%PDF-"


@dataclass
class Chunk:
    page_number: int  # 1-based, so answers can cite "page 2"
    chunk_index: int  # position across the whole document
    text: str


def is_pdf(data: bytes) -> bool:
    """Check the file signature, not just the filename or content type."""
    return data[:1024].lstrip().startswith(PDF_MAGIC)


def extract_pages(data: bytes) -> List[str]:
    """Return the text of each page (empty string for pages with no text layer)."""
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        pages.append(text)
    return pages


def chunk_pages(pages: List[str], chunk_size: int = 800, overlap: int = 120) -> List[Chunk]:
    """Split page text into overlapping chunks that never cross a page boundary.

    Character-based with a soft break on whitespace. Good enough for spec sheets;
    a later lab can swap in token- or heading-aware chunking.
    """
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    chunks: List[Chunk] = []
    index = 0
    for page_no, text in enumerate(pages, start=1):
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            if end < len(text):
                space = text.rfind(" ", start + chunk_size // 2, end)
                if space != -1:
                    end = space
            piece = text[start:end].strip()
            if piece:
                chunks.append(Chunk(page_number=page_no, chunk_index=index, text=piece))
                index += 1
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
    return chunks
