"""
SMB AI Suite — LOCAL full-control hub.
One Streamlit app, one project selected at a time via a pill nav (B1..B6),
full read/write against the local Postgres + your native llama.cpp servers.
Mirrors job-market-pipeline's local dashboard (src/dashboard/app.py) —
pipeline control lives here; the public deployment (public_app.py) only
ever shows a read-only snapshot.

Run:
    streamlit run hub_app.py
"""
import streamlit as st

from shared.config import settings
from shared.db import get_engine
from shared.llm_client import check_health
from shared.ui_theme import inject

st.set_page_config(page_title="SMB AI Suite — Local", page_icon="🧩", layout="wide")
inject()


@st.cache_resource
def _engine():
    engine = get_engine()
    try:
        from shared.migrate import run_migrations

        run_migrations(engine)
    except Exception as e:
        # Don't crash the whole app over a migration hiccup — surface it in
        # the sidebar instead, since most tabs might still work fine.
        st.session_state["_migration_error"] = str(e)
    return engine


# ---------------- Sidebar: environment status ----------------
with st.sidebar:
    st.title("🧩 SMB AI Suite")
    st.caption("Local hub · full pipeline control")

    if st.button("🔄 Refresh connections", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()

    st.divider()
    st.markdown("**llama.cpp servers**")
    health = check_health()
    for name, key in [("Embedding", "embedding"), ("Generation", "generation")]:
        pill = "status-up" if health[key] else "status-down"
        text = f"● {name}: {'online' if health[key] else 'offline'}"
        st.markdown(f'<span class="status-pill {pill}">{text}</span>', unsafe_allow_html=True)
    if not all(health.values()):
        st.caption("Start missing servers natively — see README.md.")

    st.divider()
    st.markdown("**Database**")
    try:
        _engine().connect().close()
        st.markdown('<span class="status-pill status-up">● Postgres: connected</span>', unsafe_allow_html=True)
        if "_migration_error" in st.session_state:
            st.caption(f"⚠️ Schema auto-migration hit an error: {st.session_state['_migration_error']}")
    except Exception as e:
        st.markdown('<span class="status-pill status-down">● Postgres: unreachable</span>', unsafe_allow_html=True)
        st.caption(f"`docker compose up -d postgres` — {e}")

    st.divider()
    st.caption("Publishing to the public portfolio demo:")
    st.caption("Click ⭐ Mark for public demo on any result you want shown publicly — the public app reads directly from this same database, filtered to those rows. No sync step needed.")


# ---------------- Project nav (pill segmented control, not st.tabs) ----------------
PROJECTS = {
    "B1 · Knowledge RAG": "b1",
    "B2 · Lead Scoring": "b2",
    "B3 · Approvals": "b3",
    "B4 · Support Triage": "b4",
    "B5 · BI Analyst": "b5",
    "B6 · Ops Manager": "b6",
}

selected_label = st.pills(
    "Project",
    options=list(PROJECTS.keys()),
    default="B1 · Knowledge RAG",
    label_visibility="collapsed",
)
selected = PROJECTS.get(selected_label, "b1")
st.divider()

# ---------------- Render only the selected project ----------------
if selected == "b1":
    from projects.b1_rag_knowledge.tab import render as render_b1

    render_b1(_engine())

elif selected == "b2":
    from projects.b2_lead_scoring.tab import render as render_b2

    render_b2(_engine())

elif selected == "b3":
    from projects.b3_approval_workflow.tab import render as render_b3

    render_b3(_engine())

elif selected == "b4":
    from projects.b4_support_triage.tab import render as render_b4

    render_b4(_engine())

elif selected == "b5":
    from projects.b5_bi_reporting.tab import render as render_b5

    render_b5(_engine())

elif selected == "b6":
    from projects.b6_ops_manager.tab import render as render_b6

    render_b6(_engine())