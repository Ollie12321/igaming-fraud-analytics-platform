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

import hashlib
import json
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from config import get_settings

ARTIFACT_PATH = Path("ml/artifacts/comparison_results.json")
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshot_data"
REPO_ROOT = Path(__file__).resolve().parent.parent


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
def load_scd_timeline() -> pd.DataFrame:
    """Full change history for a curated set of players with the most
    attribute changes: the most interesting timelines to visualise.
    """
    if get_settings().use_snapshot_data:
        return _from_snapshot("scd_timeline")
    query = """
        select player_id, country, valid_from, valid_to, is_current,
               vip_tier, kyc_status, self_exclusion_status, risk_segment
        from public_marts.dim_players_scd2
        where player_id in (
            select player_id from public_marts.dim_players_scd2
            group by 1 order by count(*) desc limit 15
        )
        order by player_id, valid_from
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


@st.cache_data(ttl=60)
def load_scenario_exposure() -> pd.DataFrame:
    """Real average GBP value at risk per undetected event, by scenario.
    Backs the batch-vs-streaming exposure calculator.
    """
    if get_settings().use_snapshot_data:
        return _from_snapshot("scenario_exposure")
    query = """
        with amounts as (
            select gt.scenario_type, gt.entity_type, gt.entity_id, p.amount_gbp as amount_gbp
            from public_staging.stg_fraud_ground_truth gt
            join public_staging.stg_payments p on gt.entity_id = p.payment_id
            where gt.entity_type = 'payment'

            union all

            select gt.scenario_type, gt.entity_type, gt.entity_id, b.bonus_amount::numeric as amount_gbp
            from public_staging.stg_fraud_ground_truth gt
            join raw.bonuses b on gt.entity_id = b.bonus_id
            where gt.entity_type = 'bonus_claim'

            union all

            select gt.scenario_type, gt.entity_type, gt.entity_id, s.session_stake as amount_gbp
            from public_staging.stg_fraud_ground_truth gt
            join (
                select session_id, sum(stake_amount) as session_stake
                from raw.game_rounds
                group by 1
            ) s on gt.entity_id = s.session_id
            where gt.entity_type = 'session'
        )
        select scenario_type, entity_type, count(*) as n_events, round(avg(amount_gbp)::numeric, 2) as avg_amount_gbp
        from amounts
        group by 1, 2
        order by 1, 2
    """
    return _from_live(query)


@st.cache_data(ttl=60)
def load_erasure_demo() -> dict:
    """A handful of sample players spanning distinct KYC/self-exclusion
    states, with a precomputed real dry-run erasure result for each. Always
    reads the bundled snapshot regardless of data-source mode: this dashboard
    never writes to any database, live or otherwise, and the demo is
    illustrative rather than a query surface. Regenerate with
    `python -m streamlit_app.export_snapshot`.
    """
    path = SNAPSHOT_DIR / "erasure_demo.json"
    return json.loads(path.read_text()) if path.exists() else {"players": [], "retained_reasons": {}}


def pseudonymise(value: str) -> str:
    """Same deterministic, salted, one-way hash as `governance/erasure.py`'s
    `_pseudonym`, duplicated here (rather than imported) so the dashboard
    doesn't need SQLAlchemy just to demo what erasure does to one field.
    """
    salt = get_settings().erasure_pseudonymisation_salt
    digest = hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()
    return f"erased:{digest[:32]}"


@st.cache_data(ttl=300)
def load_classification_summary() -> pd.DataFrame:
    """Column-level data classification, PII flag, and retention category,
    parsed straight from the dbt schema.yml files: the same metadata that
    governs access boundaries and the erasure logic, not a separate copy of
    it. Doesn't need a database.
    """
    rows = []
    for schema_path in [
        REPO_ROOT / "dbt" / "models" / "staging" / "schema.yml",
        REPO_ROOT / "dbt" / "models" / "marts" / "schema.yml",
    ]:
        if not schema_path.exists():
            continue
        doc = yaml.safe_load(schema_path.read_text())
        for model in doc.get("models", []):
            for column in model.get("columns", []):
                meta = column.get("meta", {})
                if not meta:
                    continue
                rows.append(
                    {
                        "model": model["name"],
                        "column": column["name"],
                        "classification": meta.get("classification", "unclassified"),
                        "pii": bool(meta.get("pii", False)),
                        "retention_category": meta.get("retention_category", "unspecified"),
                    }
                )
    return pd.DataFrame(rows)
