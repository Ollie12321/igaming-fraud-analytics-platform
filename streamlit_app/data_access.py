"""Thin, cached data-access layer for the Streamlit app. Reads only from the
warehouse's `marts`/`intermediate` schemas and the GIGO comparison artifact,
never from `raw`, mirroring the access boundary a real BI layer would have.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

from config import get_settings

ARTIFACT_PATH = Path("ml/artifacts/comparison_results.json")


@st.cache_resource
def get_engine():
    return create_engine(get_settings().warehouse_sqlalchemy_url)


@st.cache_data(ttl=60)
def load_fraud_summary() -> pd.DataFrame:
    return pd.read_sql(text("select * from public_marts.fct_fraud_summary order by scenario_type"), get_engine())


@st.cache_data(ttl=60)
def load_recent_flags(limit: int = 200) -> pd.DataFrame:
    query = """
        select scenario_type, entity_type, entity_id, player_id,
               triggering_event_ts, detected_ts, detection_latency_seconds, severity
        from public_staging.stg_streaming_fraud_flags
        order by detected_ts desc
        limit :limit
    """
    return pd.read_sql(text(query), get_engine(), params={"limit": limit})


@st.cache_data(ttl=60)
def load_player_ltv() -> pd.DataFrame:
    return pd.read_sql(text("select * from public_marts.fct_player_ltv"), get_engine())


@st.cache_data(ttl=60)
def load_churn_labels() -> pd.DataFrame:
    return pd.read_sql(text("select * from public_marts.fct_churn_labels"), get_engine())


@st.cache_data(ttl=60)
def load_scd_summary() -> pd.DataFrame:
    query = """
        select vip_tier, kyc_status, self_exclusion_status, risk_segment, is_current, count(*) as n
        from public_marts.dim_players_scd2
        group by 1, 2, 3, 4, 5
        order by 1, 2, 3, 4
    """
    return pd.read_sql(text(query), get_engine())


@st.cache_data(ttl=60)
def load_gigo_results() -> dict | None:
    if not ARTIFACT_PATH.exists():
        return None
    return json.loads(ARTIFACT_PATH.read_text())
