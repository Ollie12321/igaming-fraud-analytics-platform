"""Export a versioned data snapshot for the public Streamlit Community Cloud
deployment, which has no network access to the local Docker warehouse.

Runs the exact same queries as `data_access.py`'s live path against the local
warehouse and writes the results to `streamlit_app/snapshot_data/`, plus a
copy of the GIGO comparison artifact. Commit the output: it's the only data
source the public demo has.

Usage:
    python -m streamlit_app.export_snapshot
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from config import get_settings
from governance.erasure import RETAINED_TABLES, erase_player

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshot_data"
GIGO_ARTIFACT = Path("ml/artifacts/comparison_results.json")

QUERIES = {
    "fraud_summary": "select * from public_marts.fct_fraud_summary order by scenario_type",
    "recent_flags": """
        select scenario_type, entity_type, entity_id, player_id,
               triggering_event_ts, detected_ts, detection_latency_seconds, severity
        from public_staging.stg_streaming_fraud_flags
        order by detected_ts desc
        limit 200
    """,
    "player_ltv": "select * from public_marts.fct_player_ltv",
    "churn_labels": "select * from public_marts.fct_churn_labels",
    "scd_summary": """
        select vip_tier, kyc_status, self_exclusion_status, risk_segment, is_current, count(*) as n
        from public_marts.dim_players_scd2
        group by 1, 2, 3, 4, 5
        order by 1, 2, 3, 4
    """,
    # Real average GBP value at risk per undetected event, per scenario. Feeds
    # the batch-vs-streaming exposure calculator: it's what makes "detecting
    # this an hour later" translate into a pound figure instead of a vibe.
    "scenario_exposure": """
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
        select
            scenario_type,
            entity_type,
            count(*) as n_events,
            round(avg(amount_gbp)::numeric, 2) as avg_amount_gbp
        from amounts
        group by 1, 2
        order by 1, 2
    """,
}


def _export_tables(engine) -> None:
    for name, query in QUERIES.items():
        df = pd.read_sql(text(query), engine)
        out_path = SNAPSHOT_DIR / f"{name}.parquet"
        df.to_parquet(out_path, index=False)
        print(f"  wrote {out_path} ({len(df)} rows)")


# Tables keyed by player_id directly vs. by (entity_type, entity_id), where
# a "player"-scoped ground-truth/flag row uses the player_id as entity_id.
_ENTITY_KEYED_TABLES = {"fraud_ground_truth", "streaming_fraud_flags"}


def _retained_row_counts(conn, player_id: str) -> dict[str, int]:
    counts = {}
    for table in RETAINED_TABLES:
        if table == "devices":
            continue
        if table in _ENTITY_KEYED_TABLES:
            query = f'select count(*) from "raw"."{table}" where entity_type = \'player\' and entity_id = :pid'
        else:
            query = f'select count(*) from "raw"."{table}" where player_id = :pid'
        counts[table] = conn.execute(text(query), {"pid": player_id}).scalar()
    return counts


def _export_erasure_demo(engine) -> None:
    """Sample a handful of players spanning distinct KYC/self-exclusion states
    and precompute what a real erasure request against each would do: rows
    pseudonymised, rows retained, and one real "before" value per player so
    the dashboard can hash it live in front of the visitor. No writes happen
    here or in the dashboard: this reads the same information `governance
    erasure.py --dry-run` would print, plus one sample value for display.
    """
    candidates = pd.read_sql(
        text("""
            select distinct on (kyc_status, self_exclusion_status)
                player_id, vip_tier, kyc_status, self_exclusion_status, risk_segment
            from public_marts.dim_players_scd2
            where is_current = true
            order by kyc_status, self_exclusion_status, player_id
            """),
        engine,
    )

    demo_players = []
    with engine.connect() as conn:
        for row in candidates.itertuples(index=False):
            pid = row.player_id
            result = erase_player(pid, dry_run=True)

            sample_ip = conn.execute(
                text("select ip_address from raw.sessions where player_id = :pid limit 1"), {"pid": pid}
            ).scalar()
            dob = conn.execute(
                text("select date_of_birth from raw.players where player_id = :pid"), {"pid": pid}
            ).scalar()

            demo_players.append(
                {
                    "player_id": pid,
                    "vip_tier": row.vip_tier,
                    "kyc_status": row.kyc_status,
                    "self_exclusion_status": row.self_exclusion_status,
                    "risk_segment": row.risk_segment,
                    "sample_ip_address": sample_ip,
                    "date_of_birth": str(dob) if dob is not None else None,
                    "rows_affected": result.rows_affected,
                    "retained_row_counts": _retained_row_counts(conn, pid),
                }
            )

    out_path = SNAPSHOT_DIR / "erasure_demo.json"
    out_path.write_text(
        json.dumps({"players": demo_players, "retained_reasons": RETAINED_TABLES}, indent=2, default=str)
    )
    print(f"  wrote {out_path} ({len(demo_players)} sample players)")


def main() -> None:
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    engine = create_engine(get_settings().warehouse_sqlalchemy_url)

    _export_tables(engine)
    _export_erasure_demo(engine)

    if GIGO_ARTIFACT.exists():
        dest = SNAPSHOT_DIR / "gigo_comparison.json"
        shutil.copy(GIGO_ARTIFACT, dest)
        print(f"  wrote {dest}")
    else:
        print(f"  skipped gigo_comparison.json: {GIGO_ARTIFACT} not found; run python -m ml.naive_vs_engineered first")

    manifest = {"source": "export_snapshot.py", "tables": list(QUERIES.keys()) + ["erasure_demo", "gigo_comparison"]}
    (SNAPSHOT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nSnapshot written to {SNAPSHOT_DIR}")


if __name__ == "__main__":
    main()
