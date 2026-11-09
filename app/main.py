import hashlib
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict
from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
                        create_engine, event)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

from app.pdf_utils import chunk_pages, extract_pages, is_pdf

# ---------------------------------------------------------------------------
# Configuration (values come from the Kubernetes Secret / env in the cluster)
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(DATA_DIR / "uploads")))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/app_platform.db")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-secret")  # used by the upcoming /token endpoint
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_foreign_keys(dbapi_connection, _):
    # SQLite ignores ON DELETE CASCADE unless this is switched on per connection
    if DATABASE_URL.startswith("sqlite"):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Database models
# ---------------------------------------------------------------------------
class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)


class ProductDB(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    description = Column(String)
    price = Column(Float)
    stock = Column(Integer)
    attachments = relationship("AttachmentDB", back_populates="product",
                               cascade="all, delete-orphan")


class AttachmentDB(Base):
    """A PDF specification document linked to a product."""
    __tablename__ = "product_attachments"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False)
    original_filename = Column(String, nullable=False)
    stored_filename = Column(String, unique=True, nullable=False)  # uuid-based name on disk
    content_type = Column(String, default="application/pdf")
    size_bytes = Column(Integer)
    sha256 = Column(String, index=True)
    page_count = Column(Integer, default=0)
    extracted_text = Column(Text, default="")  # full text, kept for re-chunking later
    status = Column(String, default="processed")  # processed | no_text (scanned PDF, needs OCR)
    uploaded_at = Column(DateTime(timezone=True), default=utcnow)
    product = relationship("ProductDB", back_populates="attachments")
    chunks = relationship("AttachmentChunkDB", back_populates="attachment",
                          cascade="all, delete-orphan", order_by="AttachmentChunkDB.chunk_index")


class AttachmentChunkDB(Base):
    """A retrievable piece of an attachment: this is the unit RAG will embed and search."""
    __tablename__ = "attachment_chunks"
    id = Column(Integer, primary_key=True, index=True)
    attachment_id = Column(Integer, ForeignKey("product_attachments.id", ondelete="CASCADE"),
                           index=True, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    page_number = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    char_count = Column(Integer)
    # RAG step (later lab): add an embedding column or push to a vector store keyed by this id.
    attachment = relationship("AttachmentDB", back_populates="chunks")


Base.metadata.create_all(bind=engine)


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
    text: str
    model_config = ConfigDict(from_attributes=True)


class SearchHit(BaseModel):
    product_id: int
    product_name: str
    attachment_id: int
    filename: str
    page_number: int
    chunk_id: int
    snippet: str


def to_attachment_response(a: AttachmentDB) -> AttachmentResponse:
    return AttachmentResponse(
        id=a.id, product_id=a.product_id, original_filename=a.original_filename,
        size_bytes=a.size_bytes, sha256=a.sha256, page_count=a.page_count,
        chunk_count=len(a.chunks), status=a.status, uploaded_at=a.uploaded_at,
        text_preview=(a.extracted_text or "")[:300],
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed_fake_user()
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
    get_product_or_404(db, product_id)

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

    full_text = "\n\n".join(f"[page {i}]\n{t}" for i, t in enumerate(pages, start=1) if t)
    chunks = chunk_pages(pages, CHUNK_SIZE, CHUNK_OVERLAP)

    stored_filename = f"{uuid.uuid4().hex}.pdf"
    (UPLOAD_DIR / stored_filename).write_bytes(data)

    attachment = AttachmentDB(
        product_id=product_id,
        original_filename=Path(file.filename or "specification.pdf").name,
        stored_filename=stored_filename,
        content_type="application/pdf",
        size_bytes=len(data),
        sha256=sha256,
        page_count=len(pages),
        extracted_text=full_text,
        status="processed" if chunks else "no_text",
        chunks=[AttachmentChunkDB(chunk_index=c.chunk_index, page_number=c.page_number,
                                  text=c.text, char_count=len(c.text)) for c in chunks],
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
    """The exact text pieces a RAG pipeline will embed."""
    return get_attachment_or_404(db, attachment_id).chunks


@app.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(attachment_id: int, db: Session = Depends(get_db)):
    a = get_attachment_or_404(db, attachment_id)
    stored = a.stored_filename
    db.delete(a)
    db.commit()
    remove_file(stored)
    return None


# ---------------------------------------------------------------------------
# Retrieval (keyword baseline; replaced by vector search in the RAG lab)
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
