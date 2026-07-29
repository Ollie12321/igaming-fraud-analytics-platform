"""Injects labelled fraud/abuse scenarios into the otherwise-clean synthetic
population so the streaming detector has real ground truth to be scored
against (precision/recall/latency), rather than made-up claims.

Every function returns the rows to append to the relevant table(s) plus a
list of `fraud_ground_truth` rows. Ground truth is written to a table that is
never read by the detector, only used afterwards for scoring.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pandas as pd

from datagen import reference_data as ref
from datagen.reference_data import FX_RATES_TO_GBP


def _eligible_targets(rng, players: pd.DataFrame, end_date: pd.Timestamp, min_runway_days: int, n: int) -> pd.DataFrame:
    """Players with enough time between signup and end_date for an incident
    (plus its own internal offsets) to land inside the simulation window.
    Without this filter, a fixed day-offset from signup can push an injected
    event's timestamp past `end_date` for players who signed up late.
    """
    eligible = players[(end_date - players["signup_ts"]).dt.days >= min_runway_days]
    return eligible.sample(n=min(n, len(eligible)), random_state=int(rng.integers(0, 1_000_000)))


def _gt(entity_type: str, entity_id: str, scenario_type: str, injected_ts, ring_id: str | None = None) -> dict:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "scenario_type": scenario_type,
        "injected_ts": injected_ts,
        "ring_id": ring_id,
    }


def inject_bonus_abuse_rings(rng, end_date: pd.Timestamp, n_rings: int = 25):
    new_players, new_devices, new_bonuses, ground_truth = [], [], [], []

    for _ in range(n_rings):
        ring_id = str(uuid.uuid4())
        ring_size = int(rng.integers(3, 6))
        shared_device_id = str(uuid.uuid4())
        ring_start = end_date - timedelta(days=int(rng.integers(1, 60)))
        bonus_type = rng.choice(["welcome", "reload"])

        new_devices.append(
            {
                "device_id": shared_device_id,
                "player_id": None,
                "first_seen_ts": ring_start,
                "os": rng.choice(["iOS", "Android", "Windows", "macOS"]),
                "is_shared_fraud_ring": True,
            }
        )

        for i in range(ring_size):
            pid = str(uuid.uuid4())
            signup_ts = ring_start + timedelta(minutes=int(rng.integers(0, 90)))
            country = rng.choice(list(ref.COUNTRIES.keys()))
            new_players.append(
                {
                    "player_id": pid,
                    "signup_ts": signup_ts,
                    "country": country,
                    "currency": ref.COUNTRIES[country][0],
                    "acquisition_channel": "affiliate",
                    "activity_segment": "casual",
                    "date_of_birth": pd.Timestamp("2026-07-29") - timedelta(days=int(rng.integers(18, 40) * 365)),
                }
            )
            claim_ts = signup_ts + timedelta(minutes=int(rng.integers(1, 30)))
            bonus_id = str(uuid.uuid4())
            new_bonuses.append(
                {
                    "bonus_id": bonus_id,
                    "player_id": pid,
                    "bonus_type": bonus_type,
                    "claim_ts": claim_ts,
                    "wagering_requirement_multiple": 35,
                    "bonus_amount": 25.0,
                    "device_id": shared_device_id,
                }
            )
            ground_truth.append(_gt("bonus_claim", bonus_id, "bonus_abuse_ring", claim_ts, ring_id))
            ground_truth.append(_gt("player", pid, "bonus_abuse_ring", claim_ts, ring_id))

    return (
        pd.DataFrame(new_players),
        pd.DataFrame(new_devices),
        pd.DataFrame(new_bonuses),
        ground_truth,
    )


def inject_card_testing(
    rng, players: pd.DataFrame, player_devices: dict, end_date: pd.Timestamp, n_incidents: int = 60
):
    rows, ground_truth = [], []
    targets = _eligible_targets(rng, players, end_date, min_runway_days=31, n=n_incidents)

    for _, p in targets.iterrows():
        pid = p["player_id"]
        device_id = player_devices[pid][0]
        start_ts = p["signup_ts"] + timedelta(days=int(rng.integers(1, 30)), hours=int(rng.integers(0, 23)))
        n_attempts = int(rng.integers(4, 9))

        for i in range(n_attempts):
            ts = start_ts + timedelta(minutes=int(i * rng.integers(1, 4)))
            payment_id = str(uuid.uuid4())
            status = "declined" if i < n_attempts - 1 else "completed"
            rows.append(
                {
                    "payment_id": payment_id,
                    "player_id": pid,
                    "payment_type": "deposit",
                    "amount": round(float(rng.uniform(1, 5)), 2),
                    "currency": p["currency"],
                    "method": "credit_card",
                    "card_bin": str(rng.integers(400000, 599999)),
                    "device_id": device_id,
                    "ts": ts,
                    "status": status,
                }
            )
            ground_truth.append(_gt("payment", payment_id, "card_testing", ts))
        ground_truth.append(_gt("player", pid, "card_testing", start_ts))

    return pd.DataFrame(rows), ground_truth


def inject_account_takeovers(
    rng, players: pd.DataFrame, player_devices: dict, end_date: pd.Timestamp, n_incidents: int = 45
):
    login_rows, payment_rows, ground_truth = [], [], []
    targets = _eligible_targets(rng, players, end_date, min_runway_days=61, n=n_incidents)

    for _, p in targets.iterrows():
        pid = p["player_id"]
        max_offset = min(60, (end_date - p["signup_ts"]).days - 1)
        ato_ts = p["signup_ts"] + timedelta(days=int(rng.integers(10, max(max_offset, 11))))
        new_device_id = str(uuid.uuid4())
        login_id = str(uuid.uuid4())

        login_rows.append(
            {
                "login_id": login_id,
                "player_id": pid,
                "device_id": new_device_id,
                "ip_address": f"{rng.integers(1,255)}.{rng.integers(0,255)}.{rng.integers(0,255)}.{rng.integers(1,255)}",
                "country_from_ip": rng.choice([c for c in ref.COUNTRIES if c != p["country"]]),
                "ts": ato_ts,
                "success_flag": True,
            }
        )
        withdrawal_ts = ato_ts + timedelta(minutes=int(rng.integers(2, 15)))
        payment_id = str(uuid.uuid4())
        payment_rows.append(
            {
                "payment_id": payment_id,
                "player_id": pid,
                "payment_type": "withdrawal",
                "amount": round(float(rng.uniform(500, 5000)), 2),
                "currency": p["currency"],
                "method": "e_wallet",
                "card_bin": str(rng.integers(400000, 599999)),
                "device_id": new_device_id,
                "ts": withdrawal_ts,
                "status": "completed",
            }
        )
        ground_truth.append(_gt("login", login_id, "account_takeover", ato_ts))
        ground_truth.append(_gt("payment", payment_id, "account_takeover", withdrawal_ts))

    return pd.DataFrame(login_rows), pd.DataFrame(payment_rows), ground_truth


def inject_self_exclusion_breaches(rng, self_exclusion_dates: dict, player_devices: dict, end_date: pd.Timestamp):
    login_rows, ground_truth = [], []

    for pid, ex_ts in self_exclusion_dates.items():
        if rng.random() > 0.4:
            continue
        days_after = (end_date - ex_ts).days
        if days_after <= 1:
            continue
        breach_ts = ex_ts + timedelta(days=int(rng.integers(1, days_after)))
        login_id = str(uuid.uuid4())
        login_rows.append(
            {
                "login_id": login_id,
                "player_id": pid,
                "device_id": player_devices[pid][0],
                "ip_address": f"{rng.integers(1,255)}.{rng.integers(0,255)}.{rng.integers(0,255)}.{rng.integers(1,255)}",
                "country_from_ip": None,
                "ts": breach_ts,
                "success_flag": True,
            }
        )
        ground_truth.append(_gt("login", login_id, "self_exclusion_breach", breach_ts))

    return pd.DataFrame(login_rows), ground_truth


def inject_bot_betting(rng, players: pd.DataFrame, player_devices: dict, end_date: pd.Timestamp, n_incidents: int = 35):
    session_rows, round_rows, ground_truth = [], [], []
    targets = _eligible_targets(rng, players, end_date, min_runway_days=3, n=n_incidents)

    for _, p in targets.iterrows():
        pid = p["player_id"]
        session_id = str(uuid.uuid4())
        max_offset = max((end_date - p["signup_ts"]).days - 1, 2)
        start_ts = p["signup_ts"] + timedelta(days=int(rng.integers(1, max_offset)))
        n_rounds = int(rng.integers(150, 400))
        interval_s = rng.uniform(1.5, 2.5)
        end_ts = start_ts + timedelta(seconds=n_rounds * interval_s)

        session_rows.append(
            {
                "session_id": session_id,
                "player_id": pid,
                "device_id": player_devices[pid][0],
                "ip_address": f"{rng.integers(1,255)}.{rng.integers(0,255)}.{rng.integers(0,255)}.{rng.integers(1,255)}",
                "country_from_ip": p["country"],
                "start_ts": start_ts,
                "end_ts": end_ts,
            }
        )

        for i in range(n_rounds):
            r_ts = start_ts + timedelta(seconds=i * interval_s + rng.normal(0, 0.05))
            round_id = str(uuid.uuid4())
            round_rows.append(
                {
                    "round_id": round_id,
                    "session_id": session_id,
                    "player_id": pid,
                    "game_type": "slots",
                    "stake_amount": 1.0,
                    "payout_amount": round(float(rng.exponential(0.9)), 2),
                    "ts": r_ts,
                }
            )
        ground_truth.append(_gt("session", session_id, "bot_betting", start_ts))

    return pd.DataFrame(session_rows), pd.DataFrame(round_rows), ground_truth


def inject_structuring(rng, players: pd.DataFrame, player_devices: dict, end_date: pd.Timestamp, n_incidents: int = 30):
    rows, ground_truth = [], []
    targets = _eligible_targets(rng, players, end_date, min_runway_days=11, n=n_incidents)

    for _, p in targets.iterrows():
        pid = p["player_id"]
        max_offset = min(50, (end_date - p["signup_ts"]).days - 2)
        start_ts = p["signup_ts"] + timedelta(days=int(rng.integers(5, max(max_offset, 6))))
        n_deposits = int(rng.integers(2, 4))

        for i in range(n_deposits):
            # Reporting threshold is GBP-denominated; convert to the player's
            # own currency so `amount_gbp` downstream is actually near-threshold
            # regardless of market.
            amount_gbp_target = ref.STRUCTURING_THRESHOLD_GBP - rng.uniform(50, 500)
            fx_rate = FX_RATES_TO_GBP.get(p["currency"], 1.0)
            amount = amount_gbp_target / fx_rate
            # All deposits land within the same ~24h window (structuring is
            # about beating a daily reporting threshold, not a multi-day one).
            ts = start_ts + timedelta(hours=float(i) * rng.uniform(2, 6))
            payment_id = str(uuid.uuid4())
            rows.append(
                {
                    "payment_id": payment_id,
                    "player_id": pid,
                    "payment_type": "deposit",
                    "amount": round(float(amount), 2),
                    "currency": p["currency"],
                    "method": "bank_transfer",
                    "card_bin": str(rng.integers(400000, 599999)),
                    "device_id": player_devices[pid][0],
                    "ts": ts,
                    "status": "completed",
                }
            )
            ground_truth.append(_gt("payment", payment_id, "structuring", ts))
        ground_truth.append(_gt("player", pid, "structuring", start_ts))

    return pd.DataFrame(rows), ground_truth
