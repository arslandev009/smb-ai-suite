#!/usr/bin/env python3
"""Pushes rows flagged is_public_demo = true from local Postgres to the free
Neon project that public_app.py reads from. Same TRUNCATE-and-reload approach
as job-market-pipeline's sync_to_cloud.py, generalized to loop over each
project's table as it gets built (currently: b1_rag_queries only).

Usage:
    python scripts/sync_to_cloud.py
    python scripts/sync_to_cloud.py --track b1     # sync just one project's table
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from shared.config import settings  # noqa: E402
from shared.db import get_engine  # noqa: E402

# Add an entry here as each project gets its own public-facing table.
# (See TRACK_B_SMB_AI.md Section 7 for the full planned table list.)
SYNCABLE_TABLES = {
    "b1": "b1_rag_queries",
    "b2": "b2_leads_scored",
    "b3": "b3_approval_requests",
    "b4": "b4_support_tickets",
    "b5": "b5_bi_queries",
    "b6": "b6_routed_requests",
}


def sync_table(local_engine, cloud_engine, table_name: str):
    with local_engine.connect() as conn:
        df = pd.read_sql(
            text(f"SELECT * FROM {table_name} WHERE is_public_demo = true ORDER BY created_at ASC"), conn
        )

    with cloud_engine.begin() as conn:
        # Neon side: table must already exist (run db/init/01_b1_schema.sql's
        # CREATE TABLE statement against NEON_DATABASE_URL once, manually —
        # Neon doesn't run docker-entrypoint-initdb.d).
        conn.execute(text(f"TRUNCATE TABLE {table_name}"))
        if not df.empty:
            df.to_sql(table_name, conn, if_exists="append", index=False)

    print(f"  {table_name}: {len(df)} row(s) synced")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--track", choices=list(SYNCABLE_TABLES.keys()), help="Sync only this project's table")
    args = parser.parse_args()

    if not settings.neon_database_url:
        print("Set NEON_DATABASE_URL in .env first (free project at https://neon.tech).")
        return

    local_engine = get_engine(settings.database_url)
    cloud_engine = get_engine(settings.neon_database_url)

    tables = {args.track: SYNCABLE_TABLES[args.track]} if args.track else SYNCABLE_TABLES
    print(f"Syncing {len(tables)} table(s) to Neon...")
    for prefix, table_name in tables.items():
        sync_table(local_engine, cloud_engine, table_name)

    print("Done.")


if __name__ == "__main__":
    main()
