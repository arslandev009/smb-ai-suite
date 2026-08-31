"""Classifier → Policy-Check → Approver-Router → Human Approval → Notification.

Scoped to one real workflow: leave/PTO requests. The Classifier still checks
the request type (so a non-leave request gets a clear, honest "not supported
in this demo" rather than silently mishandled), but everything past that
point is leave-request-specific — see the doc's own scope discipline note.

The interesting new piece: HUMAN-IN-THE-LOOP INTERRUPT. human_approval_node
calls interrupt(...), which pauses the graph and returns control to the
caller with the request details attached. The graph stays paused (held by
the checkpointer, keyed on thread_id) until tab.py resumes it with
Command(resume={"decision": ..., "approver_note": ...}) — a real approve/
reject decision from you, not a simulated one.

Policy-Check deliberately reuses B1's retrieval stack directly rather than
building a second document/embedding pipeline: same shared.db.similarity_search
against the same b1_documents/b1_chunks tables, filtered to doc_type='policy'.
"""
from typing import TypedDict

import httpx
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.engine import Engine

from shared.config import settings
from shared.db import similarity_search
from shared.llm_client import embed, generate, generate_json

VALID_REQUEST_TYPES = ["leave", "procurement", "expense", "other"]

CLASSIFIER_SYSTEM = f"""You classify an employee's request. Respond with ONLY a JSON object:
{{"request_type": "<one of {', '.join(VALID_REQUEST_TYPES)}>", "requested_days": <number of days requested, or null if not a leave request>}}
Only extract requested_days for leave/PTO requests. If the number of days isn't stated explicitly, make your best estimate from the dates mentioned, or use null if it truly can't be determined."""

POLICY_SUMMARY_SYSTEM = """You summarize what a company's leave policy says that's relevant to a
specific leave request, using ONLY the provided policy excerpts. Be factual and specific (exact
day counts, notice periods, carryover rules) — this summary is shown to a human approver, it
should help them decide, not decide for them. If the excerpts don't cover something relevant,
say so rather than guessing."""


class ApprovalState(TypedDict, total=False):
    request_text: str
    request_type: str | None
    requested_days: float | None

    policy_summary: str | None
    policy_citations: list[dict]

    approver: str | None

    decision: str | None
    approver_note: str | None

    notified: bool
    notification_detail: str | None

    trace: list[dict]


def classifier_node(state: ApprovalState) -> dict:
    try:
        result = generate_json(CLASSIFIER_SYSTEM, state["request_text"])
        request_type = result.get("request_type", "other")
        if request_type not in VALID_REQUEST_TYPES:
            request_type = "other"
        requested_days = result.get("requested_days")
    except Exception:
        request_type, requested_days = "other", None

    return {
        "request_type": request_type,
        "requested_days": requested_days,
        "trace": [{"agent": "classifier", "status": "done",
                   "detail": f"classified as '{request_type}'" + (f", {requested_days} day(s)" if requested_days else "")}],
    }


def build_policy_check_node(engine: Engine):
    def policy_check_node(state: ApprovalState) -> dict:
        if state.get("request_type") != "leave":
            return {
                "policy_summary": None,
                "policy_citations": [],
                "trace": [{"agent": "policy_check", "status": "done", "detail": "skipped — not a leave request"}],
            }

        query = f"leave policy relevant to a request for {state.get('requested_days')} day(s) off"
        query_embedding = embed(query)
        rows = similarity_search(engine, "b1", query_embedding, top_k=4, doc_type="policy")

        if not rows:
            return {
                "policy_summary": "No policy documents found — upload your leave policy in B1's Knowledge base, tagged 'policy'.",
                "policy_citations": [],
                "trace": [{"agent": "policy_check", "status": "done", "detail": "no policy documents available"}],
            }

        excerpt_block = "\n\n".join(f"[{i + 1}] {r['content']}" for i, r in enumerate(rows))
        summary = generate(
            POLICY_SUMMARY_SYSTEM,
            f"Request: {state['request_text']}\n\nPolicy excerpts:\n{excerpt_block}",
            max_tokens=400,
        )
        citations = [
            {"filename": r["filename"], "excerpt": r["content"][:280], "similarity": round(float(r["similarity"]), 4)}
            for r in rows
        ]
        return {
            "policy_summary": summary,
            "policy_citations": citations,
            "trace": [{"agent": "policy_check", "status": "done", "detail": f"checked against {len(rows)} policy excerpt(s)"}],
        }

    return policy_check_node


def approver_router_node(state: ApprovalState) -> dict:
    """Deliberately NOT an LLM call — who needs to approve a leave request is
    a deterministic business rule, not a judgment call. Contrast this with
    B2's scoring node, which is a judgment call and does use the LLM."""
    if state.get("request_type") != "leave":
        approver = "N/A — not a leave request"
    else:
        days = state.get("requested_days")
        if days is None:
            approver = "Direct Manager (day count unclear — needs clarification)"
        elif days <= 2:
            approver = "Direct Manager"
        else:
            approver = "Direct Manager + HR"

    return {
        "approver": approver,
        "trace": [{"agent": "approver_router", "status": "done", "detail": f"routed to: {approver}"}],
    }


def human_approval_node(state: ApprovalState) -> dict:
    if state.get("request_type") != "leave":
        return {
            "decision": "not_applicable",
            "trace": [{"agent": "human_approval", "status": "done", "detail": "skipped — not a leave request"}],
        }

    response = interrupt({
        "request_text": state["request_text"],
        "requested_days": state.get("requested_days"),
        "policy_summary": state.get("policy_summary"),
        "approver": state.get("approver"),
    })

    return {
        "decision": response.get("decision"),
        "approver_note": response.get("approver_note"),
        "trace": [{"agent": "human_approval", "status": "done",
                   "detail": f"human decision: {response.get('decision')}"}],
    }


def notification_node(state: ApprovalState) -> dict:
    if state.get("decision") == "not_applicable":
        return {
            "notified": False,
            "notification_detail": "Not sent — request type isn't supported by this demo workflow.",
            "trace": [{"agent": "notification", "status": "done", "detail": "no notification sent"}],
        }

    payload = {
        "request_text": state["request_text"],
        "requested_days": state.get("requested_days"),
        "approver": state.get("approver"),
        "decision": state.get("decision"),
        "approver_note": state.get("approver_note"),
    }

    if not settings.n8n_webhook_url:
        return {
            "notified": False,
            "notification_detail": "n8n webhook not configured (N8N_WEBHOOK_URL empty in .env) — notification skipped.",
            "trace": [{"agent": "notification", "status": "done", "detail": "n8n not configured"}],
        }

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(settings.n8n_webhook_url, json=payload)
            resp.raise_for_status()
        return {
            "notified": True,
            "notification_detail": f"Sent to n8n webhook — decision: {state.get('decision')}",
            "trace": [{"agent": "notification", "status": "done", "detail": "notified via n8n"}],
        }
    except Exception as e:
        return {
            "notified": False,
            "notification_detail": f"n8n webhook call failed: {e}",
            "trace": [{"agent": "notification", "status": "done", "detail": "n8n call failed"}],
        }


def build_graph(engine: Engine):
    workflow = StateGraph(ApprovalState)
    workflow.add_node("classifier", classifier_node)
    workflow.add_node("policy_check", build_policy_check_node(engine))
    workflow.add_node("approver_router", approver_router_node)
    workflow.add_node("human_approval", human_approval_node)
    workflow.add_node("notification", notification_node)

    workflow.set_entry_point("classifier")
    workflow.add_edge("classifier", "policy_check")
    workflow.add_edge("policy_check", "approver_router")
    workflow.add_edge("approver_router", "human_approval")
    workflow.add_edge("human_approval", "notification")
    workflow.add_edge("notification", END)

    # A checkpointer is required for interrupt()/Command(resume=...) to work —
    # it's what lets the graph's state survive between the pause and the
    # resume, which in Streamlit's case is two completely separate script runs.
    return workflow.compile(checkpointer=MemorySaver())
