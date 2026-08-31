"""File loading, chunking, and the embed+insert pipeline. Sync throughout —
this runs directly inside a Streamlit callback, no async event loop involved."""
import re
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader
from sqlalchemy import text
from sqlalchemy.engine import Engine

from shared.config import settings
from shared.llm_client import embed_batch

_CHARS_PER_TOKEN = 4


def load_text(filename: str, file_bytes: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(BytesIO(file_bytes))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix == ".docx":
        doc = DocxDocument(BytesIO(file_bytes))
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if suffix in (".md", ".txt"):
        return file_bytes.decode("utf-8", errors="ignore")
    raise ValueError(f"Unsupported file type: {suffix}. Supported: .pdf, .docx, .md, .txt")


def chunk_text(raw_text: str, chunk_size_tokens: int, overlap_tokens: int) -> list[str]:
    raw_text = re.sub(r"\n{3,}", "\n\n", raw_text).strip()
    if not raw_text:
        return []
    chunk_size_chars = chunk_size_tokens * _CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * _CHARS_PER_TOKEN
    step = max(chunk_size_chars - overlap_chars, 1)

    chunks, start = [], 0
    while start < len(raw_text):
        chunk = raw_text[start : start + chunk_size_chars].strip()
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks


def ingest_document(engine: Engine, filename: str, file_bytes: bytes, doc_type: str) -> tuple[int, int]:
    """Returns (document_id, chunks_created)."""
    raw = load_text(filename, file_bytes)
    chunks = chunk_text(raw, settings.chunk_size_tokens, settings.chunk_overlap_tokens)
    if not chunks:
        raise ValueError(f"No extractable text found in {filename}")

    embeddings = embed_batch(chunks)

    with engine.begin() as conn:
        document_id = conn.execute(
            text(
                "INSERT INTO b1_documents (filename, doc_type, chunk_count) "
                "VALUES (:filename, :doc_type, :chunk_count) RETURNING id"
            ),
            {"filename": filename, "doc_type": doc_type, "chunk_count": len(chunks)},
        ).scalar_one()

        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            vec_literal = "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"
            conn.execute(
                text(
                    "INSERT INTO b1_chunks (document_id, content, chunk_index, embedding) "
                    "VALUES (:document_id, :content, :chunk_index, (:embedding)::vector)"
                ),
                {"document_id": document_id, "content": chunk, "chunk_index": idx, "embedding": vec_literal},
            )

    return document_id, len(chunks)


def list_documents(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, filename, doc_type, chunk_count, ingested_at FROM b1_documents ORDER BY ingested_at DESC")
        ).mappings().all()
    return [dict(r) for r in rows]


def delete_document(engine: Engine, document_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM b1_documents WHERE id = :id"), {"id": document_id})
