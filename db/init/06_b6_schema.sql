-- B6 — AI Operations Manager (capstone)
-- One top-level Supervisor routes any incoming request to the right
-- specialist system below it (B1/B2/B4/B5). Doesn't reimplement their
-- logic — invokes their existing compiled graphs directly.

CREATE TABLE IF NOT EXISTS b6_routed_requests (
    id                  SERIAL PRIMARY KEY,
    request_text        TEXT NOT NULL,
    incoming_type       TEXT,          -- 'lead' | 'ticket' | 'question' | 'report'
    routed_to_project   TEXT,          -- e.g. 'B2 — Lead Scoring'
    outcome_summary     TEXT,
    outcome_detail      JSONB NOT NULL DEFAULT '{}',
    latency_ms          INT,
    trace               JSONB NOT NULL DEFAULT '[]',
    is_public_demo      BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
