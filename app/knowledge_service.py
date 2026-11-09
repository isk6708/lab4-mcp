"""Organizational knowledge and retrieval (Chapter 4's RAG components 1-4).

  1. Knowledge sources : product spec PDFs (uploaded) + Markdown in knowledge/ (runbooks, postmortems, blueprints)
  2. Ingestion         : chunking (pdf_utils / knowledge.py) + embeddings (embeddings.py)
  3. Vector store      : float32 vectors in SQLite next to each chunk
  4. Retriever         : retrieve() below, cosine similarity, or keyword scoring when no vectors are available

Components 5 (LLM) and 6 (orchestration) live in the assistant (assistant/rag.py).
"""
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import AttachmentChunkDB, AttachmentDB, KnowledgeChunkDB, KnowledgeDocDB, ProductDB
from app.embeddings import embedder
from app.knowledge import chunk_markdown, parse_front_matter
from app.vectors import pack, rank, unpack

KNOWLEDGE_DIR = Path(os.getenv("KNOWLEDGE_DIR", str(Path(__file__).resolve().parent.parent / "knowledge")))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))
SOURCES = ("all", "specs", "knowledge")


# ---------------------------------------------------------------------------
# Text that gets embedded: chunk text plus a short header for context
# ---------------------------------------------------------------------------
def spec_embedding_text(product_name: str, filename: str, page: int, text: str) -> str:
    return f"Product: {product_name}\nSpecification: {filename}, page {page}\n{text}"


def knowledge_embedding_text(title: str, section: str, text: str) -> str:
    return f"{title}\n{section}\n{text}"


def _spec_text(chunk: AttachmentChunkDB) -> str:
    a = chunk.attachment
    return spec_embedding_text(a.product.name, a.original_filename, chunk.page_number, chunk.text)


def _knowledge_text(chunk: KnowledgeChunkDB) -> str:
    return knowledge_embedding_text(chunk.document.title, chunk.section, chunk.text)


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
def embed_missing(db: Session, batch: int = 64) -> dict:
    """Embed every chunk that has no vector yet, or one made by a different model."""
    if not embedder.enabled:
        return {"embedded": 0, "skipped": f"embeddings disabled (EMBEDDING_PROVIDER={embedder.provider})"}
    embedder.reset_backoff()
    done = 0
    for model_cls, build in ((AttachmentChunkDB, _spec_text), (KnowledgeChunkDB, _knowledge_text)):
        while True:
            rows = (db.query(model_cls)
                    .filter(or_(model_cls.embedding.is_(None), model_cls.embedding_model != embedder.model))
                    .limit(batch).all())
            if not rows:
                break
            vectors = embedder.embed([build(r) for r in rows])
            if vectors is None:
                db.commit()
                return {"embedded": done, "error": embedder.last_error}
            for row, vector in zip(rows, vectors):
                row.embedding, row.embedding_model = pack(vector), embedder.model
            db.commit()
            done += len(rows)
    return {"embedded": done}


# ---------------------------------------------------------------------------
# Knowledge folder sync (runbooks, postmortems, blueprints)
# ---------------------------------------------------------------------------
def _title_from(meta: dict, body: str, path: Path) -> str:
    if meta.get("title"):
        return str(meta["title"])
    heading = re.search(r"^#\s+(.+)$", body, re.M)
    return heading.group(1).strip() if heading else path.stem.replace("-", " ")


def sync_knowledge(db: Session, embed: bool = True) -> dict:
    """Load knowledge/*.md into the database. Unchanged files (same SHA-256) are skipped,
    changed files are re-chunked, deleted files are removed. Files starting with _ are ignored."""
    stats = {"folder": str(KNOWLEDGE_DIR), "added": 0, "updated": 0, "unchanged": 0, "removed": 0}
    if not KNOWLEDGE_DIR.is_dir():
        stats["warning"] = "knowledge folder not found"
        return stats

    seen = set()
    for path in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        if path.name.startswith("_"):
            continue
        rel = path.relative_to(KNOWLEDGE_DIR).as_posix()
        seen.add(rel)
        raw = path.read_text(encoding="utf-8", errors="replace")
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        doc = db.query(KnowledgeDocDB).filter(KnowledgeDocDB.source_path == rel).first()
        if doc and doc.sha256 == sha:
            stats["unchanged"] += 1
            continue

        meta, body = parse_front_matter(raw)
        title = _title_from(meta, body, path)
        tags = meta.get("tags", "")
        fields = dict(
            title=title,
            doc_type=str(meta.get("type") or path.parent.name.rstrip("s") or "document"),
            owner=str(meta.get("owner", "")),
            tags=",".join(tags) if isinstance(tags, list) else str(tags),
            last_updated=str(meta.get("last_updated") or datetime.fromtimestamp(path.stat().st_mtime).date()),
            sha256=sha, content=body, synced_at=datetime.now().astimezone(),
        )
        chunks = [KnowledgeChunkDB(chunk_index=c.chunk_index, section=c.section, text=c.text)
                  for c in chunk_markdown(body, title, CHUNK_SIZE, CHUNK_OVERLAP)]
        if doc:
            for key, value in fields.items():
                setattr(doc, key, value)
            doc.chunks = chunks  # old chunks are deleted (delete-orphan)
            stats["updated"] += 1
        else:
            db.add(KnowledgeDocDB(source_path=rel, chunks=chunks, **fields))
            stats["added"] += 1

    for doc in db.query(KnowledgeDocDB).all():
        if doc.source_path not in seen:
            db.delete(doc)
            stats["removed"] += 1
    db.commit()

    if embed:
        stats["embedding"] = embed_missing(db)
    return stats


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def _date(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value or "")


def _candidates(db: Session, source: str, product_id: Optional[int], doc_type: Optional[str]) -> List[dict]:
    items: List[dict] = []
    if source in ("all", "specs"):
        q = (db.query(AttachmentChunkDB, AttachmentDB, ProductDB)
             .join(AttachmentDB, AttachmentChunkDB.attachment_id == AttachmentDB.id)
             .join(ProductDB, AttachmentDB.product_id == ProductDB.id))
        if product_id is not None:
            q = q.filter(ProductDB.id == product_id)
        for chunk, att, product in q.all():
            items.append({
                "source_type": "product_spec", "title": f"{product.name} specification",
                "citation": f"{att.original_filename}, page {chunk.page_number}",
                "product_id": product.id, "product_name": product.name, "attachment_id": att.id,
                "document_id": None, "source_path": att.original_filename,
                "page_number": chunk.page_number, "section": None, "chunk_id": chunk.id,
                "last_updated": _date(att.uploaded_at), "text": chunk.text,
                "_match_text": spec_embedding_text(product.name, att.original_filename, chunk.page_number, chunk.text),
                "_vector": unpack(chunk.embedding) if chunk.embedding_model == embedder.model else None,
            })
    if source in ("all", "knowledge") and product_id is None:
        q = db.query(KnowledgeChunkDB, KnowledgeDocDB).join(KnowledgeDocDB, KnowledgeChunkDB.document_id == KnowledgeDocDB.id)
        if doc_type:
            q = q.filter(KnowledgeDocDB.doc_type == doc_type)
        for chunk, doc in q.all():
            items.append({
                "source_type": doc.doc_type, "title": doc.title,
                "citation": f"{doc.source_path} > {chunk.section}" if chunk.section else doc.source_path,
                "product_id": None, "product_name": None, "attachment_id": None,
                "document_id": doc.id, "source_path": doc.source_path,
                "page_number": None, "section": chunk.section, "chunk_id": chunk.id,
                "last_updated": doc.last_updated, "owner": doc.owner, "text": chunk.text,
                "_match_text": knowledge_embedding_text(doc.title, chunk.section, chunk.text),
                "_vector": unpack(chunk.embedding) if chunk.embedding_model == embedder.model else None,
            })
    return items


def retrieve(db: Session, question: str, top_k: int = 5, product_id: Optional[int] = None,
             source: str = "all", doc_type: Optional[str] = None, min_score: float = 0.0) -> dict:
    items = _candidates(db, source, product_id, doc_type)
    query_vector = embedder.embed_query(question) if any(i["_vector"] is not None for i in items) else None
    ranked = rank(question, items, query_vector, top_k, min_score)
    ranked.update(question=question, embedding_model=embedder.model if ranked["mode"] == "vector" else None,
                  embedding_error=embedder.last_error or None)
    if ranked["mode"] == "keyword" and embedder.enabled:
        ranked["note"] = ("Keyword fallback: no vectors available or the embedding provider is unreachable. "
                          "Pull the model (ollama pull nomic-embed-text) and run POST /knowledge/sync.")
    return ranked


def knowledge_status(db: Session) -> dict:
    def counts(model_cls):
        total = db.query(model_cls).count()
        embedded = db.query(model_cls).filter(model_cls.embedding_model == embedder.model).count()
        return {"chunks": total, "embedded_with_current_model": embedded}
    return {
        "embedding": embedder.status(),
        "knowledge_folder": str(KNOWLEDGE_DIR),
        "knowledge_documents": db.query(KnowledgeDocDB).count(),
        "knowledge_chunks": counts(KnowledgeChunkDB),
        "spec_chunks": counts(AttachmentChunkDB),
    }
