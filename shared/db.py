"""Sync SQLAlchemy engine + pgvector similarity search — shared by every project's
tab.py and by scripts/sync_to_cloud.py. Uses NullPool + AUTOCOMMIT deliberately:
job-market-pipeline's dashboard hit a stale-cached-connection bug from pooled
reads serving snapshots from before a pipeline run completed (see its case study,
Section 7) — same fix applied here from day one instead of rediscovering it.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from shared.config import settings


def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or settings.database_url
    return create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")


def _vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"


def similarity_search(
    engine: Engine,
    table_prefix: str,
    query_embedding: list[float],
    top_k: int,
    doc_type: str | None = None,
    exclude_chunk_ids: list[int] | None = None,
) -> list[dict]:
    """Cosine-distance nearest-neighbor search over <prefix>_chunks joined to
    <prefix>_documents. Used by B1 directly, and by B3/B4 which reuse B1's
    retrieval stack against their own document sets."""
    vec = _vector_literal(query_embedding)
    exclude_chunk_ids = exclude_chunk_ids or []

    query = text(
        f"""
        SELECT c.id, c.document_id, c.content, c.chunk_index,
               d.filename, d.doc_type,
               1 - (c.embedding <=> (:vec)::vector) AS similarity
        FROM {table_prefix}_chunks c
        JOIN {table_prefix}_documents d ON d.id = c.document_id
        WHERE (:doc_type IS NULL OR d.doc_type = :doc_type)
          AND NOT (c.id = ANY(:excluded))
        ORDER BY c.embedding <=> (:vec)::vector
        LIMIT :top_k
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            query,
            {"vec": vec, "doc_type": doc_type, "excluded": exclude_chunk_ids, "top_k": top_k},
        ).mappings().all()
    return [dict(r) for r in rows]
