-- B2 — Multi-Agent Lead Qualification & Scoring
-- Reuses job-market-pipeline's max(llm_score, formula_score) hybrid pattern,
-- pointed at leads instead of job postings.

CREATE TABLE IF NOT EXISTS b2_leads_scored (
    id                SERIAL PRIMARY KEY,
    lead_raw          TEXT NOT NULL,           -- the raw CSV row / pasted text as submitted
    name              TEXT,
    company           TEXT,
    email             TEXT,
    target_profile    TEXT NOT NULL,           -- the target-customer profile this lead was scored against
    enrichment_data   JSONB NOT NULL DEFAULT '{}',  -- {summary, industry, estimated_size, source_urls}
    llm_score         REAL,
    llm_reasoning     TEXT,
    formula_score     REAL,
    formula_reasoning TEXT,
    final_score       REAL,                    -- max(llm_score, formula_score)
    outreach_draft    TEXT,
    trace             JSONB NOT NULL DEFAULT '[]',  -- full agent reasoning trail, same shape as B1's
    is_public_demo    BOOLEAN NOT NULL DEFAULT false,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS b2_leads_scored_final_score_idx ON b2_leads_scored (final_score DESC);
