"""Entry point for generating the full synthetic iGaming dataset.

Usage:
    python -m datagen.simulate --output-dir data/raw

Produces one parquet file per table under `output-dir`, including
`fraud_ground_truth.parquet` which is used only for scoring the streaming
detector after the fact, never fed into it.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from config import get_settings
from datagen import fraud_scenarios as fraud
from datagen import population as pop


def _concat(*frames: pd.DataFrame) -> pd.DataFrame:
    frames = [f for f in frames if f is not None and len(f) > 0]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _inject_duplicates(rng: np.random.Generator, df: pd.DataFrame, rate: float) -> pd.DataFrame:
    if len(df) == 0:
        return df
    n_dupes = int(len(df) * rate)
    dupe_rows = df.sample(n=n_dupes, random_state=int(rng.integers(0, 1_000_000)))
    return _concat(df, dupe_rows)


def run(output_dir: Path) -> None:
    settings = get_settings()
    rng = np.random.default_rng(settings.random_seed)

    end_date = pd.Timestamp.now().normalize()
    start_date = end_date - pd.Timedelta(days=settings.simulation_days)

    t0 = time.time()
    print(f"Generating {settings.num_players:,} players over {settings.simulation_days} days...")

    players = pop.generate_players(rng, settings.num_players, start_date, end_date)
    devices, player_devices = pop.generate_devices(rng, players)
    attribute_history, self_exclusion_dates = pop.generate_attribute_history(rng, players, end_date)

    print(f"  players/devices/attributes done ({time.time() - t0:.1f}s)")

    sessions, logins, game_rounds = pop.generate_sessions_logins_rounds(
        rng, players, player_devices, self_exclusion_dates, end_date
    )
    print(f"  sessions/logins/game_rounds done ({time.time() - t0:.1f}s) -> {len(game_rounds):,} rounds")

    payments = pop.generate_payments(rng, players, sessions, player_devices)
    bonuses = pop.generate_bonuses(rng, players, player_devices)
    print(f"  payments/bonuses done ({time.time() - t0:.1f}s)")

    print("Injecting labelled fraud/abuse scenarios...")
    ground_truth_rows: list[dict] = []

    ring_players, ring_devices, ring_bonuses, gt = fraud.inject_bonus_abuse_rings(rng, end_date)
    players, devices, bonuses = (
        _concat(players, ring_players),
        _concat(devices, ring_devices),
        _concat(bonuses, ring_bonuses),
    )
    ground_truth_rows += gt

    # card testing/ATO/bot/structuring target the *original* organic population only
    # (player_devices is keyed by organically-generated player_id, not the ring accounts above).
    organic_player_ids = set(player_devices.keys())
    organic_players = players[players["player_id"].isin(organic_player_ids)]

    ct_payments, gt = fraud.inject_card_testing(rng, organic_players, player_devices, end_date)
    payments = _concat(payments, ct_payments)
    ground_truth_rows += gt

    ato_logins, ato_payments, gt = fraud.inject_account_takeovers(rng, organic_players, player_devices, end_date)
    logins, payments = _concat(logins, ato_logins), _concat(payments, ato_payments)
    ground_truth_rows += gt

    breach_logins, gt = fraud.inject_self_exclusion_breaches(rng, self_exclusion_dates, player_devices, end_date)
    logins = _concat(logins, breach_logins)
    ground_truth_rows += gt

    bot_sessions, bot_rounds, gt = fraud.inject_bot_betting(rng, organic_players, player_devices, end_date)
    sessions, game_rounds = _concat(sessions, bot_sessions), _concat(game_rounds, bot_rounds)
    ground_truth_rows += gt

    structuring_payments, gt = fraud.inject_structuring(rng, organic_players, player_devices, end_date)
    payments = _concat(payments, structuring_payments)
    ground_truth_rows += gt

    fraud_ground_truth = pd.DataFrame(ground_truth_rows)
    print(f"  fraud injection done ({time.time() - t0:.1f}s) -> {len(fraud_ground_truth):,} ground-truth labels")

    # Simulate realistic at-least-once ingestion duplicates (Kinesis/Firehose retries,
    # DAG re-runs) so the staging-layer deduplication logic has something real to do,
    # and the naive-vs-engineered feature comparison later has real teeth.
    payments = _inject_duplicates(rng, payments, rate=0.02)
    game_rounds = _inject_duplicates(rng, game_rounds, rate=0.015)
    print(f"  injected ingestion duplicates ({time.time() - t0:.1f}s)")

    output_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "players": players,
        "devices": devices,
        "player_attribute_history": attribute_history,
        "sessions": sessions,
        "login_events": logins,
        "game_rounds": game_rounds,
        "payments": payments,
        "bonuses": bonuses,
        "fraud_ground_truth": fraud_ground_truth,
    }
    for name, df in tables.items():
        path = output_dir / f"{name}.parquet"
        df.to_parquet(path, index=False)
        print(f"  wrote {path} ({len(df):,} rows)")

    print(f"\nDone in {time.time() - t0:.1f}s. Output: {output_dir.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    run(args.output_dir)


if __name__ == "__main__":
    main()
