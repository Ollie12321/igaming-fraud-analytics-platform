"""Replicates fraud flags from the hot path (DynamoDB) into the warehouse's
`raw.streaming_fraud_flags` table for BI/reporting (fct_fraud_summary).

This is the AWS-connected counterpart to `streaming.local_backtest`, which
produces the same target table for local/CI runs without needing a live
DynamoDB table. The warehouse copy is intentionally never on the path that
blocks a transaction: DynamoDB itself is authoritative for that.

Usage:
    python -m streaming.sync_flags_to_warehouse
"""

from __future__ import annotations

import io

import boto3
import pandas as pd
from sqlalchemy import create_engine, text

from config import get_settings

RAW_SCHEMA = "raw"
TABLE = "streaming_fraud_flags"


def fetch_flags(table_name: str, region: str) -> pd.DataFrame:
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    items: list[dict] = []
    response = table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return pd.DataFrame(items)


def sync() -> int:
    settings = get_settings()
    flags = fetch_flags(settings.dynamodb_fraud_flags_table, settings.aws_region)

    if flags.empty:
        print("No flags found in DynamoDB, nothing to sync.")
        return 0

    for col in ("triggering_event_ts", "detected_ts"):
        flags[col] = pd.to_datetime(flags[col])

    engine = create_engine(settings.warehouse_sqlalchemy_url)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {RAW_SCHEMA}"))
        conn.execute(text(f'DROP TABLE IF EXISTS "{RAW_SCHEMA}"."{TABLE}" CASCADE'))
        flags.head(0).to_sql(TABLE, conn, schema=RAW_SCHEMA, if_exists="replace", index=False)

    buffer = io.StringIO()
    flags.to_csv(buffer, index=False, header=False)
    buffer.seek(0)
    with engine.connect() as conn:
        raw_conn = conn.connection
        with raw_conn.cursor() as cur:
            cur.copy_expert(f'COPY "{RAW_SCHEMA}"."{TABLE}" FROM STDIN WITH (FORMAT csv, NULL \'\')', buffer)
        raw_conn.commit()

    print(f"Synced {len(flags):,} flags into {RAW_SCHEMA}.{TABLE}")
    return len(flags)


if __name__ == "__main__":
    sync()
