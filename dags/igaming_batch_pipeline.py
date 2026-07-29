"""Daily batch pipeline: generate/extract raw data, gate it with a Great
Expectations quality check, load it into the warehouse, then run the full dbt
DAG (staging -> intermediate -> marts, including the SCD Type 2 player
dimension), then test it.

In a production deployment, "load raw" would be an extract step against the
real OLTP/event-archive systems rather than the synthetic generator; the
generator is here so the whole pipeline is runnable end-to-end with zero
external dependencies.

The quality gate is deliberately its own task, before the warehouse load: it
catches malformed/out-of-domain source data (broken producer, upstream schema
drift) cheaply, before spending time and warehouse compute loading it. dbt
tests further down the DAG catch a different class of problem: business
logic that's only checkable *after* transformation (referential integrity,
SCD2 period validity, fraud-recall sanity).
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

DBT_DIR = f"{PROJECT_ROOT}/dbt"
DBT_ARGS = ["--project-dir", DBT_DIR, "--profiles-dir", DBT_DIR]


def _generate_synthetic_data(**_context):
    from pathlib import Path

    from datagen.simulate import run as run_simulation

    run_simulation(Path(f"{PROJECT_ROOT}/data/raw"))


def _validate_raw_data(**_context):
    from pathlib import Path

    from quality.raw_data_checks import run_checks

    report = run_checks(Path(f"{PROJECT_ROOT}/data/raw"))
    report.print_summary()
    if not report.passed:
        raise ValueError("Raw data quality gate failed. See report above.")


def _load_raw_to_warehouse(**_context):
    from pathlib import Path

    from load.postgres_loader import load_all

    load_all(Path(f"{PROJECT_ROOT}/data/raw"), Path(f"{PROJECT_ROOT}/data/processed"))


def _dbt_seed(**_context):
    subprocess.run(["dbt", "seed", *DBT_ARGS], check=True, env=os.environ.copy())


def _dbt_run(**_context):
    subprocess.run(["dbt", "run", *DBT_ARGS], check=True, env=os.environ.copy())


def _dbt_test(**_context):
    subprocess.run(["dbt", "test", *DBT_ARGS], check=True, env=os.environ.copy())


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
}

with DAG(
    dag_id="igaming_batch_pipeline",
    default_args=default_args,
    description="Batch ELT: raw -> GE quality gate -> warehouse -> dbt (staging/intermediate/marts + SCD2) -> tests",
    schedule="0 6 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["igaming", "batch", "dbt"],
) as dag:
    generate_task = PythonOperator(task_id="generate_synthetic_data", python_callable=_generate_synthetic_data)
    validate_task = PythonOperator(task_id="validate_raw_data_quality", python_callable=_validate_raw_data)
    load_task = PythonOperator(task_id="load_raw_to_warehouse", python_callable=_load_raw_to_warehouse)
    seed_task = PythonOperator(task_id="dbt_seed", python_callable=_dbt_seed)
    run_task = PythonOperator(task_id="dbt_run", python_callable=_dbt_run)
    test_task = PythonOperator(task_id="dbt_test", python_callable=_dbt_test)

    generate_task >> validate_task >> load_task >> seed_task >> run_task >> test_task
