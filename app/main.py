import hashlib
import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

try:  # local runs read the project's .env; real environment variables (Docker, Kubernetes) win
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from passlib.context import CryptContext  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import (UPLOAD_DIR, AttachmentChunkDB, AttachmentDB, KnowledgeDocDB, ProductDB,  # noqa: E402
                    SessionLocal, UserDB, init_db)
from app.embeddings import embedder  # noqa: E402
from app.knowledge_service import (CHUNK_OVERLAP, CHUNK_SIZE, SOURCES, embed_missing,  # noqa: E402
                                   knowledge_status, retrieve, spec_embedding_text, sync_knowledge)
from app.pdf_utils import chunk_pages, extract_pages, is_pdf  # noqa: E402
from app.vectors import pack  # noqa: E402

JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-secret")  # used by the upcoming /token endpoint
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
SYNC_ON_STARTUP = os.getenv("KNOWLEDGE_SYNC_ON_STARTUP", "true").lower() == "true"

log = logging.getLogger("lab4")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

init_db()


# ---------------------------------------------------------------------------
# API schemas
# ---------------------------------------------------------------------------
class ProductBase(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    stock: int


class ProductCreate(ProductBase):
    pass


class ProductResponse(ProductBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class AttachmentResponse(BaseModel):
    id: int
    product_id: int
    original_filename: str
    size_bytes: int
    sha256: str
    page_count: int
    chunk_count: int
    embedded_chunks: int
    status: str
    uploaded_at: datetime
    text_preview: str


class ProductDetailResponse(ProductResponse):
    attachments: List[AttachmentResponse] = []


class ChunkResponse(BaseModel):
    id: int
    chunk_index: int
    page_number: int
    char_count: int
    embedded: bool
    text: str


class SearchHit(BaseModel):
    product_id: int
    product_name: str
    attachment_id: int
    filename: str
    page_number: int
    chunk_id: int
    snippet: str


class KnowledgeDocResponse(BaseModel):
    id: int
    source_path: str
    title: str
    doc_type: str
    owner: str
    tags: List[str]
    last_updated: str
    chunk_count: int
    embedded_chunks: int


def to_attachment_response(a: AttachmentDB) -> AttachmentResponse:
    return AttachmentResponse(
        id=a.id, product_id=a.product_id, original_filename=a.original_filename,
        size_bytes=a.size_bytes, sha256=a.sha256, page_count=a.page_count,
        chunk_count=len(a.chunks), embedded_chunks=sum(1 for c in a.chunks if c.embedding_model == embedder.model),
        status=a.status, uploaded_at=a.uploaded_at, text_preview=(a.extracted_text or "")[:300],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_product_or_404(db: Session, product_id: int) -> ProductDB:
    product = db.get(ProductDB, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


def get_attachment_or_404(db: Session, attachment_id: int) -> AttachmentDB:
    attachment = db.get(AttachmentDB, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return attachment


def remove_file(stored_filename: str) -> None:
    try:
        (UPLOAD_DIR / stored_filename).unlink(missing_ok=True)
    except OSError:
        pass


def seed_fake_user():
    db = SessionLocal()
    try:
        if not db.query(UserDB).filter(UserDB.username == "admin").first():
            db.add(UserDB(username="admin", hashed_password=pwd_context.hash("123456"), is_active=True))
            db.commit()
            print("🚀 Platform Setup: Fake user 'admin' seeded with encrypted password '123456'.")
    finally:
        db.close()


def _background_sync():
    """Load knowledge/ and embed missing chunks without delaying startup or health checks."""
    db = SessionLocal()
    try:
        stats = sync_knowledge(db)
        log.info("Knowledge sync: %s", stats)
    except Exception:  # noqa: BLE001 - never crash the API because of the knowledge base
        log.exception("Knowledge sync failed")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed_fake_user()
    if SYNC_ON_STARTUP:
        threading.Thread(target=_background_sync, name="knowledge-sync", daemon=True).start()
    yield


app = FastAPI(title="Lab4 Platform Engineering Local Testbed", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health_check():
    return {"status": "healthy", "workspace": "lab4-platform-engineering"}


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
@app.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    db_product = ProductDB(**product.model_dump())
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product


@app.get("/products", response_model=List[ProductResponse])
def read_products(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(ProductDB).offset(skip).limit(limit).all()


@app.get("/products/{product_id}", response_model=ProductDetailResponse)
def read_product(product_id: int, db: Session = Depends(get_db)):
    p = get_product_or_404(db, product_id)
    return ProductDetailResponse(
        id=p.id, name=p.name, description=p.description, price=p.price, stock=p.stock,
        attachments=[to_attachment_response(a) for a in p.attachments],
    )


@app.put("/products/{product_id}", response_model=ProductResponse)
def update_product(product_id: int, updated_product: ProductCreate, db: Session = Depends(get_db)):
    db_product = get_product_or_404(db, product_id)
    for key, value in updated_product.model_dump().items():
        setattr(db_product, key, value)
    db.commit()
    db.refresh(db_product)
    return db_product


@app.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    db_product = get_product_or_404(db, product_id)
    files = [a.stored_filename for a in db_product.attachments]
    db.delete(db_product)  # cascades to attachments and chunks
    db.commit()
    for f in files:
        remove_file(f)
    return None


# ---------------------------------------------------------------------------
# Product attachments (PDF specification sheets)
# ---------------------------------------------------------------------------
@app.post("/products/{product_id}/attachments", response_model=AttachmentResponse,
          status_code=status.HTTP_201_CREATED)
async def upload_attachment(product_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    product = get_product_or_404(db, product_id)

    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File larger than {MAX_UPLOAD_MB} MB")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if not is_pdf(data):
        raise HTTPException(status_code=415, detail="Only PDF files are accepted")

    sha256 = hashlib.sha256(data).hexdigest()
    duplicate = db.query(AttachmentDB).filter(
        AttachmentDB.product_id == product_id, AttachmentDB.sha256 == sha256).first()
    if duplicate:
        raise HTTPException(status_code=409,
                            detail=f"This PDF is already attached to the product (attachment {duplicate.id})")

    try:
        pages = extract_pages(data)
    except Exception as exc:  # corrupt or encrypted PDF
        raise HTTPException(status_code=422, detail=f"Could not read PDF: {exc}") from exc

    filename = Path(file.filename or "specification.pdf").name
    full_text = "\n\n".join(f"[page {i}]\n{t}" for i, t in enumerate(pages, start=1) if t)
    chunks = chunk_pages(pages, CHUNK_SIZE, CHUNK_OVERLAP)
    chunk_rows = [AttachmentChunkDB(chunk_index=c.chunk_index, page_number=c.page_number,
                                    text=c.text, char_count=len(c.text)) for c in chunks]

    # Embed now so the spec is searchable by meaning immediately (best effort:
    # if the provider is down, chunks are stored and embedded later by /knowledge/sync)
    vectors = await run_in_threadpool(
        embedder.embed, [spec_embedding_text(product.name, filename, c.page_number, c.text) for c in chunks])
    if vectors:
        for row, vector in zip(chunk_rows, vectors):
            row.embedding, row.embedding_model = pack(vector), embedder.model

    stored_filename = f"{uuid.uuid4().hex}.pdf"
    (UPLOAD_DIR / stored_filename).write_bytes(data)

    attachment = AttachmentDB(
        product_id=product_id, original_filename=filename, stored_filename=stored_filename,
        content_type="application/pdf", size_bytes=len(data), sha256=sha256, page_count=len(pages),
        extracted_text=full_text, status="processed" if chunks else "no_text", chunks=chunk_rows,
    )
    try:
        db.add(attachment)
        db.commit()
    except Exception:
        db.rollback()
        remove_file(stored_filename)
        raise
    db.refresh(attachment)
    return to_attachment_response(attachment)


@app.get("/products/{product_id}/attachments", response_model=List[AttachmentResponse])
def list_attachments(product_id: int, db: Session = Depends(get_db)):
    product = get_product_or_404(db, product_id)
    return [to_attachment_response(a) for a in product.attachments]


@app.get("/attachments/{attachment_id}", response_model=AttachmentResponse)
def read_attachment(attachment_id: int, db: Session = Depends(get_db)):
    return to_attachment_response(get_attachment_or_404(db, attachment_id))


@app.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int, db: Session = Depends(get_db)):
    a = get_attachment_or_404(db, attachment_id)
    path = UPLOAD_DIR / a.stored_filename
    if not path.exists():
        raise HTTPException(status_code=410, detail="File missing from storage")
    return FileResponse(path, media_type="application/pdf", filename=a.original_filename)


@app.get("/attachments/{attachment_id}/chunks", response_model=List[ChunkResponse])
def read_attachment_chunks(attachment_id: int, db: Session = Depends(get_db)):
    """The text pieces that RAG embeds and retrieves."""
    return [ChunkResponse(id=c.id, chunk_index=c.chunk_index, page_number=c.page_number,
                          char_count=c.char_count or len(c.text), embedded=c.embedding_model == embedder.model,
                          text=c.text)
            for c in get_attachment_or_404(db, attachment_id).chunks]


@app.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(attachment_id: int, db: Session = Depends(get_db)):
    a = get_attachment_or_404(db, attachment_id)
    stored = a.stored_filename
    db.delete(a)
    db.commit()
    remove_file(stored)
    return None


# ---------------------------------------------------------------------------
# Keyword search over spec sheets (exact words)
# ---------------------------------------------------------------------------
@app.get("/search", response_model=List[SearchHit])
def search_specifications(q: str = Query(..., min_length=2),
                          product_id: Optional[int] = None,
                          limit: int = Query(5, ge=1, le=50),
                          db: Session = Depends(get_db)):
    query = (db.query(AttachmentChunkDB, AttachmentDB, ProductDB)
             .join(AttachmentDB, AttachmentChunkDB.attachment_id == AttachmentDB.id)
             .join(ProductDB, AttachmentDB.product_id == ProductDB.id)
             .filter(AttachmentChunkDB.text.ilike(f"%{q}%")))
    if product_id is not None:
        query = query.filter(ProductDB.id == product_id)

    hits = []
    for chunk, attachment, product in query.limit(limit).all():
        pos = chunk.text.lower().find(q.lower())
        start = max(pos - 120, 0)
        hits.append(SearchHit(
            product_id=product.id, product_name=product.name,
            attachment_id=attachment.id, filename=attachment.original_filename,
            page_number=chunk.page_number, chunk_id=chunk.id,
            snippet=("…" if start else "") + chunk.text[start:pos + len(q) + 200],
        ))
    return hits


# ---------------------------------------------------------------------------
# Organizational knowledge + semantic retrieval (Chapter 4: RAG components 1-4)
# ---------------------------------------------------------------------------
@app.get("/retrieve")
def retrieve_knowledge(q: str = Query(..., min_length=2, description="Question in plain language"),
                       top_k: int = Query(5, ge=1, le=20),
                       product_id: Optional[int] = Query(None, description="Only this product's spec sheets"),
                       source: Literal["all", "specs", "knowledge"] = "all",
                       doc_type: Optional[str] = Query(None, description="runbook, postmortem or blueprint"),
                       min_score: float = Query(0.0, ge=0.0, le=1.0),
                       db: Session = Depends(get_db)):
    """Retriever: ranked chunks from spec PDFs and the knowledge base, with citations,
    relevance scores and last-updated dates. Uses vector (meaning) search when embeddings
    are available, otherwise keyword scoring. The assistant's RAG mode builds answers from this."""
    assert source in SOURCES
    return retrieve(db, q, top_k=top_k, product_id=product_id, source=source,
                    doc_type=doc_type, min_score=min_score)


@app.get("/knowledge", response_model=List[KnowledgeDocResponse])
def list_knowledge(db: Session = Depends(get_db)):
    """Documents loaded from the knowledge/ folder (runbooks, postmortems, blueprints)."""
    return [KnowledgeDocResponse(
        id=d.id, source_path=d.source_path, title=d.title, doc_type=d.doc_type, owner=d.owner,
        tags=[t for t in (d.tags or "").split(",") if t], last_updated=d.last_updated,
        chunk_count=len(d.chunks), embedded_chunks=sum(1 for c in d.chunks if c.embedding_model == embedder.model))
        for d in db.query(KnowledgeDocDB).order_by(KnowledgeDocDB.doc_type, KnowledgeDocDB.title).all()]


@app.get("/knowledge/status")
def get_knowledge_status(db: Session = Depends(get_db)):
    """Embedding provider and how many chunks have vectors from the current model."""
    return knowledge_status(db)


@app.post("/knowledge/sync")
def sync_knowledge_now(db: Session = Depends(get_db)):
    """Re-read knowledge/*.md (changed files only) and embed every chunk that lacks a current vector."""
    return sync_knowledge(db)


@app.post("/knowledge/reembed")
def reembed_all(db: Session = Depends(get_db)):
    """Embed chunks that have no vector or a vector from another model (after changing EMBEDDING_MODEL)."""
    return embed_missing(db)
