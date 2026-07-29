"""Hourly sync of fraud flags from the hot path (DynamoDB) into the warehouse
so `fct_fraud_summary` reflects flags raised since the last run.

Falls back to the local backtest (streaming/local_backtest.py) when no AWS
credentials are configured, so this DAG also runs in a pure local/CI setup.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = os.environ.get("PIPELINE_ROOT", "/opt/airflow/project")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _sync_flags(**_context):
    from pathlib import Path

    if os.environ.get("AWS_PROFILE") or os.environ.get("AWS_ACCESS_KEY_ID"):
        from streaming.sync_flags_to_warehouse import sync

        sync()
    else:
        from streaming.local_backtest import run as run_backtest

        run_backtest(Path(f"{PROJECT_ROOT}/data/raw"), Path(f"{PROJECT_ROOT}/data/processed"))
        subprocess.run(
            [
                sys.executable,
                "-m",
                "load.postgres_loader",
                "--input-dir",
                f"{PROJECT_ROOT}/data/raw",
                "--processed-dir",
                f"{PROJECT_ROOT}/data/processed",
            ],
            check=True,
            env=os.environ.copy(),
        )


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}

with DAG(
    dag_id="sync_fraud_flags",
    default_args=default_args,
    description="Replicate real-time fraud flags into the warehouse for BI",
    schedule="@hourly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["igaming", "streaming", "fraud"],
) as dag:
    PythonOperator(task_id="sync_fraud_flags_to_warehouse", python_callable=_sync_flags)
