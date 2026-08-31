"""Renders B6's tab: submit any kind of request in plain text, watch the
Supervisor classify it and route it to whichever B1/B2/B4/B5 system actually
handles that type — the same real sub-graphs those tabs use individually,
not a simulated summary of them.
"""
import json
import time

import streamlit as st
from sqlalchemy import text

from projects.b6_ops_manager.graph import build_graph

EXAMPLES = {
    "Lead": "Priya Nair, Head of Ops at a 40-person B2B SaaS analytics company, downloaded our whitepaper.",
    "Ticket": "This is completely unacceptable — I want a full refund right now or I'm contacting my lawyer.",
    "Question": "How many PTO days can I carry over into next year?",
    "Report": "How many leads have a final score above 0.5?",
}


def _init_state():
    st.session_state.setdefault("b6_results", [])


def _run_request(engine, request_text: str, progress_placeholder) -> tuple[dict, int]:
    graph = build_graph(engine)
    start = time.monotonic()
    trace_log: list = []
    final_state: dict = {}
    for event in graph.stream({"request_text": request_text, "trace": []}, stream_mode="values"):
        final_state = event
        trace = event.get("trace", [])
        if trace and (not trace_log or trace_log[-1] != trace[-1]):
            trace_log.append(trace[-1])
            progress_placeholder.caption(f"→ {trace[-1]['agent']}: {trace[-1].get('detail', '')}")
    latency_ms = int((time.monotonic() - start) * 1000)
    final_state["trace_log_full"] = trace_log
    return final_state, latency_ms


def _log_request(engine, request_text: str, state: dict, latency_ms: int) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO b6_routed_requests
                    (request_text, incoming_type, routed_to_project, outcome_summary,
                     outcome_detail, latency_ms, trace)
                VALUES
                    (:request_text, :incoming_type, :routed_to_project, :outcome_summary,
                     :outcome_detail, :latency_ms, :trace)
                RETURNING id
                """
            ),
            {
                "request_text": request_text,
                "incoming_type": state.get("request_type"),
                "routed_to_project": state.get("routed_to"),
                "outcome_summary": state.get("outcome_summary"),
                "outcome_detail": json.dumps(state.get("outcome_detail", {})),
                "latency_ms": latency_ms,
                "trace": json.dumps(state.get("trace_log_full", [])),
            },
        ).scalar_one()


def _mark_public(engine, request_id: int):
    with engine.begin() as conn:
        conn.execute(text("UPDATE b6_routed_requests SET is_public_demo = true WHERE id = :id"), {"id": request_id})


def render(engine):
    _init_state()

    st.markdown("### B6 · AI Operations Manager (capstone)")
    st.caption("One Supervisor routes any incoming request to the correct specialist system — B1, B2, B4, or B5 — using their real, unmodified graphs.")

    cols = st.columns(4)
    for c, (label, text_) in zip(cols, EXAMPLES.items()):
        if c.button(f"Try: {label}", use_container_width=True):
            st.session_state["b6_draft"] = text_

    request_text = st.text_area(
        "Incoming request", value=st.session_state.get("b6_draft", ""), height=90,
        placeholder="Could be a lead, a support ticket, a question, or a reporting request...",
    )

    if st.button("Route it", type="primary") and request_text.strip():
        progress_placeholder = st.empty()
        state, latency_ms = _run_request(engine, request_text.strip(), progress_placeholder)
        progress_placeholder.empty()
        request_id = _log_request(engine, request_text.strip(), state, latency_ms)
        st.session_state.b6_results.insert(0, {"id": request_id, "request_text": request_text.strip(), "latency_ms": latency_ms, **state})
        st.rerun()

    if st.session_state.b6_results:
        st.divider()
        st.markdown("**Recent routed requests**")
        for r in st.session_state.b6_results:
            with st.container(border=True):
                st.markdown(f"**{r['request_text']}**")
                st.caption(f"Classified as: `{r.get('request_type')}` → routed to **{r.get('routed_to')}** · {r.get('latency_ms')}ms")
                st.markdown(f"*Outcome:* {r.get('outcome_summary')}")

                detail = r.get("outcome_detail", {})
                if detail:
                    with st.expander("Outcome details"):
                        for k, v in detail.items():
                            st.markdown(f"- **{k}**: {v}")

                with st.expander("Agent reasoning trail"):
                    for step in r.get("trace_log_full", []):
                        st.markdown(f"- **{step['agent']}** — {step.get('detail', '')}")

                if st.button("⭐ Mark for public demo", key=f"pub_b6_{r['id']}"):
                    _mark_public(engine, r["id"])
                    st.toast("Marked.")
