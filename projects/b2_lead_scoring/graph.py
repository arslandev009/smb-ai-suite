"""The five agents (Intake, Enrichment, Scoring, Outreach Drafting, Supervisor)
+ LangGraph wiring.

Unlike B1's fixed linear chain, this is a genuine SUPERVISOR pattern: every
specialist reports back to the Supervisor, which decides the next hop based
on what's still missing in state — hub-and-spoke, not a straight line.

    intake -> supervisor -> enrichment -> supervisor -> scoring -> supervisor
                  \\-> outreach -> supervisor -> end (once everything is done)

Enrichment is also B2's first real TOOL CALL: an actual web search (via ddgs,
DuckDuckGo — free, no API key), not just an LLM guess.
"""
import re
from typing import TypedDict

from langgraph.graph import END, StateGraph

from shared.llm_client import generate, generate_json

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover
    DDGS = None


class LeadState(TypedDict, total=False):
    lead_raw: str
    target_profile: str

    name: str | None
    company: str | None
    email: str | None
    notes: str | None

    enrichment_summary: str | None
    enrichment_industry: str | None
    enrichment_size: str | None
    enrichment_sources: list[str]

    llm_score: float | None
    llm_reasoning: str | None
    formula_score: float | None
    formula_reasoning: str | None
    final_score: float | None

    outreach_draft: str | None

    trace: list[dict]


INTAKE_SYSTEM = """You extract structured fields from a raw lead submission (a CSV row or
pasted text). Respond with ONLY a JSON object:
{"name": "<person name or null>", "company": "<company name or null>", "email": "<email or null>", "notes": "<anything else relevant, one sentence>"}
If a field genuinely isn't present, use null rather than guessing."""

ENRICHMENT_SUMMARY_SYSTEM = """You summarize raw web search snippets about a company into a short,
factual profile. Respond with ONLY a JSON object:
{"summary": "<2-3 sentence factual summary>", "industry": "<best-guess industry, or null>", "estimated_size": "<e.g. 'startup', 'mid-size', 'enterprise', or null>"}
Only state what the snippets actually support — if the snippets are thin or irrelevant, say so in the summary rather than inventing detail."""

SCORING_SYSTEM = """You judge how well a lead fits a target customer profile, considering both
the lead's own notes and the enrichment summary about their company. Respond with ONLY a JSON
object: {"score": <0.0-1.0>, "reasoning": "<one sentence>"}
Judge genuine fit (industry, size, apparent need) — not just keyword overlap with the profile text.
Do not include any explanation, preamble, or text before or after the JSON object — your entire
response must be parseable as JSON on its own."""

OUTREACH_SYSTEM = """You write a short (3-4 sentence), specific, non-generic outreach message to a
sales lead, referencing something concrete about their company or notes. No greeting boilerplate,
no "I hope this finds you well" — get to the point. Plain text, no markdown."""


def intake_node(state: LeadState) -> dict:
    try:
        result = generate_json(INTAKE_SYSTEM, state["lead_raw"])
    except Exception:
        result = {"name": None, "company": None, "email": None, "notes": state["lead_raw"][:200]}
    return {
        "name": result.get("name"),
        "company": result.get("company"),
        "email": result.get("email"),
        "notes": result.get("notes"),
        "trace": [{"agent": "intake", "status": "done",
                   "detail": f"parsed lead: {result.get('company') or result.get('name') or 'unknown'}"}],
    }


def enrichment_node(state: LeadState) -> dict:
    company = state.get("company")
    if not company:
        return {
            "enrichment_summary": "No company name available to research.",
            "enrichment_sources": [],
            "trace": [{"agent": "enrichment", "status": "done", "detail": "skipped — no company name"}],
        }

    snippets: list[str] = []
    sources: list[str] = []
    if DDGS is not None:
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(f"{company} company overview industry", max_results=4):
                    snippet = r.get("body") or r.get("title") or ""
                    if snippet:
                        snippets.append(snippet)
                    if r.get("href"):
                        sources.append(r["href"])
        except Exception:
            pass  # network hiccup / rate limit — fall through to the "no results" summary below

    if not snippets:
        return {
            "enrichment_summary": f"No web search results found for '{company}'.",
            "enrichment_sources": [],
            "trace": [{"agent": "enrichment", "status": "done", "detail": "web search returned nothing"}],
        }

    try:
        result = generate_json(ENRICHMENT_SUMMARY_SYSTEM, "\n\n".join(snippets[:4]))
    except Exception:
        result = {"summary": snippets[0][:300], "industry": None, "estimated_size": None}

    return {
        "enrichment_summary": result.get("summary"),
        "enrichment_industry": result.get("industry"),
        "enrichment_size": result.get("estimated_size"),
        "enrichment_sources": sources[:4],
        "trace": [{"agent": "enrichment", "status": "done",
                   "detail": f"found {len(snippets)} web result(s) for '{company}'"}],
    }


_STOPWORDS = {"the", "a", "an", "and", "or", "for", "with", "of", "in", "on", "to", "is", "are"}


def _formula_score(state: LeadState) -> tuple[float, str]:
    """Transparent, deterministic keyword-overlap score — the formula floor
    that catches cases the LLM might undersell, same role as job-market-
    pipeline's formula_score."""
    def _keywords(text: str) -> set[str]:
        # min length 3 filters out noise from alphanumeric tokens like "B2B"
        # splitting into meaningless single-letter fragments ("b", "b")
        return {w.lower() for w in re.findall(r"[a-zA-Z]{3,}", text) if w.lower() not in _STOPWORDS}

    profile_words = _keywords(state.get("target_profile", ""))
    lead_text = " ".join(filter(None, [state.get("notes", ""), state.get("enrichment_summary", ""),
                                        state.get("enrichment_industry", "")]))
    lead_words = _keywords(lead_text)

    if not profile_words:
        return 0.0, "no target profile keywords to compare against"

    overlap = profile_words & lead_words
    score = round(len(overlap) / len(profile_words), 3)
    reasoning = f"{len(overlap)}/{len(profile_words)} profile keywords matched: {', '.join(sorted(overlap)) or 'none'}"
    return min(score, 1.0), reasoning


def scoring_node(state: LeadState) -> dict:
    formula_score, formula_reasoning = _formula_score(state)

    lead_summary = (
        f"Company: {state.get('company')}\nNotes: {state.get('notes')}\n"
        f"Enrichment: {state.get('enrichment_summary')} "
        f"(industry: {state.get('enrichment_industry')}, size: {state.get('enrichment_size')})"
    )
    try:
        result = generate_json(
            SCORING_SYSTEM,
            f"Target customer profile: {state.get('target_profile')}\n\nLead:\n{lead_summary}",
        )
        llm_score = float(result.get("score", 0.0))
        llm_reasoning = result.get("reasoning", "")
    except Exception as e:
        llm_score, llm_reasoning = 0.0, str(e)[:300]

    final_score = max(llm_score, formula_score)

    return {
        "llm_score": llm_score,
        "llm_reasoning": llm_reasoning,
        "formula_score": formula_score,
        "formula_reasoning": formula_reasoning,
        "final_score": final_score,
        "trace": [{"agent": "scoring", "status": "done",
                   "detail": f"llm={llm_score:.2f} formula={formula_score:.2f} -> final={final_score:.2f}"}],
    }


def outreach_node(state: LeadState) -> dict:
    context = (
        f"Lead name: {state.get('name')}\nCompany: {state.get('company')}\nNotes: {state.get('notes')}\n"
        f"Company summary: {state.get('enrichment_summary')}\n"
        f"Fit reasoning: {state.get('llm_reasoning')}"
    )
    try:
        draft = generate(OUTREACH_SYSTEM, context, max_tokens=200)
    except Exception:
        draft = "(outreach draft generation failed)"

    return {
        "outreach_draft": draft,
        "trace": [{"agent": "outreach", "status": "done", "detail": "drafted outreach message"}],
    }


def supervisor_node(state: LeadState) -> dict:
    """Doesn't do any work itself — just decides which specialist handles the
    next step. This is what makes it a supervisor pattern rather than a fixed
    pipeline: routing is a decision made from state, not a hardcoded order."""
    return {}


def supervisor_router(state: LeadState) -> str:
    if state.get("enrichment_summary") is None:
        return "enrichment"
    if state.get("final_score") is None:
        return "scoring"
    if state.get("outreach_draft") is None:
        return "outreach"
    return "end"


def build_graph():
    workflow = StateGraph(LeadState)
    workflow.add_node("intake", intake_node)
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("enrichment", enrichment_node)
    workflow.add_node("scoring", scoring_node)
    workflow.add_node("outreach", outreach_node)

    workflow.set_entry_point("intake")
    workflow.add_edge("intake", "supervisor")
    workflow.add_conditional_edges(
        "supervisor", supervisor_router,
        {"enrichment": "enrichment", "scoring": "scoring", "outreach": "outreach", "end": END},
    )
    workflow.add_edge("enrichment", "supervisor")
    workflow.add_edge("scoring", "supervisor")
    workflow.add_edge("outreach", "supervisor")

    return workflow.compile()