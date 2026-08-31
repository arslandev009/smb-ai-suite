"""Shared look-and-feel across every tab in the hub — same ink/amber/teal system
used consistently so B1..B6 read as one suite, not six bolted-together apps.
Call inject() once near the top of hub_app.py / public_app.py."""
import streamlit as st

CSS = """
<style>
.block-container { padding-top: 2.4rem; }

div[data-testid="stMetric"] {
    background: rgba(232, 163, 61, 0.06);
    border: 1px solid rgba(232, 163, 61, 0.18);
    border-radius: 10px;
    padding: 14px 16px;
}

.status-pill {
    display: inline-block;
    padding: 3px 11px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.02em;
}
.status-up { background: rgba(95, 190, 176, 0.15); color: #5fbeb0; }
.status-down { background: rgba(232, 115, 95, 0.15); color: #e8735f; }

.agent-card {
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 6px;
}
.agent-card.idle { opacity: 0.45; }
.agent-card.active { border-color: #e8a33d; background: rgba(232,163,61,0.08); }
.agent-card.done { border-color: #5fbeb0; background: rgba(95,190,176,0.06); }
.agent-card.rejected { border-color: #e8735f; background: rgba(232,115,95,0.08); }

.citation-card {
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 8px;
    padding: 8px 12px;
    margin-bottom: 6px;
    background: rgba(0,0,0,0.15);
    font-size: 0.85rem;
}

/* Nav: st.pills segmented control, replacing the fragile st.tabs CSS hack.
   Gives it breathing room from the top edge and a bit of extra weight so it
   reads as a proper nav bar rather than a row of default buttons. */
div[data-testid*="ButtonGroup"] {
    margin-top: 0.4rem;
    margin-bottom: 0.2rem;
    gap: 6px;
}
div[data-testid*="ButtonGroup"] button {
    font-family: var(--font-mono, monospace);
    font-size: 0.85rem;
    letter-spacing: 0.01em;
    border-radius: 999px !important;
    padding: 6px 16px !important;
}
</style>
"""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)