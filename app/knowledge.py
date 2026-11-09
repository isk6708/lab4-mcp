"""Markdown knowledge documents: metadata header parsing and heading-aware chunking.

Pure functions (no database, no network) so they are easy to test.

Each document starts with a metadata header, the "consistent metadata header"
the book recommends for every knowledge artifact:

    ---
    title: Pod in CrashLoopBackOff
    type: runbook
    owner: platform-team
    last_updated: 2026-11-09
    tags: [kubernetes, pod]
    ---
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class TextChunk:
    chunk_index: int
    section: str  # heading path, e.g. "Pod in CrashLoopBackOff > Fix"
    text: str


def _clean_value(raw: str):
    value = raw.split(" #", 1)[0].strip()  # drop inline comments
    if value.startswith("[") and value.endswith("]"):
        return [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
    return value.strip("'\"")


def parse_front_matter(raw: str) -> Tuple[Dict, str]:
    """Split a document into (metadata dict, body). Missing header -> ({}, raw)."""
    text = raw.lstrip("﻿")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not match:
        return {}, text
    meta: Dict = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            key, value = line.split(":", 1)
            meta[key.strip().lower()] = _clean_value(value)
    return meta, text[match.end():]


def split_text(text: str, chunk_size: int = 800, overlap: int = 120) -> List[str]:
    """Character-based split with a soft break on whitespace (same rule as PDF chunking)."""
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    text = text.strip()
    pieces, start = [], 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            space = text.rfind(" ", start + chunk_size // 2, end)
            if space == -1:
                space = text.rfind("\n", start + chunk_size // 2, end)
            if space != -1:
                end = space
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def chunk_markdown(body: str, title: str = "", chunk_size: int = 800, overlap: int = 120) -> List[TextChunk]:
    """Split Markdown by headings so each chunk belongs to one section, then by size."""
    sections: List[Tuple[str, List[str]]] = []
    path: List[str] = []
    current: List[str] = []
    current_name = title
    in_code = False

    def flush():
        if any(line.strip() for line in current):
            sections.append((current_name, list(current)))

    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        heading = None if in_code else re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush()
            current = []
            level = len(heading.group(1))
            path = path[: level - 1] + [heading.group(2).strip()]
            current_name = " > ".join(path)
        else:
            current.append(line)
    flush()

    chunks: List[TextChunk] = []
    for name, lines in sections:
        for piece in split_text("\n".join(lines), chunk_size, overlap):
            chunks.append(TextChunk(chunk_index=len(chunks), section=name or title, text=piece))
    return chunks
