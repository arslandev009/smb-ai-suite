"""Renders B3's tab: submit a leave request, watch it move through Classifier
→ Policy-Check → Approver-Router, then the graph PAUSES for a real human
decision (yours) before Notification runs. This is what makes it different
from B1/B2 — execution genuinely stops and waits, it isn't simulated.
"""
import json
import uuid

import streamlit as st
from sqlalchemy import text

from projects.b3_approval_workflow.graph import build_graph
from langgraph.types import Command

EXAMPLE_SHORT = "I'd like to take 2 days off next Friday and Monday for a long weekend trip."
EXAMPLE_LONG = "Requesting 10 days off starting the 15th of next month for an extended family visit overseas."


def _init_state():
    st.session_state.setdefault("b3_pending", None)   # {thread_id, payload, trace_log, request_text}
    st.session_state.setdefault("b3_results", [])


@st.cache_resource
def _graph(_engine):
    # Underscore-prefixed arg tells Streamlit's cache not to hash the engine
    # object itself — the graph (and its MemorySaver checkpointer) needs to
    # be a genuine singleton across reruns, or a paused request would lose
    # its state the moment the page reruns.
    return build_graph(_engine)


def _stream_and_accumulate(graph, input_or_command, config, trace_log: list) -> dict:
    final_state = {}
    for event in graph.stream(input_or_command, config=config, stream_mode="values"):
        final_state = event
        trace = event.get("trace", [])
        if trace and (not trace_log or trace_log[-1] != trace[-1]):
            trace_log.append(trace[-1])
    return final_state


def _log_request(engine, thread_id: str, state: dict, status: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO b3_approval_requests
                    (thread_id, request_text, request_type, requested_days, policy_summary,
                     policy_citations, approver, status, approver_note, notified,
                     notification_detail, trace, decided_at)
                VALUES
                    (:thread_id, :request_text, :request_type, :requested_days, :policy_summary,
                     :policy_citations, :approver, :status, :approver_note, :notified,
                     :notification_detail, :trace, now())
                RETURNING id
                """
            ),
            {
                "thread_id": thread_id,
                "request_text": state.get("request_text"),
                "request_type": state.get("request_type"),
                "requested_days": state.get("requested_days"),
                "policy_summary": state.get("policy_summary"),
                "policy_citations": json.dumps(state.get("policy_citations", [])),
                "approver": state.get("approver"),
                "status": status,
                "approver_note": state.get("approver_note"),
                "notified": state.get("notified", False),
                "notification_detail": state.get("notification_detail"),
                "trace": json.dumps(state.get("trace_log_snapshot", [])),
            },
        ).scalar_one()


def _mark_public(engine, request_id: int):
    with engine.begin() as conn:
        conn.execute(text("UPDATE b3_approval_requests SET is_public_demo = true WHERE id = :id"), {"id": request_id})


def render(engine):
    _init_state()
    graph = _graph(engine)

    st.markdown("### B3 · Business Process Automation (Approval Workflow)")
    st.caption("Classifier → Policy-Check (reuses B1's docs) → Approver-Router → **Human Approval (real pause)** → Notification.")

    # ---------------- Submission form (hidden while a request is pending) ----------------
    if st.session_state.b3_pending is None:
        c1, c2 = st.columns(2)
        if c1.button("Try: short request (2 days)", use_container_width=True):
            st.session_state["b3_draft"] = EXAMPLE_SHORT
        if c2.button("Try: long request (10 days)", use_container_width=True):
            st.session_state["b3_draft"] = EXAMPLE_LONG

        request_text = st.text_area(
            "Leave request", value=st.session_state.get("b3_draft", ""), height=90,
            placeholder="Describe the leave request in plain text...",
        )

        if st.button("Submit request", type="primary") and request_text.strip():
            thread_id = str(uuid.uuid4())
            config = {"configurable": {"thread_id": thread_id}}
            trace_log: list = []
            state = _stream_and_accumulate(graph, {"request_text": request_text.strip(), "trace": []}, config, trace_log)

            if "__interrupt__" in state:
                st.session_state.b3_pending = {
                    "thread_id": thread_id,
                    "config": config,
                    "payload": state["__interrupt__"][0].value,
                    "trace_log": trace_log,
                    "request_text": request_text.strip(),
                }
            else:
                # Non-leave request — the graph ran straight through to the
                # end without ever pausing, since human_approval_node exits
                # immediately for anything that isn't a leave request.
                request_id = _log_request(
                    engine, thread_id, {**state, "trace_log_snapshot": trace_log}, status="not_applicable"
                )
                st.session_state.b3_results.insert(
                    0, {"id": request_id, "request_text": request_text.strip(), **state, "trace_log": trace_log}
                )
            st.rerun()

    # ---------------- Pending approval card ----------------
    else:
        pending = st.session_state.b3_pending
        p = pending["payload"]

        with st.container(border=True):
            st.markdown("#### ⏸️ Awaiting your decision")
            st.markdown(f"**Request:** {p['request_text']}")
            if p.get("requested_days") is not None:
                st.caption(f"Requested: {p['requested_days']} day(s) · Routed to: **{p.get('approver')}**")
            if p.get("policy_summary"):
                st.markdown(f"*Policy check:* {p['policy_summary']}")

            note = st.text_input("Approver note (optional)", key="b3_note")
            c1, c2 = st.columns(2)
            approve = c1.button("✅ Approve", type="primary", use_container_width=True)
            reject = c2.button("❌ Reject", use_container_width=True)

            if approve or reject:
                decision = "approved" if approve else "rejected"
                trace_log = pending["trace_log"]
                final_state = _stream_and_accumulate(
                    graph,
                    Command(resume={"decision": decision, "approver_note": note}),
                    pending["config"],
                    trace_log,
                )
                request_id = _log_request(
                    engine, pending["thread_id"], {**final_state, "trace_log_snapshot": trace_log}, status=decision
                )
                st.session_state.b3_results.insert(
                    0, {"id": request_id, "request_text": pending["request_text"], **final_state, "trace_log": trace_log}
                )
                st.session_state.b3_pending = None
                st.rerun()

    # ---------------- Completed requests ----------------
    if st.session_state.b3_results:
        st.divider()
        st.markdown("**Recent requests**")
        for r in st.session_state.b3_results:
            with st.container(border=True):
                status = r.get("decision") or "not_applicable"
                badge = {"approved": "✅ approved", "rejected": "❌ rejected", "not_applicable": "⚪ not a leave request"}.get(status, status)
                st.markdown(f"**{r['request_text']}** — {badge}")
                if r.get("approver"):
                    st.caption(f"Approver: {r['approver']}" + (f" · Note: {r['approver_note']}" if r.get("approver_note") else ""))
                if r.get("notification_detail"):
                    st.caption(f"📣 {r['notification_detail']}")

                with st.expander("Agent reasoning trail"):
                    for step in r.get("trace_log", []):
                        st.markdown(f"- **{step['agent']}** — {step.get('detail', '')}")

                if st.button("⭐ Mark for public demo", key=f"pub_b3_{r['id']}"):
                    _mark_public(engine, r["id"])
                    st.toast("Marked.")
