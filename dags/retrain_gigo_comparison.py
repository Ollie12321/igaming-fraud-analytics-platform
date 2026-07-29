"""Weekly refresh of the naive-vs-engineered churn model comparison that
backs the Data Quality tab in the Streamlit app. Depends on the batch
pipeline having already refreshed the marts for the current week.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.sensors.external_task import ExternalTaskSensor

PROJECT_ROOT = os.environ.get("PIPELINE_ROOT", "/opt/airflow/project")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _run_comparison(**_context):
    from ml.naive_vs_engineered import run

    run()


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="retrain_gigo_comparison",
    default_args=default_args,
    description="Refresh the naive-vs-engineered churn model comparison",
    schedule="0 7 * * 1",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["igaming", "ml", "data-quality"],
) as dag:
    wait_for_batch = ExternalTaskSensor(
        task_id="wait_for_batch_pipeline",
        external_dag_id="igaming_batch_pipeline",
        external_task_id="dbt_test",
        allowed_states=["success"],
        execution_delta=timedelta(hours=1),
        timeout=3600,
        mode="reschedule",
    )

    comparison_task = PythonOperator(task_id="run_naive_vs_engineered_comparison", python_callable=_run_comparison)

    wait_for_batch >> comparison_task
