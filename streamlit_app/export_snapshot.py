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
}


def main() -> None:
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    engine = create_engine(get_settings().warehouse_sqlalchemy_url)

    for name, query in QUERIES.items():
        df = pd.read_sql(text(query), engine)
        out_path = SNAPSHOT_DIR / f"{name}.parquet"
        df.to_parquet(out_path, index=False)
        print(f"  wrote {out_path} ({len(df)} rows)")

    if GIGO_ARTIFACT.exists():
        dest = SNAPSHOT_DIR / "gigo_comparison.json"
        shutil.copy(GIGO_ARTIFACT, dest)
        print(f"  wrote {dest}")
    else:
        print(f"  skipped gigo_comparison.json: {GIGO_ARTIFACT} not found; run python -m ml.naive_vs_engineered first")

    manifest = {"source": "export_snapshot.py", "tables": list(QUERIES.keys())}
    (SNAPSHOT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nSnapshot written to {SNAPSHOT_DIR}")


if __name__ == "__main__":
    main()
