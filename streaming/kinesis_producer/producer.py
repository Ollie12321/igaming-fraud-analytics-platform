"""Publishes normalised player events to the real `igaming-player-events`
Kinesis stream. Used two ways:

  1. `--mode replay`: takes the most recent slice of the generated historical
     dataset and republishes it to Kinesis, shifting timestamps so it looks
     like "now": this is what actually exercises the deployed Lambda +
     DynamoDB path end-to-end.
  2. `--mode live`: generates a small amount of synthetic traffic continuously
     in real time, for a standing demo.

Both modes emit the same event shape the Lambda handler expects (see
streaming/fraud_rules/rules.py docstring).
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import pandas as pd

from config import get_settings


def _event_from_login(row, shift: timedelta) -> dict:
    return {
        "event_type": "login",
        "entity_id": row.login_id,
        "player_id": row.player_id,
        "device_id": row.device_id,
        "country_from_ip": row.country_from_ip,
        "ts": (row.ts + shift).isoformat(),
    }


def _event_from_payment(row, shift: timedelta) -> dict:
    return {
        "event_type": "payment",
        "entity_id": row.payment_id,
        "player_id": row.player_id,
        "device_id": row.device_id,
        "payment_type": row.payment_type,
        "amount": row.amount,
        "currency": row.currency,
        "card_bin": row.card_bin,
        "ts": (row.ts + shift).isoformat(),
    }


def _event_from_bonus(row, shift: timedelta) -> dict:
    return {
        "event_type": "bonus_claim",
        "entity_id": row.bonus_id,
        "player_id": row.player_id,
        "device_id": row.device_id,
        "bonus_type": row.bonus_type,
        "ts": (row.claim_ts + shift).isoformat(),
    }


def _event_from_round(row, shift: timedelta) -> dict:
    return {
        "event_type": "game_round",
        "entity_id": row.round_id,
        "player_id": row.player_id,
        "session_id": row.session_id,
        "ts": (row.ts + shift).isoformat(),
    }


def replay(input_dir: Path, hours: int, requests_per_second: float) -> None:
    settings = get_settings()
    client = boto3.client("kinesis", region_name=settings.aws_region)

    cutoff = pd.read_parquet(input_dir / "game_rounds.parquet")["ts"].max() - timedelta(hours=hours)
    shift = (
        datetime.now(timezone.utc) - cutoff.tz_localize("UTC")
        if cutoff.tzinfo is None
        else datetime.now(timezone.utc) - cutoff
    )

    events = []
    for name, builder, ts_col in [
        ("login_events.parquet", _event_from_login, "ts"),
        ("payments.parquet", _event_from_payment, "ts"),
        ("bonuses.parquet", _event_from_bonus, "claim_ts"),
        ("game_rounds.parquet", _event_from_round, "ts"),
    ]:
        df = pd.read_parquet(input_dir / name)
        df = df[df[ts_col] >= cutoff]
        events.extend(builder(row, shift) for row in df.itertuples(index=False))

    events.sort(key=lambda e: e["ts"])
    print(f"Replaying {len(events):,} events from the last {hours}h onto {settings.kinesis_stream_name}")

    delay = 1.0 / requests_per_second if requests_per_second > 0 else 0
    for event in events:
        client.put_record(
            StreamName=settings.kinesis_stream_name,
            Data=json.dumps(event).encode("utf-8"),
            PartitionKey=event["player_id"],
        )
        if delay:
            time.sleep(delay)

    print("Replay complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--hours", type=int, default=6, help="How much of the recent history to replay")
    parser.add_argument("--rate", type=float, default=20.0, help="Events per second to publish")
    args = parser.parse_args()
    replay(args.input_dir, args.hours, args.rate)


if __name__ == "__main__":
    main()
