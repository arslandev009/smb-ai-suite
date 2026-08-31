"""The four agents (Router, Retriever, Synthesizer, Critic) + the LangGraph
wiring, all sync. This is the exact same architecture as the earlier scaffold —
only the runtime changed (sync functions, called from Streamlit instead of
async functions called from FastAPI).

    router -> retriever -> synthesizer -> critic --(rejected, retries left)--> retriever
                                              \\--(verified OR retries exhausted)--> END
"""
import re
from typing import TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.engine import Engine

from shared.config import settings
from shared.db import similarity_search
from shared.llm_client import embed, generate, generate_json

VALID_DOC_TYPES = ["hr", "product", "policy", "general"]

ROUTER_SYSTEM = f"""You classify a user's question into one knowledge-base category so the \
right document set gets searched. Valid categories: {", ".join(VALID_DOC_TYPES)}.
Respond with ONLY a JSON object: {{"doc_type": "<one of the categories above>", "reasoning": "<one short sentence>"}}
If the question could apply to more than one category, or you're unsure, use "general" so the search isn't over-filtered."""

SYNTHESIZER_SYSTEM = """You are a precise knowledge-base assistant. Answer the user's question \
using ONLY the numbered source excerpts provided. Every factual claim in your answer must be \
traceable to at least one source. Cite sources inline like [1], [2] matching the excerpt numbers.
If the excerpts don't contain enough information to answer, say so plainly instead of guessing.
Do not use outside knowledge. Keep the answer focused and concise."""

CRITIC_SYSTEM = """You are a strict fact-checker. Given an answer and the source excerpts it was \
supposedly based on, verify that every claim in the answer is actually supported by the excerpts.
Respond with ONLY a JSON object:
{"verified": true|false, "unsupported_claims": ["<claim text>", ...], "feedback": "<one short sentence explaining the verdict>"}
Be strict: an answer that hedges appropriately ("the documents don't specify X") when the excerpts
truly don't cover something should be marked verified=true. An answer stating something as fact
that isn't in the excerpts should be verified=false."""


class RAGState(TypedDict, total=False):
    question: str
    doc_type_hint: str | None
    doc_type_routed: str | None
    retrieved_chunks: list[dict]
    excluded_chunk_ids: list[int]
    answer: str
    cited_chunk_ids: list[int]
    verified: bool
    critic_feedback: str
    retry_count: int
    trace: list[dict]


def router_node(state: RAGState) -> dict:
    if state.get("doc_type_hint"):
        return {
            "doc_type_routed": state["doc_type_hint"],
            "trace": [{"agent": "router", "status": "done", "detail": f"manual override: {state['doc_type_hint']}"}],
        }
    try:
        result = generate_json(ROUTER_SYSTEM, state["question"])
        doc_type = result.get("doc_type", "general")
        if doc_type not in VALID_DOC_TYPES:
            doc_type = "general"
    except Exception:
        doc_type = "general"
    return {
        "doc_type_routed": doc_type,
        "trace": [{"agent": "router", "status": "done", "detail": f"routed to '{doc_type}'"}],
    }


def build_retriever_node(engine: Engine):
    def retriever_node(state: RAGState) -> dict:
        query_embedding = embed(state["question"])
        doc_type = state.get("doc_type_routed")
        filter_type = None if doc_type in (None, "general") else doc_type

        rows = similarity_search(
            engine,
            "b1",
            query_embedding,
            top_k=settings.top_k_retrieval,
            doc_type=filter_type,
            exclude_chunk_ids=state.get("excluded_chunk_ids", []),
        )

        fell_back = False
        if not rows and filter_type is not None:
            # The Router's category guess didn't match any document's actual
            # doc_type tag (e.g. routed to 'policy' but nothing is tagged that
            # way). Rather than surface a hard "no documents" dead end, fall
            # back to searching the whole library — a client's own document
            # tagging won't always line up with what the Router infers.
            rows = similarity_search(
                engine,
                "b1",
                query_embedding,
                top_k=settings.top_k_retrieval,
                doc_type=None,
                exclude_chunk_ids=state.get("excluded_chunk_ids", []),
            )
            fell_back = bool(rows)

        chunks = [
            {"id": r["id"], "document_id": r["document_id"], "filename": r["filename"],
             "content": r["content"], "similarity": float(r["similarity"])}
            for r in rows
        ]
        detail = f"found {len(chunks)} chunks"
        if fell_back:
            detail += f" (none tagged '{filter_type}' — searched full library instead)"

        return {
            "retrieved_chunks": chunks,
            "trace": [{"agent": "retriever", "status": "done", "detail": detail}],
        }

    return retriever_node


def synthesizer_node(state: RAGState) -> dict:
    chunks = state.get("retrieved_chunks", [])
    if not chunks:
        return {
            "answer": "I couldn't find any relevant documents to answer this question.",
            "cited_chunk_ids": [],
            "trace": [{"agent": "synthesizer", "status": "done", "detail": "no chunks to work with"}],
        }

    excerpt_block = "\n\n".join(f"[{i + 1}] (source: {c['filename']})\n{c['content']}" for i, c in enumerate(chunks))
    answer = generate(SYNTHESIZER_SYSTEM, f"Question: {state['question']}\n\nSource excerpts:\n{excerpt_block}", max_tokens=700)

    cited_indices = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
    cited_chunk_ids = [chunks[i - 1]["id"] for i in cited_indices if 0 < i <= len(chunks)]

    return {
        "answer": answer,
        "cited_chunk_ids": cited_chunk_ids or [c["id"] for c in chunks],
        "trace": [{"agent": "synthesizer", "status": "done", "detail": f"drafted answer, {len(cited_chunk_ids)} citations"}],
    }


def critic_node(state: RAGState) -> dict:
    chunks = state.get("retrieved_chunks", [])
    excerpt_block = "\n\n".join(f"[{i + 1}] {c['content']}" for i, c in enumerate(chunks))
    retry_count = state.get("retry_count", 0)

    try:
        result = generate_json(CRITIC_SYSTEM, f"Answer to verify:\n{state['answer']}\n\nSource excerpts it should be based on:\n{excerpt_block}")
        verified = bool(result.get("verified", False))
        feedback = result.get("feedback", "")
    except Exception:
        verified = retry_count >= settings.max_critic_retries
        feedback = "critic call failed; accepting after retry budget exhausted" if verified else "critic call failed, retrying"

    update: dict = {
        "verified": verified,
        "critic_feedback": feedback,
        "trace": [{"agent": "critic", "status": "done", "detail": ("verified" if verified else f"rejected: {feedback}")}],
    }
    if not verified:
        update["excluded_chunk_ids"] = list(set(state.get("excluded_chunk_ids", []) + [c["id"] for c in chunks]))
        update["retry_count"] = retry_count + 1
    return update


def critic_router(state: RAGState) -> str:
    if state.get("verified", False):
        return "end"
    if state.get("retry_count", 0) > settings.max_critic_retries:
        return "end"
    return "retry"


def build_graph(engine: Engine):
    workflow = StateGraph(RAGState)
    workflow.add_node("router", router_node)
    workflow.add_node("retriever", build_retriever_node(engine))
    workflow.add_node("synthesizer", synthesizer_node)
    workflow.add_node("critic", critic_node)

    workflow.set_entry_point("router")
    workflow.add_edge("router", "retriever")
    workflow.add_edge("retriever", "synthesizer")
    workflow.add_edge("synthesizer", "critic")
    workflow.add_conditional_edges("critic", critic_router, {"retry": "retriever", "end": END})

    return workflow.compile()