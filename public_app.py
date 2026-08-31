"""
SMB AI Suite — PUBLIC portfolio demo (deploy this file to Streamlit Community
Cloud). Read-only, no pipeline controls, no llama.cpp calls, no LangGraph
execution — mirrors job-market-pipeline's src/dashboard/app_public.py exactly:
this page reads a snapshot synced from the local machine via
scripts/sync_to_cloud.py, so it stays up regardless of whether the home
machine is on.

Secrets required in Streamlit Cloud's settings (Settings > Secrets), NOT a
local .env file:
    NEON_DATABASE_URL = "postgresql://..."
"""
import pandas as pd
import streamlit as st
from sqlalchemy import text

from shared.db import get_engine
from shared.ui_theme import inject

st.set_page_config(page_title="SMB AI Suite — Live Demo", page_icon="🧩", layout="wide")
inject()


@st.cache_resource
def _engine():
    url = st.secrets.get("NEON_DATABASE_URL")
    if not url:
        st.error("NEON_DATABASE_URL is not set in this app's Secrets.")
        st.stop()
    return get_engine(url)


def _run_query(sql: str, params: dict | None = None) -> pd.DataFrame:
    with _engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


st.title("🧩 SMB AI Suite")
st.caption("A multi-agent AI system for small businesses — six LangGraph-orchestrated projects, one suite.")

with st.expander("ℹ️ About this project", expanded=False):
    st.markdown(
        """
        Six multi-agent business systems (B1–B6), each built with **LangGraph**
        on the same reusable stack: local Postgres + `pgvector`, a locally-hosted
        LLM via **llama.cpp** (no cloud API dependency), and a shared retrieval
        pattern reused across the projects that need it.

        This page shows **real captured runs**, not live inference — the full
        system (live agent execution, document upload, pipeline control) runs
        locally. See the [GitHub repo](https://github.com/arslandev009/smb-ai-suite) for the complete source and each
        project's architecture writeup.
        """
    )

PROJECTS = {
    "B1 · Knowledge RAG": "b1",
    "B2 · Lead Scoring": "b2",
    "B3 · Approvals": "b3",
    "B4 · Support Triage": "b4",
    "B5 · BI Analyst": "b5",
    "B6 · Ops Manager": "b6",
}

selected_label = st.pills(
    "Project", options=list(PROJECTS.keys()), default="B1 · Knowledge RAG", label_visibility="collapsed"
)
selected = PROJECTS.get(selected_label, "b1")
PROJECTS_INV = {v: k for k, v in PROJECTS.items()}
st.divider()

if selected == "b1":
    st.markdown("### B1 · Multi-Agent RAG Knowledge Assistant")
    st.caption("Router → Retriever → Synthesizer → Critic, with a retry loop when the Critic isn't satisfied.")

    df = _run_query(
        "SELECT question, doc_type_routed, trace, answer, verified, retry_count, citations "
        "FROM b1_rag_queries WHERE is_public_demo = true ORDER BY created_at ASC"
    )

    if df.empty:
        st.info("No demo runs published yet — run `python scripts/sync_to_cloud.py` after marking some ⭐ in the local hub.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Published runs", len(df))
        m2.metric("Verified on first pass", int((df["retry_count"] == 0).sum()))
        m3.metric("Avg retries", round(df["retry_count"].mean(), 2))

        for _, row in df.iterrows():
            with st.container(border=True):
                st.markdown(f"**Q: {row['question']}**")
                badge = "✅ verified" if row["verified"] else "⚠️ unverified"
                st.caption(f"{badge} · {row['retry_count']} retries · routed: `{row['doc_type_routed']}`")

                with st.expander("Agent reasoning trail"):
                    for step in row["trace"]:
                        st.markdown(f"- **{step['agent']}** — {step.get('detail', '')}")

                st.markdown(row["answer"])

                citations = row["citations"] or []
                if citations:
                    with st.expander(f"{len(citations)} citation(s)"):
                        for c in citations:
                            st.markdown(
                                f'<div class="citation-card"><b>{c["filename"]}</b> · '
                                f'{round(c["similarity"] * 100)}% match<br>{c["excerpt"]}…</div>',
                                unsafe_allow_html=True,
                            )

elif selected == "b2":
    st.markdown("### B2 · Multi-Agent Lead Qualification & Scoring")
    st.caption("Intake → Enrichment (real web search) → Scoring (hybrid) → Outreach Drafting, orchestrated by a Supervisor.")

    df2 = _run_query(
        "SELECT company, name, target_profile, enrichment_data, llm_score, formula_score, "
        "final_score, outreach_draft, trace FROM b2_leads_scored WHERE is_public_demo = true "
        "ORDER BY final_score DESC"
    )

    if df2.empty:
        st.info("No demo leads published yet — score some in the local hub and mark them ⭐, then run `python scripts/sync_to_cloud.py --track b2`.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Published leads", len(df2))
        m2.metric("Avg fit score", f"{df2['final_score'].mean():.2f}")
        m3.metric("Strong fits (≥0.7)", int((df2["final_score"] >= 0.7).sum()))

        for _, row in df2.iterrows():
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"**{row['company'] or row['name'] or 'Unknown'}**")
                c2.metric("Fit score", f"{row['final_score']:.2f}")
                st.caption(f"LLM: {row['llm_score']:.2f} · Formula: {row['formula_score']:.2f}")

                enrichment = row["enrichment_data"] or {}
                if enrichment.get("summary"):
                    st.markdown(f"*Company research:* {enrichment['summary']}")

                if row["outreach_draft"]:
                    st.text_area("Outreach draft", row["outreach_draft"], height=100, disabled=True,
                                 key=f"pub_outreach_{row['company']}_{row['final_score']}")

                with st.expander("Agent reasoning trail"):
                    for step in row["trace"]:
                        st.markdown(f"- **{step['agent']}** — {step.get('detail', '')}")

elif selected == "b3":
    st.markdown("### B3 · Business Process Automation (Approval Workflow)")
    st.caption("Classifier → Policy-Check → Approver-Router → Human Approval → Notification.")

    df3 = _run_query(
        "SELECT request_text, request_type, requested_days, policy_summary, approver, "
        "status, approver_note, notified, notification_detail, trace FROM b3_approval_requests "
        "WHERE is_public_demo = true ORDER BY created_at DESC"
    )

    if df3.empty:
        st.info("No demo requests published yet — decide some in the local hub and mark them ⭐, then run `python scripts/sync_to_cloud.py --track b3`.")
    else:
        m1, m2 = st.columns(2)
        m1.metric("Published requests", len(df3))
        m2.metric("Approved", int((df3["status"] == "approved").sum()))

        for _, row in df3.iterrows():
            with st.container(border=True):
                badge = {"approved": "✅ approved", "rejected": "❌ rejected"}.get(row["status"], row["status"])
                st.markdown(f"**{row['request_text']}** — {badge}")
                if row["approver"]:
                    st.caption(f"Approver: {row['approver']}" + (f" · Note: {row['approver_note']}" if row["approver_note"] else ""))
                if row["policy_summary"]:
                    st.markdown(f"*Policy check:* {row['policy_summary']}")
                if row["notification_detail"]:
                    st.caption(f"📣 {row['notification_detail']}")

                with st.expander("Agent reasoning trail"):
                    for step in row["trace"]:
                        st.markdown(f"- **{step['agent']}** — {step.get('detail', '')}")

elif selected == "b4":
    st.markdown("### B4 · Customer Support Triage")
    st.caption("Classifier → Knowledge Retrieval → Response Drafting → Escalation (guardrail).")

    df4 = _run_query(
        "SELECT ticket_text, category, urgency, citations, drafted_response, escalated, "
        "escalation_reason, keyword_flagged, status, trace FROM b4_support_tickets "
        "WHERE is_public_demo = true ORDER BY created_at DESC"
    )

    if df4.empty:
        st.info("No demo tickets published yet — triage some in the local hub and mark them ⭐, then run `python scripts/sync_to_cloud.py --track b4`.")
    else:
        m1, m2 = st.columns(2)
        m1.metric("Published tickets", len(df4))
        m2.metric("Escalated", int(df4["escalated"].sum()))

        for _, row in df4.iterrows():
            with st.container(border=True):
                badge = "🚨 Escalated to human" if row["escalated"] else "✅ Auto-resolved"
                st.markdown(f"**{row['ticket_text']}** — {badge}")
                st.caption(f"Category: {row['category']} · Urgency: {row['urgency']}")
                if row["escalated"]:
                    st.warning(f"Escalation reason: {row['escalation_reason']}")
                st.markdown(f"*Drafted reply:* {row['drafted_response']}")

                citations = row["citations"] or []
                if citations:
                    with st.expander(f"{len(citations)} citation(s)"):
                        for c in citations:
                            st.markdown(
                                f'<div class="citation-card"><b>{c["filename"]}</b> · '
                                f'{round(c["similarity"] * 100)}% match<br>{c["excerpt"]}…</div>',
                                unsafe_allow_html=True,
                            )

                with st.expander("Agent reasoning trail"):
                    for step in row["trace"]:
                        st.markdown(f"- **{step['agent']}** — {step.get('detail', '')}")

elif selected == "b5":
    st.markdown("### B5 · BI / Reporting Analyst")
    st.caption("SQL Generator → Validator (blocks unsafe SQL) → Chart → Insight Summarizer.")

    df5 = _run_query(
        "SELECT question, generated_sql, row_count, sample_results, chart_type, chart_x, chart_y, "
        "insight_summary, trace FROM b5_bi_queries WHERE is_public_demo = true ORDER BY created_at DESC"
    )

    if df5.empty:
        st.info("No demo queries published yet — run some in the local hub and mark them ⭐, then run `python scripts/sync_to_cloud.py --track b5`.")
    else:
        for _, row in df5.iterrows():
            with st.container(border=True):
                st.markdown(f"**{row['question']}**")
                with st.expander("Generated SQL"):
                    st.code(row["generated_sql"], language="sql")

                rows = row["sample_results"] or []
                if rows:
                    df_display = pd.DataFrame(rows)
                    st.dataframe(df_display, use_container_width=True)
                    if row["chart_type"] in ("bar", "line") and row["chart_x"] in df_display.columns and row["chart_y"] in df_display.columns:
                        chart_df = df_display.set_index(row["chart_x"])[[row["chart_y"]]]
                        (st.bar_chart if row["chart_type"] == "bar" else st.line_chart)(chart_df)

                if row["insight_summary"]:
                    st.markdown(f"*Insight:* {row['insight_summary']}")

                with st.expander("Agent reasoning trail"):
                    for step in row["trace"]:
                        st.markdown(f"- **{step['agent']}** — {step.get('detail', '')}")

elif selected == "b6":
    st.markdown("### B6 · AI Operations Manager (capstone)")
    st.caption("One Supervisor routes any incoming request to B1, B2, B4, or B5 — the real specialist systems, not a summary of them.")

    df6 = _run_query(
        "SELECT request_text, incoming_type, routed_to_project, outcome_summary, "
        "outcome_detail, latency_ms, trace FROM b6_routed_requests "
        "WHERE is_public_demo = true ORDER BY created_at DESC"
    )

    if df6.empty:
        st.info("No demo requests published yet — route some in the local hub and mark them ⭐, then run `python scripts/sync_to_cloud.py --track b6`.")
    else:
        m1, m2 = st.columns(2)
        m1.metric("Published requests", len(df6))
        m2.metric("Distinct routes", df6["routed_to_project"].nunique())

        for _, row in df6.iterrows():
            with st.container(border=True):
                st.markdown(f"**{row['request_text']}**")
                st.caption(f"Classified as: `{row['incoming_type']}` → routed to **{row['routed_to_project']}** · {row['latency_ms']}ms")
                st.markdown(f"*Outcome:* {row['outcome_summary']}")

                detail = row["outcome_detail"] or {}
                if detail:
                    with st.expander("Outcome details"):
                        for k, v in detail.items():
                            st.markdown(f"- **{k}**: {v}")

                with st.expander("Agent reasoning trail"):
                    for step in row["trace"]:
                        st.markdown(f"- **{step['agent']}** — {step.get('detail', '')}")

else:
    st.info(f"🚧 {PROJECTS_INV.get(selected, selected.upper())} isn't built yet — check back once it's published from the local hub.")
