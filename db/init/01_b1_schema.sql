-- B1 — Multi-Agent RAG Knowledge Assistant
-- All B1 tables are prefixed b1_ so they share this one database cleanly
-- alongside b2_, b3_, ... as each project gets built.

CREATE TABLE IF NOT EXISTS b1_documents (
    id            SERIAL PRIMARY KEY,
    filename      TEXT NOT NULL,
    doc_type      TEXT NOT NULL DEFAULT 'general',   -- 'hr' | 'product' | 'policy' | 'general'
    chunk_count   INT NOT NULL DEFAULT 0,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS b1_chunks (
    id            SERIAL PRIMARY KEY,
    document_id   INT NOT NULL REFERENCES b1_documents(id) ON DELETE CASCADE,
    content       TEXT NOT NULL,
    chunk_index   INT NOT NULL,
    embedding     VECTOR(768),   -- must match EMBEDDING_DIM in .env
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS b1_chunks_embedding_hnsw_idx
    ON b1_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS b1_chunks_document_id_idx ON b1_chunks (document_id);
CREATE INDEX IF NOT EXISTS b1_documents_doc_type_idx ON b1_documents (doc_type);

-- Every query the agent graph runs gets logged here — this is the table both
-- the local hub tab AND the public snapshot sync read from. This is the
-- "rag_queries" table referenced in the showcase plan (Section 7 of the doc).
CREATE TABLE IF NOT EXISTS b1_rag_queries (
    id                SERIAL PRIMARY KEY,
    question          TEXT NOT NULL,
    doc_type_routed   TEXT,
    trace             JSONB NOT NULL DEFAULT '[]',   -- [{agent, status, detail}, ...] full reasoning trail
    answer            TEXT NOT NULL,
    verified          BOOLEAN NOT NULL,
    retry_count       INT NOT NULL DEFAULT 0,
    citations         JSONB NOT NULL DEFAULT '[]',   -- [{filename, excerpt, similarity}, ...]
    is_public_demo    BOOLEAN NOT NULL DEFAULT false, -- flagged rows are what sync_to_cloud.py publishes
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
