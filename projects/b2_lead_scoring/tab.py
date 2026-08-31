"""Renders B2's tab: a target-customer-profile input, CSV lead upload, and a
ranked results table with per-lead agent trace + outreach draft. Reuses the
dashboard pattern from job-market-pipeline / B1 rather than inventing a new one.
"""
import csv
import io
import json

import streamlit as st
from sqlalchemy import text

from projects.b2_lead_scoring.graph import build_graph

DEFAULT_PROFILE = (
    "B2B SaaS companies, 10-200 employees, tech or software industry, "
    "based in North America or Europe, showing signs of active growth."
)


def _init_state():
    st.session_state.setdefault("b2_results", [])


def _run_lead(lead_raw: str, target_profile: str, progress_placeholder) -> dict:
    graph = build_graph()
    initial_state = {"lead_raw": lead_raw, "target_profile": target_profile, "trace": []}

    final_state: dict = {}
    for event in graph.stream(initial_state, stream_mode="values"):
        final_state = event
        trace = event.get("trace", [])
        if trace:
            progress_placeholder.caption(f"→ {trace[-1]['agent']}: {trace[-1].get('detail', '')}")

    return final_state


def _log_lead(engine, lead_raw: str, target_profile: str, state: dict) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO b2_leads_scored
                    (lead_raw, name, company, email, target_profile, enrichment_data,
                     llm_score, llm_reasoning, formula_score, formula_reasoning,
                     final_score, outreach_draft, trace)
                VALUES
                    (:lead_raw, :name, :company, :email, :target_profile, :enrichment_data,
                     :llm_score, :llm_reasoning, :formula_score, :formula_reasoning,
                     :final_score, :outreach_draft, :trace)
                RETURNING id
                """
            ),
            {
                "lead_raw": lead_raw,
                "name": state.get("name"),
                "company": state.get("company"),
                "email": state.get("email"),
                "target_profile": target_profile,
                "enrichment_data": json.dumps({
                    "summary": state.get("enrichment_summary"),
                    "industry": state.get("enrichment_industry"),
                    "estimated_size": state.get("enrichment_size"),
                    "source_urls": state.get("enrichment_sources", []),
                }),
                "llm_score": state.get("llm_score"),
                "llm_reasoning": state.get("llm_reasoning"),
                "formula_score": state.get("formula_score"),
                "formula_reasoning": state.get("formula_reasoning"),
                "final_score": state.get("final_score"),
                "outreach_draft": state.get("outreach_draft"),
                "trace": json.dumps(state.get("trace", [])),
            },
        ).scalar_one()


def _mark_public(engine, lead_id: int):
    with engine.begin() as conn:
        conn.execute(text("UPDATE b2_leads_scored SET is_public_demo = true WHERE id = :id"), {"id": lead_id})


def render(engine):
    _init_state()

    st.markdown("### B2 · Multi-Agent Lead Qualification & Scoring")
    st.caption("Intake → Enrichment (real web search) → Scoring (hybrid) → Outreach Drafting, orchestrated by a Supervisor.")

    target_profile = st.text_area("Target customer profile", value=DEFAULT_PROFILE, height=80)

    st.markdown("**Leads**")
    csv_file = st.file_uploader(
        "Upload a CSV (any columns — each row is passed to Intake as-is)", type=["csv"]
    )
    manual_lead = st.text_area(
        "...or paste a single lead (name, company, email, notes — free text)", height=80,
        placeholder="Jane Doe, VP Eng at Acme Robotics (acme-robotics.com), jane@acme.io — mentioned they're scaling their support team",
    )

    rows: list[str] = []
    if csv_file is not None:
        content = csv_file.read().decode("utf-8", errors="ignore")
        reader = csv.DictReader(io.StringIO(content))
        for r in reader:
            rows.append(", ".join(f"{k}: {v}" for k, v in r.items() if v))
    if manual_lead.strip():
        rows.append(manual_lead.strip())

    if rows and st.button(f"Score {len(rows)} lead(s)", type="primary"):
        progress_placeholder = st.empty()
        results = []
        for i, lead_raw in enumerate(rows):
            progress_placeholder.caption(f"Lead {i + 1}/{len(rows)}...")
            final_state = _run_lead(lead_raw, target_profile, progress_placeholder)
            lead_id = _log_lead(engine, lead_raw, target_profile, final_state)
            results.append({"id": lead_id, "lead_raw": lead_raw, **final_state})
        progress_placeholder.empty()
        st.session_state.b2_results = sorted(results, key=lambda r: r.get("final_score") or 0, reverse=True)
        st.success(f"Scored {len(rows)} lead(s).")

    if st.session_state.b2_results:
        st.divider()
        st.markdown("**Ranked results**")
        for r in st.session_state.b2_results:
            score = r.get("final_score") or 0.0
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"**{r.get('company') or r.get('name') or 'Unknown'}**")
                c2.metric("Fit score", f"{score:.2f}")

                st.caption(
                    f"LLM: {r.get('llm_score', 0):.2f} ({r.get('llm_reasoning', '')}) · "
                    f"Formula: {r.get('formula_score', 0):.2f} ({r.get('formula_reasoning', '')})"
                )

                if r.get("enrichment_summary"):
                    st.markdown(f"*Company research:* {r['enrichment_summary']}")

                if r.get("outreach_draft"):
                    st.text_area("Outreach draft", r["outreach_draft"], height=100, key=f"outreach_{r['id']}", disabled=True)

                with st.expander("Agent reasoning trail"):
                    for step in r.get("trace", []):
                        st.markdown(f"- **{step['agent']}** — {step.get('detail', '')}")

                if st.button("⭐ Mark for public demo", key=f"pub_{r['id']}"):
                    _mark_public(engine, r["id"])
                    st.toast("Marked.")
