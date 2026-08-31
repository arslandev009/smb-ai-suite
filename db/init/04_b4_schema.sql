-- B4 — Customer Support Triage
-- Knowledge Retrieval reuses B1's document set directly (b1_documents/
-- b1_chunks) rather than a separate ingestion system, same pattern as B3's
-- Policy-Check. Core skill here is escalation/guardrail logic, not a new
-- retrieval mechanism.

CREATE TABLE IF NOT EXISTS b4_support_tickets (
    id                  SERIAL PRIMARY KEY,
    ticket_text         TEXT NOT NULL,
    category            TEXT,                       -- 'billing' | 'technical' | 'account' | 'other'
    urgency             TEXT,                        -- 'low' | 'medium' | 'high'
    citations           JSONB NOT NULL DEFAULT '[]',
    drafted_response    TEXT,
    escalated           BOOLEAN NOT NULL DEFAULT false,
    escalation_reason   TEXT,
    keyword_flagged     BOOLEAN NOT NULL DEFAULT false,  -- true if the hard-coded sensitive-keyword rule fired
    status              TEXT NOT NULL DEFAULT 'auto_resolved',  -- 'auto_resolved' | 'escalated'
    trace               JSONB NOT NULL DEFAULT '[]',
    is_public_demo      BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS b4_support_tickets_status_idx ON b4_support_tickets (status);
