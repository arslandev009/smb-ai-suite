"""Classifier → Knowledge Retrieval (reuses B1's docs) → Response Drafting →
Escalation. The core skill here is different from B2 (judgment scoring) and
B3 (a real pause for a human decision): B4's Escalation node is a GUARDRAIL —
the system deciding, on its own, when NOT to act autonomously.

Escalation is a hybrid, same spirit as B2's hybrid scoring but inverted: a
hard-coded keyword rule can only push TOWARD escalating (never away from it),
combined with the LLM's own judgment call. Either one alone can trigger
escalation — this is deliberately a floor of safety, not a ceiling.
"""
import re
from typing import TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.engine import Engine

from shared.db import similarity_search
from shared.llm_client import embed, generate, generate_json

VALID_CATEGORIES = ["billing", "technical", "account", "other"]
VALID_URGENCY = ["low", "medium", "high"]

# Hard floor — if any of these appear, escalate regardless of what the LLM
# or retrieval quality suggests. Deliberately not exhaustive; this is a
# starter list a real deployment would tune per client.
ESCALATION_KEYWORDS = [
    "refund", "lawsuit", "lawyer", "legal action", "cancel my account",
    "fraud", "chargeback", "unacceptable", "furious", "scam", "sue",
]

CLASSIFIER_SYSTEM = f"""You classify a customer support ticket. Respond with ONLY a JSON object:
{{"category": "<one of {', '.join(VALID_CATEGORIES)}>", "urgency": "<one of {', '.join(VALID_URGENCY)}>"}}"""

RESPONSE_DRAFTING_SYSTEM = """You are a customer support agent. Draft a helpful, specific reply to
the ticket using ONLY the provided knowledge-base excerpts. If the excerpts don't cover what the
customer is asking, say so honestly rather than guessing — do not invent policy details, refund
amounts, or timelines that aren't in the excerpts. Keep the tone professional and concise."""

ESCALATION_SYSTEM = """You decide whether a drafted support reply is safe to auto-send, or should
be escalated to a human instead. You are given the actual knowledge-base excerpts the reply should
be grounded in — check the reply against them directly, the same way a fact-checker would, rather
than reasoning about how many excerpts exist. Respond with ONLY a JSON object:
{"escalate": true|false, "reason": "<one short sentence>"}
Escalate if: the reply states something the excerpts don't actually support, the customer's issue
is sensitive or high-stakes, urgency is high, or no excerpts were found at all. If the reply is
genuinely well-supported by the excerpts and the issue isn't sensitive, it's fine to not escalate —
don't escalate reflexively just because you can't be 100% certain."""


class TicketState(TypedDict, total=False):
    ticket_text: str
    category: str | None
    urgency: str | None

    retrieved_chunks: list[dict]
    citations: list[dict]

    drafted_response: str | None

    keyword_flagged: bool
    llm_escalate: bool
    escalation_reason: str | None
    escalated: bool
    status: str

    trace: list[dict]


def classifier_node(state: TicketState) -> dict:
    try:
        result = generate_json(CLASSIFIER_SYSTEM, state["ticket_text"])
        category = result.get("category", "other")
        urgency = result.get("urgency", "medium")
        if category not in VALID_CATEGORIES:
            category = "other"
        if urgency not in VALID_URGENCY:
            urgency = "medium"
    except Exception:
        category, urgency = "other", "medium"

    return {
        "category": category,
        "urgency": urgency,
        "trace": [{"agent": "classifier", "status": "done", "detail": f"category '{category}', urgency '{urgency}'"}],
    }


def build_knowledge_retrieval_node(engine: Engine):
    def knowledge_retrieval_node(state: TicketState) -> dict:
        # Unfiltered search across B1's whole library — a support ticket's
        # category (billing/technical/account) doesn't map cleanly onto B1's
        # document doc_types (hr/product/policy/general), so rather than
        # force an artificial alignment, this searches everything and lets
        # relevance do the work, same as B1's own fallback-to-full-library
        # behavior when a category filter comes up empty.
        query_embedding = embed(state["ticket_text"])
        rows = similarity_search(engine, "b1", query_embedding, top_k=4, doc_type=None)

        chunks = [
            {"id": r["id"], "filename": r["filename"], "content": r["content"], "similarity": float(r["similarity"])}
            for r in rows
        ]
        citations = [
            {"filename": c["filename"], "excerpt": c["content"][:280], "similarity": round(c["similarity"], 4)}
            for c in chunks
        ]
        return {
            "retrieved_chunks": chunks,
            "citations": citations,
            "trace": [{"agent": "knowledge_retrieval", "status": "done", "detail": f"found {len(chunks)} chunk(s)"}],
        }

    return knowledge_retrieval_node


def response_drafting_node(state: TicketState) -> dict:
    chunks = state.get("retrieved_chunks", [])
    if not chunks:
        return {
            "drafted_response": "I don't have enough information in our knowledge base to answer this confidently — escalating to a team member.",
            "trace": [{"agent": "response_drafting", "status": "done", "detail": "no chunks available, drafted an honest fallback"}],
        }

    excerpt_block = "\n\n".join(f"[{i + 1}] (source: {c['filename']})\n{c['content']}" for i, c in enumerate(chunks))
    response = generate(
        RESPONSE_DRAFTING_SYSTEM,
        f"Customer ticket: {state['ticket_text']}\n\nKnowledge base excerpts:\n{excerpt_block}",
        max_tokens=500,
    )
    return {
        "drafted_response": response,
        "trace": [{"agent": "response_drafting", "status": "done", "detail": "drafted a reply from retrieved excerpts"}],
    }


def escalation_node(state: TicketState) -> dict:
    ticket_lower = state["ticket_text"].lower()
    keyword_flagged = any(kw in ticket_lower for kw in ESCALATION_KEYWORDS)

    chunks = state.get("retrieved_chunks", [])
    if chunks:
        excerpt_block = "\n\n".join(f"[{i + 1}] {c['content']}" for i, c in enumerate(chunks))
        evidence_section = f"Knowledge-base excerpts the reply should be grounded in:\n{excerpt_block}"
    else:
        evidence_section = "No knowledge-base excerpts were found at all."

    try:
        result = generate_json(
            ESCALATION_SYSTEM,
            f"Ticket: {state['ticket_text']}\n\nUrgency: {state.get('urgency')}\n\n"
            f"Drafted reply: {state.get('drafted_response')}\n\n{evidence_section}",
        )
        llm_escalate = bool(result.get("escalate", False))
        llm_reason = result.get("reason", "")
    except Exception:
        # Fail safe in the direction of caution — if the escalation judgment
        # call itself fails, escalate rather than silently auto-sending.
        llm_escalate, llm_reason = True, "escalation judgment call failed; escalating as a precaution"

    no_evidence = len(state.get("retrieved_chunks", [])) == 0
    escalated = keyword_flagged or llm_escalate or no_evidence

    if keyword_flagged:
        reason = "flagged by sensitive-keyword rule"
    elif no_evidence:
        reason = "no supporting knowledge-base content found"
    elif llm_escalate:
        reason = llm_reason
    else:
        reason = None

    return {
        "keyword_flagged": keyword_flagged,
        "llm_escalate": llm_escalate,
        "escalated": escalated,
        "escalation_reason": reason,
        "status": "escalated" if escalated else "auto_resolved",
        "trace": [{"agent": "escalation", "status": "done",
                   "detail": ("escalated: " + reason) if escalated else "cleared for auto-send"}],
    }


def build_graph(engine: Engine):
    workflow = StateGraph(TicketState)
    workflow.add_node("classifier", classifier_node)
    workflow.add_node("knowledge_retrieval", build_knowledge_retrieval_node(engine))
    workflow.add_node("response_drafting", response_drafting_node)
    workflow.add_node("escalation", escalation_node)

    workflow.set_entry_point("classifier")
    workflow.add_edge("classifier", "knowledge_retrieval")
    workflow.add_edge("knowledge_retrieval", "response_drafting")
    workflow.add_edge("response_drafting", "escalation")
    workflow.add_edge("escalation", END)

    return workflow.compile()