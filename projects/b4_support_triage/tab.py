"""Renders B4's tab: submit a support ticket, watch it get classified, see
what the knowledge base actually supports, and see whether the system trusts
its own drafted reply enough to auto-send it — or hands it to a human instead.
"""
import json

import streamlit as st
from sqlalchemy import text

from projects.b4_support_triage.graph import build_graph

EXAMPLE_NORMAL = "How many users can I have on the free trial?"
EXAMPLE_ESCALATE = "This is completely unacceptable — I want a full refund right now or I'm contacting my lawyer."
EXAMPLE_NO_EVIDENCE = "Does your product support quantum-resistant encryption for enterprise deployments?"


def _init_state():
    st.session_state.setdefault("b4_results", [])


def _run_ticket(engine, ticket_text: str, progress_placeholder) -> dict:
    graph = build_graph(engine)
    final_state: dict = {}
    for event in graph.stream({"ticket_text": ticket_text, "trace": []}, stream_mode="values"):
        final_state = event
        trace = event.get("trace", [])
        if trace:
            progress_placeholder.caption(f"→ {trace[-1]['agent']}: {trace[-1].get('detail', '')}")
    return final_state


def _log_ticket(engine, state: dict) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO b4_support_tickets
                    (ticket_text, category, urgency, citations, drafted_response,
                     escalated, escalation_reason, keyword_flagged, status, trace)
                VALUES
                    (:ticket_text, :category, :urgency, :citations, :drafted_response,
                     :escalated, :escalation_reason, :keyword_flagged, :status, :trace)
                RETURNING id
                """
            ),
            {
                "ticket_text": state["ticket_text"],
                "category": state.get("category"),
                "urgency": state.get("urgency"),
                "citations": json.dumps(state.get("citations", [])),
                "drafted_response": state.get("drafted_response"),
                "escalated": state.get("escalated", False),
                "escalation_reason": state.get("escalation_reason"),
                "keyword_flagged": state.get("keyword_flagged", False),
                "status": state.get("status", "auto_resolved"),
                "trace": json.dumps(state.get("trace", [])),
            },
        ).scalar_one()


def _mark_public(engine, ticket_id: int):
    with engine.begin() as conn:
        conn.execute(text("UPDATE b4_support_tickets SET is_public_demo = true WHERE id = :id"), {"id": ticket_id})


def render(engine):
    _init_state()

    st.markdown("### B4 · Customer Support Triage")
    st.caption("Classifier → Knowledge Retrieval (reuses B1's docs) → Response Drafting → **Escalation** (guardrail — knows when *not* to auto-send).")

    c1, c2, c3 = st.columns(3)
    if c1.button("Try: normal question", use_container_width=True):
        st.session_state["b4_draft"] = EXAMPLE_NORMAL
    if c2.button("Try: should escalate", use_container_width=True):
        st.session_state["b4_draft"] = EXAMPLE_ESCALATE
    if c3.button("Try: no KB coverage", use_container_width=True):
        st.session_state["b4_draft"] = EXAMPLE_NO_EVIDENCE

    ticket_text = st.text_area(
        "Support ticket", value=st.session_state.get("b4_draft", ""), height=90,
        placeholder="Paste or type a customer support message...",
    )

    if st.button("Submit ticket", type="primary") and ticket_text.strip():
        progress_placeholder = st.empty()
        state = _run_ticket(engine, ticket_text.strip(), progress_placeholder)
        state["ticket_text"] = ticket_text.strip()
        progress_placeholder.empty()
        ticket_id = _log_ticket(engine, state)
        st.session_state.b4_results.insert(0, {"id": ticket_id, **state})
        st.rerun()

    if st.session_state.b4_results:
        st.divider()
        st.markdown("**Recent tickets**")
        for r in st.session_state.b4_results:
            with st.container(border=True):
                if r.get("escalated"):
                    badge = "🚨 Escalated to human"
                else:
                    badge = "✅ Auto-resolved"
                st.markdown(f"**{r['ticket_text']}** — {badge}")
                st.caption(f"Category: {r.get('category')} · Urgency: {r.get('urgency')}")

                if r.get("escalated"):
                    st.warning(f"Escalation reason: {r.get('escalation_reason')}")
                    if r.get("keyword_flagged"):
                        st.caption("⚠️ Triggered by the hard-coded sensitive-keyword rule.")

                st.markdown(f"*Drafted reply:* {r.get('drafted_response')}")

                citations = r.get("citations", [])
                if citations:
                    with st.expander(f"{len(citations)} citation(s)"):
                        for c in citations:
                            st.markdown(
                                f'<div class="citation-card"><b>{c["filename"]}</b> · '
                                f'{round(c["similarity"] * 100)}% match<br>{c["excerpt"]}…</div>',
                                unsafe_allow_html=True,
                            )

                with st.expander("Agent reasoning trail"):
                    for step in r.get("trace", []):
                        st.markdown(f"- **{step['agent']}** — {step.get('detail', '')}")

                if st.button("⭐ Mark for public demo", key=f"pub_b4_{r['id']}"):
                    _mark_public(engine, r["id"])
                    st.toast("Marked.")
