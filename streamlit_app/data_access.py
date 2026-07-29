"""Thin, cached data-access layer for the Streamlit app. Reads only from the
warehouse's `marts`/`intermediate` schemas and the GIGO comparison artifact,
never from `raw`, mirroring the access boundary a real BI layer would have.

Two data sources, chosen by `settings.use_snapshot_data`:

- Live (default, local runs): queries the Docker warehouse directly, same as
  every other consumer in this repo.
- Snapshot (`USE_SNAPSHOT_DATA=true`, set on the public Community Cloud
  deployment): reads versioned parquet/JSON files from `snapshot_data/`. The
  public demo has no access to a local Docker warehouse, and giving it one
  would mean paying for an always-on public database just to serve read-only
  historical analytics, exactly the batch-vs-streaming cost argument this
  project makes elsewhere. Regenerate the snapshot with
  `python -m streamlit_app.export_snapshot`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from config import get_settings

ARTIFACT_PATH = Path("ml/artifacts/comparison_results.json")
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshot_data"


@st.cache_resource
def get_engine():
    from sqlalchemy import create_engine

    return create_engine(get_settings().warehouse_sqlalchemy_url)


def _from_snapshot(name: str) -> pd.DataFrame:
    return pd.read_parquet(SNAPSHOT_DIR / f"{name}.parquet")


def _from_live(query: str, params: dict | None = None) -> pd.DataFrame:
    from sqlalchemy import text

    return pd.read_sql(text(query), get_engine(), params=params or {})


@st.cache_data(ttl=60)
def load_fraud_summary() -> pd.DataFrame:
    if get_settings().use_snapshot_data:
        return _from_snapshot("fraud_summary")
    return _from_live("select * from public_marts.fct_fraud_summary order by scenario_type")


@st.cache_data(ttl=60)
def load_recent_flags(limit: int = 200) -> pd.DataFrame:
    if get_settings().use_snapshot_data:
        return _from_snapshot("recent_flags").head(limit)
    query = """
        select scenario_type, entity_type, entity_id, player_id,
               triggering_event_ts, detected_ts, detection_latency_seconds, severity
        from public_staging.stg_streaming_fraud_flags
        order by detected_ts desc
        limit :limit
    """
    return _from_live(query, {"limit": limit})


@st.cache_data(ttl=60)
def load_player_ltv() -> pd.DataFrame:
    if get_settings().use_snapshot_data:
        return _from_snapshot("player_ltv")
    return _from_live("select * from public_marts.fct_player_ltv")


@st.cache_data(ttl=60)
def load_churn_labels() -> pd.DataFrame:
    if get_settings().use_snapshot_data:
        return _from_snapshot("churn_labels")
    return _from_live("select * from public_marts.fct_churn_labels")


@st.cache_data(ttl=60)
def load_scd_summary() -> pd.DataFrame:
    if get_settings().use_snapshot_data:
        return _from_snapshot("scd_summary")
    query = """
        select vip_tier, kyc_status, self_exclusion_status, risk_segment, is_current, count(*) as n
        from public_marts.dim_players_scd2
        group by 1, 2, 3, 4, 5
        order by 1, 2, 3, 4
    """
    return _from_live(query)


@st.cache_data(ttl=60)
def load_gigo_results() -> dict | None:
    if get_settings().use_snapshot_data:
        snapshot_path = SNAPSHOT_DIR / "gigo_comparison.json"
        return json.loads(snapshot_path.read_text()) if snapshot_path.exists() else None
    if not ARTIFACT_PATH.exists():
        return None
    return json.loads(ARTIFACT_PATH.read_text())
