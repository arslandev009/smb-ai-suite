"""Renders B1's tab inside hub_app.py. Everything here is scoped to this one
project — document library, upload, chat, and the live agent-rack visualization
that updates as the LangGraph stream progresses.
"""
import json
import time

import streamlit as st
from sqlalchemy import text

from projects.b1_rag_knowledge.graph import VALID_DOC_TYPES, build_graph
from projects.b1_rag_knowledge.ingestion import delete_document, ingest_document, list_documents

AGENT_META = {
    "router": ("Router", "picks the knowledge source"),
    "retriever": ("Retriever", "vector search over chunks"),
    "synthesizer": ("Synthesizer", "drafts the cited answer"),
    "critic": ("Critic", "verifies claims against sources"),
}
AGENT_ORDER = ["router", "retriever", "synthesizer", "critic"]


def _init_state():
    st.session_state.setdefault("b1_messages", [])
    st.session_state.setdefault("b1_doc_type_filter", None)


def _rack_html(statuses: dict, detail: str) -> str:
    cards = ""
    for agent in AGENT_ORDER:
        label, role = AGENT_META[agent]
        cls = statuses.get(agent, "idle")
        cards += (
            f'<div class="agent-card {cls}"><b>{label}</b>'
            f'<div style="font-size:0.78rem;color:#9295a6">{role}</div></div>'
        )
    detail_html = f'<div style="font-size:0.82rem;color:#9295a6;margin-top:4px;">{detail}</div>' if detail else ""
    return f'<div>{cards}{detail_html}</div>'


def _run_query(engine, question: str, doc_type_hint: str | None, rack_placeholder) -> dict:
    graph = build_graph(engine)
    initial_state = {
        "question": question,
        "doc_type_hint": doc_type_hint,
        "excluded_chunk_ids": [],
        "retry_count": 0,
        "trace": [],
    }
    statuses = {a: "idle" for a in AGENT_ORDER}
    statuses["router"] = "active"
    rack_placeholder.markdown(_rack_html(statuses, "starting…"), unsafe_allow_html=True)

    final_state: dict = {}
    for event in graph.stream(initial_state, stream_mode="values"):
        final_state = event
        trace = event.get("trace", [])
        if not trace:
            continue
        evt = trace[-1]
        agent = evt["agent"]
        detail = evt.get("detail", "")
        rejected = agent == "critic" and detail.startswith("rejected")

        statuses[agent] = "rejected" if rejected else "done"
        if rejected:
            statuses["retriever"] = "active"
            statuses["synthesizer"] = "idle"
        else:
            idx = AGENT_ORDER.index(agent)
            if idx + 1 < len(AGENT_ORDER):
                statuses[AGENT_ORDER[idx + 1]] = "active"

        rack_placeholder.markdown(_rack_html(statuses, detail), unsafe_allow_html=True)
        time.sleep(0.35)  # pacing so the rack is actually watchable, not a instant flash

    return final_state


def _log_query(engine, question, final_state, citations) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO b1_rag_queries
                    (question, doc_type_routed, trace, answer, verified, retry_count, citations)
                VALUES (:question, :doc_type_routed, :trace, :answer, :verified, :retry_count, :citations)
                RETURNING id
                """
            ),
            {
                "question": question,
                "doc_type_routed": final_state.get("doc_type_routed"),
                "trace": json.dumps(final_state.get("trace", [])),
                "answer": final_state.get("answer", ""),
                "verified": final_state.get("verified", False),
                "retry_count": final_state.get("retry_count", 0),
                "citations": json.dumps(citations),
            },
        ).scalar_one()


def _mark_public(engine, query_id: int):
    with engine.begin() as conn:
        conn.execute(text("UPDATE b1_rag_queries SET is_public_demo = true WHERE id = :id"), {"id": query_id})


def render(engine):
    _init_state()

    st.markdown("### B1 · Multi-Agent RAG Knowledge Assistant")
    st.caption("Router → Retriever → Synthesizer → Critic, with a retry loop when the Critic isn't satisfied.")

    col_docs, col_chat = st.columns([1, 2], gap="large")

    # ---------------- Document library + upload ----------------
    with col_docs:
        st.markdown("**Knowledge base**")
        docs = list_documents(engine)

        if not docs:
            st.caption("No documents yet — upload something below.")
        for doc in docs:
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**{doc['filename']}**  \n`{doc['doc_type']}` · {doc['chunk_count']} chunks")
            if c2.button("🗑️", key=f"del_{doc['id']}"):
                delete_document(engine, doc["id"])
                st.rerun()

        st.divider()
        st.markdown("**Upload**")
        uploaded = st.file_uploader(
            "Drop .pdf / .docx / .md / .txt", type=["pdf", "docx", "md", "txt"], accept_multiple_files=True
        )

        # One category per FILE, not one shared dropdown for the whole batch —
        # a single dropdown silently mistagged every file in a multi-file
        # upload with whatever category was last selected, which broke the
        # Router's filtering for any file that wasn't actually that category.
        file_doc_types: dict[str, str] = {}
        if uploaded:
            st.caption("Assign a category to each file:")
            for f in uploaded:
                file_doc_types[f.name] = st.selectbox(
                    f.name,
                    VALID_DOC_TYPES,
                    index=VALID_DOC_TYPES.index("general"),
                    key=f"doctype_{f.name}",
                )

        if uploaded and st.button("Ingest files", type="primary", use_container_width=True):
            with st.spinner("Chunking + embedding…"):
                for f in uploaded:
                    try:
                        ingest_document(engine, f.name, f.read(), file_doc_types[f.name])
                    except ValueError as e:
                        st.error(f"{f.name}: {e}")
            st.success("Done.")
            st.rerun()

    # ---------------- Chat ----------------
    with col_chat:
        for msg in st.session_state.b1_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("result"):
                    result = msg["result"]
                    badge = "✅ verified" if result["verified"] else "⚠️ unverified"
                    retry_txt = f" · {result['retry_count']} retry" if result["retry_count"] else ""
                    st.caption(f"{badge}{retry_txt} · routed: `{result['doc_type_routed']}`")
                    for c in result["citations"]:
                        st.markdown(
                            f'<div class="citation-card"><b>{c["filename"]}</b> · '
                            f'{round(c["similarity"] * 100)}% match<br>{c["excerpt"]}…</div>',
                            unsafe_allow_html=True,
                        )
                    if not result.get("published") and st.button("⭐ Mark for public demo", key=f"pub_{msg['query_id']}"):
                        _mark_public(engine, msg["query_id"])
                        msg["result"]["published"] = True
                        st.rerun()

        question = st.chat_input("Ask a question about your documents…")
        if question:
            st.session_state.b1_messages.append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)

            with st.chat_message("assistant"):
                rack_placeholder = st.empty()
                final_state = _run_query(engine, question, st.session_state.b1_doc_type_filter, rack_placeholder)

                citations = [
                    {
                        "filename": c["filename"],
                        "excerpt": c["content"][:280],
                        "similarity": round(c["similarity"], 4),
                    }
                    for c in final_state.get("retrieved_chunks", [])
                    if c["id"] in set(final_state.get("cited_chunk_ids", []))
                ]
                query_id = _log_query(engine, question, final_state, citations)

                answer = final_state.get("answer", "")
                st.markdown(answer)
                result = {
                    "verified": final_state.get("verified", False),
                    "retry_count": final_state.get("retry_count", 0),
                    "doc_type_routed": final_state.get("doc_type_routed"),
                    "citations": citations,
                    "published": False,
                }
                badge = "✅ verified" if result["verified"] else "⚠️ unverified"
                retry_txt = f" · {result['retry_count']} retry" if result["retry_count"] else ""
                st.caption(f"{badge}{retry_txt} · routed: `{result['doc_type_routed']}`")
                for c in citations:
                    st.markdown(
                        f'<div class="citation-card"><b>{c["filename"]}</b> · '
                        f'{round(c["similarity"] * 100)}% match<br>{c["excerpt"]}…</div>',
                        unsafe_allow_html=True,
                    )

            st.session_state.b1_messages.append(
                {"role": "assistant", "content": answer, "result": result, "query_id": query_id}
            )