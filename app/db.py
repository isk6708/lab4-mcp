"""Database engine, models and a small SQLite migration."""
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text,
                        create_engine, event, inspect, text)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(DATA_DIR / "uploads")))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/app_platform.db")

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _):
    if DATABASE_URL.startswith("sqlite"):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")   # enforce ON DELETE CASCADE
        dbapi_connection.execute("PRAGMA busy_timeout=5000")  # background sync + requests share the file


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


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
    attachments = relationship("AttachmentDB", back_populates="product", cascade="all, delete-orphan")


class AttachmentDB(Base):
    """A PDF specification document linked to a product."""
    __tablename__ = "product_attachments"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False)
    original_filename = Column(String, nullable=False)
    stored_filename = Column(String, unique=True, nullable=False)
    content_type = Column(String, default="application/pdf")
    size_bytes = Column(Integer)
    sha256 = Column(String, index=True)
    page_count = Column(Integer, default=0)
    extracted_text = Column(Text, default="")
    status = Column(String, default="processed")  # processed | no_text
    uploaded_at = Column(DateTime(timezone=True), default=utcnow)
    product = relationship("ProductDB", back_populates="attachments")
    chunks = relationship("AttachmentChunkDB", back_populates="attachment",
                          cascade="all, delete-orphan", order_by="AttachmentChunkDB.chunk_index")


class AttachmentChunkDB(Base):
    """A retrievable piece of a spec PDF."""
    __tablename__ = "attachment_chunks"
    id = Column(Integer, primary_key=True, index=True)
    attachment_id = Column(Integer, ForeignKey("product_attachments.id", ondelete="CASCADE"), index=True, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    page_number = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    char_count = Column(Integer)
    embedding = Column(LargeBinary, nullable=True)     # float32 vector
    embedding_model = Column(String, nullable=True)    # which model produced it
    attachment = relationship("AttachmentDB", back_populates="chunks")


class KnowledgeDocDB(Base):
    """A Markdown knowledge document (runbook, postmortem, blueprint) from the knowledge/ folder."""
    __tablename__ = "knowledge_documents"
    id = Column(Integer, primary_key=True, index=True)
    source_path = Column(String, unique=True, nullable=False)  # e.g. runbooks/crashloopbackoff.md
    title = Column(String, nullable=False)
    doc_type = Column(String, default="document")
    owner = Column(String, default="")
    tags = Column(String, default="")  # comma-separated
    last_updated = Column(String, default="")
    sha256 = Column(String, nullable=False)
    content = Column(Text, default="")
    synced_at = Column(DateTime(timezone=True), default=utcnow)
    chunks = relationship("KnowledgeChunkDB", back_populates="document",
                          cascade="all, delete-orphan", order_by="KnowledgeChunkDB.chunk_index")


class KnowledgeChunkDB(Base):
    __tablename__ = "knowledge_chunks"
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    section = Column(String, default="")
    text = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=True)
    embedding_model = Column(String, nullable=True)
    document = relationship("KnowledgeDocDB", back_populates="chunks")


def get_db():
    """FastAPI dependency: one database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """create_all() never alters existing tables; add columns introduced after the first release."""
    wanted = {"attachment_chunks": {"embedding": "BLOB", "embedding_model": "VARCHAR"}}
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if table not in insp.get_table_names():
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
