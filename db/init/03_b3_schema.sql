-- B3 — Business Process Automation (Approval Workflow)
-- Scoped to one real workflow end to end (leave/PTO requests) rather than a
-- generic form builder. Policy-Check reuses B1's document set directly
-- (b1_documents/b1_chunks, doc_type='policy') instead of a separate ingestion
-- system — same retrieval stack, same knowledge base.

CREATE TABLE IF NOT EXISTS b3_approval_requests (
    id                SERIAL PRIMARY KEY,
    thread_id         TEXT NOT NULL,           -- LangGraph checkpointer thread id, for resuming a paused run
    request_text      TEXT NOT NULL,
    request_type      TEXT,                    -- 'leave' | 'procurement' | 'expense' | 'other'
    requested_days    REAL,
    policy_summary    TEXT,
    policy_citations  JSONB NOT NULL DEFAULT '[]',
    approver          TEXT,                    -- who the Approver-Router decided should approve
    status            TEXT NOT NULL DEFAULT 'pending_approval',  -- pending_approval | approved | rejected
    approver_note     TEXT,
    notified          BOOLEAN NOT NULL DEFAULT false,
    notification_detail TEXT,
    trace             JSONB NOT NULL DEFAULT '[]',
    is_public_demo    BOOLEAN NOT NULL DEFAULT false,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS b3_approval_requests_status_idx ON b3_approval_requests (status);
