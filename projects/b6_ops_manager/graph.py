"""One top-level Supervisor Agent that receives any incoming request and
routes it to the correct specialist SYSTEM below it — not a specialist agent
like B2's supervisor, a whole specialist PROJECT (B1, B2, B4, or B5). This is
what makes B6 hierarchical: a supervisor-of-supervisors, since B2 already has
its own internal supervisor pattern.

B6 does not reimplement any B1-B5 logic — it imports and invokes their
existing compiled graphs directly, then normalizes each project's very
different output shape into one consistent outcome for display/logging.

Scoped to the four request types the project's own definition of done calls
for (lead, ticket, question, report) — B3 isn't part of this routing set,
since embedding its human-in-the-loop interrupt inside another graph's node
would need its own thread/checkpointer plumbing that the "route and get an
answer back" shape here doesn't fit.
"""
from typing import TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.engine import Engine

from shared.llm_client import generate_json

VALID_REQUEST_TYPES = ["lead", "ticket", "question", "report"]

CLASSIFIER_SYSTEM = """You route an incoming request to the correct specialist system. Respond
with ONLY a JSON object: {"request_type": "<one of: lead, ticket, question, report>"}
- "lead": a new sales prospect or contact to qualify/score for fit
- "ticket": a customer support issue, complaint, or question about their own account/order
- "question": a general question about internal company documents, policies, or product info
- "report": a request for data, statistics, or analytics about the business itself
If genuinely ambiguous between "ticket" and "question", prefer "question" — B1's Router already
handles routing within its own knowledge base."""

# Used only when routing an ad-hoc request into B2, which needs a target
# profile to score against — same default B2's own tab uses standalone.
DEFAULT_LEAD_PROFILE = (
    "B2B SaaS companies, 10-200 employees, tech or software industry, "
    "based in North America or Europe, showing signs of active growth."
)


class SupervisorState(TypedDict, total=False):
    request_text: str
    request_type: str | None
    routed_to: str | None
    outcome_summary: str | None
    outcome_detail: dict
    trace: list[dict]


def classifier_node(state: SupervisorState) -> dict:
    try:
        result = generate_json(CLASSIFIER_SYSTEM, state["request_text"])
        request_type = result.get("request_type", "question")
        if request_type not in VALID_REQUEST_TYPES:
            request_type = "question"
    except Exception:
        request_type = "question"

    return {
        "request_type": request_type,
        "trace": [{"agent": "supervisor_classifier", "status": "done", "detail": f"routed to '{request_type}'"}],
    }


def classifier_router(state: SupervisorState) -> str:
    return state.get("request_type", "question")


def build_route_question_node(engine: Engine):
    def route_question_node(state: SupervisorState) -> dict:
        from projects.b1_rag_knowledge.graph import build_graph as build_b1

        result = build_b1(engine).invoke({
            "question": state["request_text"], "doc_type_hint": None,
            "excluded_chunk_ids": [], "retry_count": 0, "trace": [],
        })
        return {
            "routed_to": "B1 — Knowledge RAG",
            "outcome_summary": result.get("answer", ""),
            "outcome_detail": {
                "verified": result.get("verified"),
                "doc_type_routed": result.get("doc_type_routed"),
                "citation_count": len(result.get("cited_chunk_ids", [])),
            },
            "trace": [{"agent": "b1_question", "status": "done",
                       "detail": f"verified={result.get('verified')}"}],
        }

    return route_question_node


def route_lead_node(state: SupervisorState) -> dict:
    from projects.b2_lead_scoring.graph import build_graph as build_b2

    result = build_b2().invoke({
        "lead_raw": state["request_text"], "target_profile": DEFAULT_LEAD_PROFILE, "trace": [],
    })
    return {
        "routed_to": "B2 — Lead Scoring",
        "outcome_summary": f"{result.get('company') or result.get('name') or 'Lead'} — fit score {result.get('final_score', 0):.2f}",
        "outcome_detail": {
            "company": result.get("company"),
            "final_score": result.get("final_score"),
            "llm_reasoning": result.get("llm_reasoning"),
            "outreach_draft": result.get("outreach_draft"),
        },
        "trace": [{"agent": "b2_lead", "status": "done",
                   "detail": f"scored {result.get('final_score', 0):.2f}"}],
    }


def build_route_ticket_node(engine: Engine):
    def route_ticket_node(state: SupervisorState) -> dict:
        from projects.b4_support_triage.graph import build_graph as build_b4

        result = build_b4(engine).invoke({"ticket_text": state["request_text"], "trace": []})
        return {
            "routed_to": "B4 — Support Triage",
            "outcome_summary": result.get("drafted_response", ""),
            "outcome_detail": {
                "category": result.get("category"),
                "urgency": result.get("urgency"),
                "escalated": result.get("escalated"),
                "escalation_reason": result.get("escalation_reason"),
            },
            "trace": [{"agent": "b4_ticket", "status": "done",
                       "detail": "escalated" if result.get("escalated") else "auto-resolved"}],
        }

    return route_ticket_node


def build_route_report_node(engine: Engine):
    def route_report_node(state: SupervisorState) -> dict:
        from projects.b5_bi_reporting.graph import build_graph as build_b5

        result = build_b5(engine).invoke({"question": state["request_text"], "trace": []})
        return {
            "routed_to": "B5 — BI Analyst",
            "outcome_summary": result.get("insight_summary") or result.get("rejection_reason", "Query was rejected."),
            "outcome_detail": {
                "validation_passed": result.get("validation_passed"),
                "generated_sql": result.get("generated_sql"),
                "row_count": result.get("row_count"),
            },
            "trace": [{"agent": "b5_report", "status": "done",
                       "detail": "validated" if result.get("validation_passed") else "rejected"}],
        }

    return route_report_node


def build_graph(engine: Engine):
    workflow = StateGraph(SupervisorState)
    workflow.add_node("classifier", classifier_node)
    workflow.add_node("route_question", build_route_question_node(engine))
    workflow.add_node("route_lead", route_lead_node)
    workflow.add_node("route_ticket", build_route_ticket_node(engine))
    workflow.add_node("route_report", build_route_report_node(engine))

    workflow.set_entry_point("classifier")
    workflow.add_conditional_edges(
        "classifier", classifier_router,
        {"question": "route_question", "lead": "route_lead", "ticket": "route_ticket", "report": "route_report"},
    )
    workflow.add_edge("route_question", END)
    workflow.add_edge("route_lead", END)
    workflow.add_edge("route_ticket", END)
    workflow.add_edge("route_report", END)

    return workflow.compile()
