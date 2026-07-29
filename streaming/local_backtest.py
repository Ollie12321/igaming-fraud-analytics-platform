"""Replays the generated historical events through the exact same rule logic
that runs in the deployed Lambda, using an in-memory state store instead of
DynamoDB.

This exists for two reasons:
  1. It lets the whole pipeline be demoed/tested/CI'd for free, with no AWS
     account required.
  2. It's what actually produces `data/processed/streaming_fraud_flags.parquet`,
     which gets loaded into the warehouse so `fct_fraud_summary` can report a
     real, reproducible precision/recall/latency number rather than an
     assumed one.

Usage:
    python -m streaming.local_backtest --input-dir data/raw --output-dir data/processed
"""

from __future__ import annotations

import argparse
import time
import uuid
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from datagen.reference_data import FX_RATES_TO_GBP
from streaming.fraud_rules import rules
from streaming.fraud_rules.state_store import InMemoryStateStore

RULES_BY_EVENT_TYPE = {
    "login": [rules.check_account_takeover],
    "payment": [rules.check_card_testing, rules.check_account_takeover, rules.check_structuring],
    "bonus_claim": [rules.check_bonus_abuse_ring],
    "game_round": [rules.check_bot_betting],
}


def _build_event_stream(input_dir: Path) -> list[dict]:
    events: list[dict] = []

    logins = pd.read_parquet(input_dir / "login_events.parquet")
    for row in logins.itertuples(index=False):
        events.append(
            {
                "event_type": "login",
                "entity_id": row.login_id,
                "player_id": row.player_id,
                "device_id": row.device_id,
                "ts": row.ts,
            }
        )

    payments = pd.read_parquet(input_dir / "payments.parquet")
    for row in payments.itertuples(index=False):
        events.append(
            {
                "event_type": "payment",
                "entity_id": row.payment_id,
                "player_id": row.player_id,
                "device_id": row.device_id,
                "ts": row.ts,
                "payment_type": row.payment_type,
                "amount": row.amount,
                "currency": row.currency,
                "amount_gbp": row.amount * FX_RATES_TO_GBP.get(row.currency, 1.0),
                "card_bin": row.card_bin,
            }
        )

    bonuses = pd.read_parquet(input_dir / "bonuses.parquet")
    for row in bonuses.itertuples(index=False):
        events.append(
            {
                "event_type": "bonus_claim",
                "entity_id": row.bonus_id,
                "player_id": row.player_id,
                "device_id": row.device_id,
                "ts": row.claim_ts,
                "bonus_type": row.bonus_type,
            }
        )

    game_rounds = pd.read_parquet(input_dir / "game_rounds.parquet")
    for row in game_rounds.itertuples(index=False):
        events.append(
            {
                "event_type": "game_round",
                "entity_id": row.round_id,
                "player_id": row.player_id,
                "session_id": row.session_id,
                "ts": row.ts,
            }
        )

    events.sort(key=lambda e: e["ts"])
    return events


def _build_self_exclusion_registry(input_dir: Path) -> dict:
    history = pd.read_parquet(input_dir / "player_attribute_history.parquet")
    excluded = history[history["self_exclusion_status"] == "self_excluded"]
    excluded = excluded.sort_values("effective_ts").drop_duplicates("player_id", keep="first")
    return dict(zip(excluded["player_id"], excluded["effective_ts"]))


def run(input_dir: Path, output_dir: Path, processing_latency_seconds: float = 1.5) -> pd.DataFrame:
    t0 = time.time()
    print("Building unified event stream...")
    events = _build_event_stream(input_dir)
    self_exclusion_registry = _build_self_exclusion_registry(input_dir)
    print(f"  {len(events):,} events, sorted ({time.time() - t0:.1f}s)")

    store = InMemoryStateStore()
    rng = np.random.default_rng(7)
    flag_rows = []

    for i, event in enumerate(events):
        detected_ts = event["ts"] + timedelta(seconds=float(rng.uniform(0.2, processing_latency_seconds)))

        if event["event_type"] == "login":
            for flag in rules.check_self_exclusion_breach(event, self_exclusion_registry):
                flag_rows.append(_flag_to_row(flag, detected_ts))

        for rule_fn in RULES_BY_EVENT_TYPE.get(event["event_type"], []):
            for flag in rule_fn(event, store):
                flag_rows.append(_flag_to_row(flag, detected_ts))

        if (i + 1) % 500_000 == 0:
            print(f"  processed {i + 1:,}/{len(events):,} events ({time.time() - t0:.1f}s)")

    flags_df = pd.DataFrame(flag_rows)
    print(f"Backtest complete: {len(flags_df):,} flags raised in {time.time() - t0:.1f}s")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "streaming_fraud_flags.parquet"
    flags_df.to_parquet(out_path, index=False)
    print(f"Wrote {out_path}")
    return flags_df


def _flag_to_row(flag: rules.Flag, detected_ts) -> dict:
    return {
        "flag_id": str(uuid.uuid4()),
        "scenario_type": flag.scenario_type,
        "entity_type": flag.entity_type,
        "entity_id": flag.entity_id,
        "player_id": flag.player_id,
        "triggering_event_ts": flag.triggering_event_ts,
        "detected_ts": detected_ts,
        "severity": flag.severity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    run(args.input_dir, args.output_dir)


if __name__ == "__main__":
    main()
