# SMB AI Suite

One repo, one Postgres, one local Streamlit hub, one public read-only
Streamlit Cloud showcase — six LangGraph-orchestrated multi-agent business
systems (B1–B6), built and shown as a single suite instead of six
disconnected projects.

Pure Python throughout. No FastAPI, no Node, no Next.js — Streamlit for every
UI, exactly the pattern already proven out on job-market-pipeline.

---

## Architecture

```
                    docker-compose.yml  (ONE file, ONE service)
                              │
                          Postgres + pgvector
                    (b1_..., b2_..., ... prefixed tables,
                     one database, shared by every project)
                              │
              ┌───────────────┼────────────────┐
              │                                 │
        hub_app.py                     scripts/sync_to_cloud.py
   (local, full control,                        │
    runs on the host, not                        ▼
    in Docker — needs to                  Neon (free tier)
    talk to native llama.cpp)              cloud snapshot
              │                                 │
        projects/b1_rag_knowledge/              ▼
        projects/b2_lead_scoring/         public_app.py
        ...                          (deployed to Streamlit
        each with its own tab.py      Community Cloud — read-only,
        rendered as a tab in hub_app  no LangGraph, no llama.cpp calls)
```

**Why only one Docker service:** exactly like job-market-pipeline's dashboard,
`hub_app.py` needs to run natively — it talks directly to your llama.cpp
servers on localhost. Only Postgres benefits from being containerized
(clean, reproducible, versioned schema via `db/init/*.sql`). When B3 needs
n8n, it becomes a second service in this same `docker-compose.yml` — the
suite never grows past one compose file.

**Why one database, prefixed tables:** `b1_documents`, `b1_chunks`,
`b2_leads`, `b3_approvals`, etc., all in one `smb_ai_suite` database. Simpler
connection management than six databases, and prefix-namespacing is enough
isolation for a portfolio project.

**Why the public/local split:** a public Streamlit Cloud app is 1 CPU / ~1GB
RAM — fine for `st.dataframe()` over a Neon snapshot, not fine for live
LangGraph + LLM inference from anonymous traffic. So `public_app.py` never
executes anything — it only replays real runs you chose to publish (marked
⭐ in the local hub, then pushed with `sync_to_cloud.py`), same as
job-market-pipeline's `app_public.py` reading a synced snapshot instead of
live Postgres.

---

## Prerequisites

Same as job-market-pipeline:
- Docker Desktop
- `llama-server.exe` (Vulkan) + your Qwen 9B GGUF
- A small **embedding model** GGUF (e.g. `nomic-embed-text-v1.5.Q8_0.gguf`) —
  new requirement for B1 specifically, runs as a second llama-server instance
  alongside the 9B generator

Python 3.11+ and a virtualenv — no Node.js needed anywhere in this project.

---

## 1. Start the two llama.cpp servers (native Windows)

```powershell
llama-server.exe -m models\nomic-embed-text-v1.5.Q8_0.gguf --embedding --port 8090
llama-server.exe -m models\qwen3.5-9b-q4_k_m.gguf --port 8091
```

## 2. Environment + Postgres

```powershell
copy .env.example .env
# edit .env: set a real POSTGRES_PASSWORD, matching DATABASE_URL

docker compose up -d postgres
```
`db/init/*.sql` runs automatically on first boot — creates the `vector`
extension and every project's schema (currently B1's; B2–B6 are numbered
placeholders, filled in as each project gets built).

## 3. Python environment

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Run the local hub

```powershell
streamlit run hub_app.py
```
Opens at `http://localhost:8501`. The sidebar shows live status for both
llama.cpp servers and Postgres. B1's tab is fully functional; B2–B6 show
what's coming, in build order.

Seed B1 with the two included sample docs by uploading them from the
sidebar's upload widget inside the B1 tab (`projects/b1_rag_knowledge/sample_docs/`).

## 5. Publish a public portfolio demo (optional, once you have good runs)

1. In the local hub, ask a few good questions in B1's chat.
2. Click **⭐ Mark for public demo** on the runs worth showing.
3. Create a free project at [neon.tech](https://neon.tech), then run each
   project's schema against it once (Neon doesn't auto-run
   `docker-entrypoint-initdb.d` the way local Docker does):
   ```powershell
   psql "$NEON_DATABASE_URL" -f db/init/00_extensions.sql
   psql "$NEON_DATABASE_URL" -f db/init/01_b1_schema.sql
   ```
4. Set `NEON_DATABASE_URL` in `.env`, then:
   ```powershell
   python scripts/sync_to_cloud.py
   ```
5. Deploy `public_app.py` to Streamlit Community Cloud, with
   `NEON_DATABASE_URL` set in that app's **Secrets** (not your local `.env`).

---

## Project layout

```
smb-ai-suite/
├── docker-compose.yml         ← the ONE container: Postgres + pgvector
├── .env.example
├── requirements.txt           ← one shared environment for everything
├── hub_app.py                 ← LOCAL hub — st.tabs(), full control
├── public_app.py              ← PUBLIC snapshot — deploy this to Streamlit Cloud
├── db/init/
│   ├── 00_extensions.sql
│   ├── 01_b1_schema.sql       ← done
│   └── 02..06_*_stub.sql      ← placeholders, filled in as each project is built
├── shared/                    ← used by every project
│   ├── config.py              ← settings from .env
│   ├── db.py                  ← sync SQLAlchemy engine + pgvector search
│   ├── llm_client.py          ← sync llama.cpp client (embed / generate / health)
│   └── ui_theme.py            ← shared CSS, same look across all six tabs
├── projects/
│   ├── b1_rag_knowledge/      ← DONE
│   │   ├── graph.py           ← LangGraph: Router → Retriever → Synthesizer → Critic
│   │   ├── ingestion.py       ← file loading, chunking, embed+insert
│   │   ├── tab.py             ← this project's Streamlit tab
│   │   └── sample_docs/
│   ├── b2_lead_scoring/       ← empty, next up
│   ├── b3_approval_workflow/
│   ├── b4_support_triage/
│   ├── b5_bi_reporting/
│   └── b6_ops_manager/
└── scripts/
    └── sync_to_cloud.py       ← local -> Neon snapshot sync (loops over every project's table)
```

---

## Build order

B1 → B2 → B3 → B4 → B5 → B6, strictly in sequence — B2–B5 all lean on B1's
retrieval pattern, B6 needs all five finished before a supervisor-of-
supervisors makes sense. This skeleton has all six slots ready; each gets
built out fully (graph, ingestion/tools, tab, schema) when its turn comes,
not stubbed shallow.

## What's already validated in this scaffold

- `docker-compose.yml` — YAML-valid, one service
- All shared modules (`config`, `db`, `llm_client`, `ui_theme`) import cleanly
- B1's LangGraph graph compiles, including the Critic → Retriever retry cycle
- B1's chunker runs correctly
- Both `hub_app.py` and `public_app.py` boot cleanly under Streamlit with no
  runtime errors (verified headless before packaging)

## Known limitations (by design)

- No auth / multi-tenant access control
- No OCR (swap `pypdf` for `unstructured` if a client's PDFs are scanned)
- B1's Router has 4 fixed categories (`hr`/`product`/`policy`/`general`) —
  swap for a real client's taxonomy in `projects/b1_rag_knowledge/graph.py`
