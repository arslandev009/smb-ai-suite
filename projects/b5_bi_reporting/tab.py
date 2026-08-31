"""Renders B5's tab: ask a plain-English question about the suite's own
data, see the generated SQL, see it validated (and rejected transparently
if it fails), see the results as a table + chart, and get a plain-English
summary of what they show.
"""
import json

import pandas as pd
import streamlit as st
from sqlalchemy import text

from projects.b5_bi_reporting.graph import ALLOWED_TABLES, build_graph

EXAMPLES = [
    "How many leads have a final score above 0.5?",
    "How many support tickets were escalated vs auto-resolved?",
    "What's the average final score of scored leads?",
    "How many documents have been uploaded, by category?",
]


def _init_state():
    st.session_state.setdefault("b5_results", [])


def _run_question(engine, question: str, progress_placeholder) -> dict:
    graph = build_graph(engine)
    final_state: dict = {}
    for event in graph.stream({"question": question, "trace": []}, stream_mode="values"):
        final_state = event
        trace = event.get("trace", [])
        if trace:
            progress_placeholder.caption(f"→ {trace[-1]['agent']}: {trace[-1].get('detail', '')}")
    return final_state


def _log_query(engine, state: dict) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO b5_bi_queries
                    (question, generated_sql, validation_passed, rejection_reason, row_count,
                     sample_results, chart_type, chart_x, chart_y, insight_summary, trace)
                VALUES
                    (:question, :generated_sql, :validation_passed, :rejection_reason, :row_count,
                     :sample_results, :chart_type, :chart_x, :chart_y, :insight_summary, :trace)
                RETURNING id
                """
            ),
            {
                "question": state["question"],
                "generated_sql": state.get("generated_sql", ""),
                "validation_passed": state.get("validation_passed", False),
                "rejection_reason": state.get("rejection_reason"),
                "row_count": state.get("row_count"),
                "sample_results": json.dumps(state.get("rows", [])[:20]),
                "chart_type": state.get("chart_type"),
                "chart_x": state.get("chart_x"),
                "chart_y": state.get("chart_y"),
                "insight_summary": state.get("insight_summary"),
                "trace": json.dumps(state.get("trace", [])),
            },
        ).scalar_one()


def _mark_public(engine, query_id: int):
    with engine.begin() as conn:
        conn.execute(text("UPDATE b5_bi_queries SET is_public_demo = true WHERE id = :id"), {"id": query_id})


def render(engine):
    _init_state()

    st.markdown("### B5 · BI / Reporting Analyst")
    st.caption("SQL Generator → **Validator** (blocks unsafe SQL, then executes read-only) → Chart → Insight Summarizer.")

    with st.expander("What data can I ask about?"):
        for t, cols in ALLOWED_TABLES.items():
            st.markdown(f"- **{t}**: {cols}")

    cols = st.columns(len(EXAMPLES))
    for c, example in zip(cols, EXAMPLES):
        if c.button(example, use_container_width=True):
            st.session_state["b5_draft"] = example

    question = st.text_input(
        "Ask a question about the suite's data", value=st.session_state.get("b5_draft", ""),
        placeholder="e.g. How many leads scored above 0.5?",
    )

    if st.button("Run", type="primary") and question.strip():
        progress_placeholder = st.empty()
        state = _run_question(engine, question.strip(), progress_placeholder)
        state["question"] = question.strip()
        progress_placeholder.empty()
        query_id = _log_query(engine, state)
        st.session_state.b5_results.insert(0, {"id": query_id, **state})
        st.rerun()

    if st.session_state.b5_results:
        st.divider()
        st.markdown("**Recent questions**")
        for r in st.session_state.b5_results:
            with st.container(border=True):
                st.markdown(f"**{r['question']}**")

                with st.expander("Generated SQL"):
                    st.code(r.get("generated_sql", ""), language="sql")

                if not r.get("validation_passed"):
                    st.error(f"🛑 Query rejected by the Validator: {r.get('rejection_reason')}")
                else:
                    rows = r.get("rows", [])
                    if rows:
                        df = pd.DataFrame(rows)
                        st.dataframe(df, use_container_width=True)

                        chart_type = r.get("chart_type")
                        x, y = r.get("chart_x"), r.get("chart_y")
                        if chart_type in ("bar", "line") and x in df.columns and y in df.columns:
                            chart_df = df.set_index(x)[[y]]
                            if chart_type == "bar":
                                st.bar_chart(chart_df)
                            else:
                                st.line_chart(chart_df)
                    else:
                        st.info("Query ran successfully but returned no rows.")

                    if r.get("insight_summary"):
                        st.markdown(f"*Insight:* {r['insight_summary']}")

                with st.expander("Agent reasoning trail"):
                    for step in r.get("trace", []):
                        st.markdown(f"- **{step['agent']}** — {step.get('detail', '')}")

                if r.get("validation_passed") and st.button("⭐ Mark for public demo", key=f"pub_b5_{r['id']}"):
                    _mark_public(engine, r["id"])
                    st.toast("Marked.")
