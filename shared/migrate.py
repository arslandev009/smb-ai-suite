"""Auto-applies every schema file in db/init/ on startup, in filename order.
Every statement in those files uses CREATE ... IF NOT EXISTS, so re-running
this on every app start is always safe — it's a no-op once a table already
exists. This is what replaces manually pasting each project's schema into
Neon's SQL Editor: add a project's schema file to db/init/, restart the hub,
and the table is just there."""
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

DB_INIT_DIR = Path(__file__).resolve().parent.parent / "db" / "init"


def _statements(sql: str) -> list[str]:
    """Naive split on ';' — fine here since our schema files are plain DDL
    with no semicolons inside string literals or function bodies."""
    out = []
    for raw in sql.split(";"):
        lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("--")]
        cleaned = "\n".join(lines).strip()
        if cleaned:
            out.append(cleaned)
    return out


def run_migrations(engine: Engine) -> list[str]:
    """Returns the list of .sql filenames that were applied (attempted)."""
    if not DB_INIT_DIR.exists():
        return []

    applied = []
    with engine.begin() as conn:
        for path in sorted(DB_INIT_DIR.glob("*.sql")):
            for stmt in _statements(path.read_text()):
                conn.execute(text(stmt))
            applied.append(path.name)
    return applied
