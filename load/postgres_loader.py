"""Loads the generated parquet files into the local Postgres warehouse's
`raw` schema.

Postgres stands in for Redshift here so the project can run entirely for
free/locally; see the README for why (Redshift/MWAA always-on cost vs.
the near-free serverless pieces that are actually deployed to AWS).

Uses COPY (not row-by-row INSERT) so a multi-million row `game_rounds`
table loads in seconds rather than minutes.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from config import get_settings

RAW_SCHEMA = "raw"

TABLES = [
    "players",
    "devices",
    "player_attribute_history",
    "sessions",
    "login_events",
    "game_rounds",
    "payments",
    "bonuses",
    "fraud_ground_truth",
    "streaming_fraud_flags",
]


def _copy_dataframe(conn, df: pd.DataFrame, schema: str, table: str) -> None:
    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False)
    buffer.seek(0)
    raw_conn = conn.connection
    with raw_conn.cursor() as cur:
        cur.copy_expert(
            f'COPY "{schema}"."{table}" FROM STDIN WITH (FORMAT csv, NULL \'\')',
            buffer,
        )
    raw_conn.commit()


def load_all(input_dir: Path, processed_dir: Path | None = None) -> None:
    settings = get_settings()
    engine = create_engine(settings.warehouse_sqlalchemy_url)

    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {RAW_SCHEMA}"))

    search_dirs = [input_dir] + ([processed_dir] if processed_dir else [])

    for table in TABLES:
        path = next((d / f"{table}.parquet" for d in search_dirs if (d / f"{table}.parquet").exists()), None)
        if path is None:
            print(f"  skipping {table} (no file found in {search_dirs})")
            continue

        df = pd.read_parquet(path)
        with engine.begin() as conn:
            # CASCADE handles reloads after `dbt run` has already created
            # staging views on top of these raw tables.
            conn.execute(text(f'DROP TABLE IF EXISTS "{RAW_SCHEMA}"."{table}" CASCADE'))
            df.head(0).to_sql(table, conn, schema=RAW_SCHEMA, if_exists="replace", index=False)

        with engine.connect() as conn:
            _copy_dataframe(conn, df, RAW_SCHEMA, table)

        print(f"  loaded {RAW_SCHEMA}.{table} ({len(df):,} rows)")

    print("Raw load complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    load_all(args.input_dir, args.processed_dir)


if __name__ == "__main__":
    main()
