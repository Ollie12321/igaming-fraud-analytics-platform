"""Generates the 'normal' (non-fraudulent) player population and their behaviour.

Everything here is vectorised with numpy where the table is large (game_rounds,
payments) to keep a 10k-player / 150-day simulation running in well under a
minute on a laptop.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import numpy as np
import pandas as pd

from datagen import reference_data as ref


def _uuids(n: int) -> np.ndarray:
    return np.array([str(uuid.uuid4()) for _ in range(n)])


def _weighted_keys(rng: np.random.Generator, mapping: dict, size: int, weight_index: int = 0) -> np.ndarray:
    keys = list(mapping.keys())
    weights = [v[weight_index] if isinstance(v, tuple) else v for v in mapping.values()]
    weights = np.array(weights, dtype=float) / np.sum(weights)
    return rng.choice(keys, size=size, p=weights)


def generate_players(
    rng: np.random.Generator, n: int, start_date: pd.Timestamp, end_date: pd.Timestamp
) -> pd.DataFrame:
    signup_offsets = rng.integers(0, (end_date - start_date).days, size=n)
    signup_ts = (
        start_date
        + pd.to_timedelta(signup_offsets, unit="D")
        + pd.to_timedelta(rng.integers(0, 86400, size=n), unit="s")
    )
    countries = _weighted_keys(rng, ref.COUNTRIES, n, weight_index=1)
    currencies = np.array([ref.COUNTRIES[c][0] for c in countries])
    channels = _weighted_keys(rng, ref.ACQUISITION_CHANNELS, n)
    segments = _weighted_keys(rng, ref.ACTIVITY_SEGMENTS, n)
    ages_days = rng.integers(18 * 365, 75 * 365, size=n)
    dob = pd.Timestamp("2026-07-29") - pd.to_timedelta(ages_days, unit="D")

    return pd.DataFrame(
        {
            "player_id": _uuids(n),
            "signup_ts": signup_ts,
            "country": countries,
            "currency": currencies,
            "acquisition_channel": channels,
            "activity_segment": segments,
            "date_of_birth": dob,
        }
    )


def generate_devices(rng: np.random.Generator, players: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One primary device per player at signup; ~15% acquire a second device later."""
    n = len(players)
    primary_device_ids = _uuids(n)
    os_choices = rng.choice(["iOS", "Android", "Windows", "macOS"], size=n, p=[0.35, 0.35, 0.22, 0.08])

    rows = [
        {
            "device_id": primary_device_ids[i],
            "player_id": players["player_id"].iloc[i],
            "first_seen_ts": players["signup_ts"].iloc[i],
            "os": os_choices[i],
            "is_shared_fraud_ring": False,
        }
        for i in range(n)
    ]

    player_devices: dict[str, list[str]] = {pid: [did] for pid, did in zip(players["player_id"], primary_device_ids)}

    second_device_mask = rng.random(n) < 0.15
    for i in np.where(second_device_mask)[0]:
        pid = players["player_id"].iloc[i]
        did = str(uuid.uuid4())
        offset_days = rng.integers(5, 120)
        rows.append(
            {
                "device_id": did,
                "player_id": pid,
                "first_seen_ts": players["signup_ts"].iloc[i] + timedelta(days=int(offset_days)),
                "os": rng.choice(["iOS", "Android", "Windows", "macOS"]),
                "is_shared_fraud_ring": False,
            }
        )
        player_devices[pid].append(did)

    return pd.DataFrame(rows), player_devices


def generate_attribute_history(
    rng: np.random.Generator, players: pd.DataFrame, end_date: pd.Timestamp
) -> tuple[pd.DataFrame, dict]:
    """Produces the change-log that dbt/snapshots turns into an SCD Type 2 dimension."""
    rows = []
    self_exclusion_dates: dict[str, pd.Timestamp] = {}

    for _, p in players.iterrows():
        pid, signup_ts, segment = p["player_id"], p["signup_ts"], p["activity_segment"]

        rows.append(
            {
                "player_id": pid,
                "effective_ts": signup_ts,
                "vip_tier": "bronze",
                "kyc_status": "pending",
                "self_exclusion_status": "none",
                "risk_segment": "low",
            }
        )

        # KYC resolves within a few days for the vast majority
        kyc_roll = rng.random()
        kyc_ts = signup_ts + timedelta(hours=int(rng.integers(2, 96)))
        if kyc_ts < end_date:
            kyc_status = "verified" if kyc_roll < 0.90 else ("rejected" if kyc_roll < 0.95 else "pending")
            if kyc_status != "pending":
                rows.append(
                    {
                        "player_id": pid,
                        "effective_ts": kyc_ts,
                        "vip_tier": "bronze",
                        "kyc_status": kyc_status,
                        "self_exclusion_status": "none",
                        "risk_segment": "low",
                    }
                )

        # VIP tier progression for regular/vip segments
        if segment in ("regular", "vip") and rng.random() < (0.55 if segment == "vip" else 0.20):
            n_upgrades = rng.integers(1, 4 if segment == "vip" else 2)
            tier_idx = 0
            ts = kyc_ts
            for _ in range(int(n_upgrades)):
                ts = ts + timedelta(days=int(rng.integers(10, 60)))
                if ts >= end_date:
                    break
                tier_idx = min(tier_idx + 1, len(ref.VIP_TIERS) - 1)
                rows.append(
                    {
                        "player_id": pid,
                        "effective_ts": ts,
                        "vip_tier": ref.VIP_TIERS[tier_idx],
                        "kyc_status": "verified",
                        "self_exclusion_status": "none",
                        "risk_segment": "low" if tier_idx < 2 else "medium",
                    }
                )

        # Responsible-gambling self-exclusion: ~1.5% of players, at a random later point
        if rng.random() < 0.015:
            days_active = max(1, (end_date - signup_ts).days - 5)
            ex_ts = signup_ts + timedelta(days=int(rng.integers(5, days_active + 5)))
            if ex_ts < end_date:
                status = "self_excluded" if rng.random() < 0.7 else "cooling_off"
                rows.append(
                    {
                        "player_id": pid,
                        "effective_ts": ex_ts,
                        "vip_tier": rows[-1]["vip_tier"],
                        "kyc_status": rows[-1]["kyc_status"],
                        "self_exclusion_status": status,
                        "risk_segment": "high",
                    }
                )
                if status == "self_excluded":
                    self_exclusion_dates[pid] = ex_ts

    return pd.DataFrame(rows).sort_values(["player_id", "effective_ts"]).reset_index(drop=True), self_exclusion_dates


def generate_sessions_logins_rounds(
    rng: np.random.Generator,
    players: pd.DataFrame,
    player_devices: dict,
    self_exclusion_dates: dict,
    end_date: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    session_rows, login_rows = [], []
    round_id_chunks, session_id_chunks, player_id_chunks = [], [], []
    game_type_chunks, stake_chunks, payout_chunks, ts_chunks = [], [], [], []

    for _, p in players.iterrows():
        pid = p["player_id"]
        segment = p["activity_segment"]
        _, sessions_per_week, stake_mult = ref.ACTIVITY_SEGMENTS[segment]

        active_end = self_exclusion_dates.get(pid, end_date)
        active_days = max((active_end - p["signup_ts"]).days, 0)
        if active_days == 0:
            continue

        expected_sessions = sessions_per_week * (active_days / 7)
        n_sessions = rng.poisson(max(expected_sessions, 0.01))
        if n_sessions == 0:
            continue

        offsets = np.sort(rng.integers(0, active_days * 86400, size=n_sessions))
        session_starts = p["signup_ts"] + pd.to_timedelta(offsets, unit="s")
        devices_for_player = player_devices[pid]

        for start_ts in session_starts:
            session_id = str(uuid.uuid4())
            device_id = rng.choice(devices_for_player)
            duration_min = max(1, int(rng.gamma(2.0, 15.0)))
            end_ts = start_ts + timedelta(minutes=duration_min)

            session_rows.append(
                {
                    "session_id": session_id,
                    "player_id": pid,
                    "device_id": device_id,
                    "ip_address": f"{rng.integers(1,255)}.{rng.integers(0,255)}.{rng.integers(0,255)}.{rng.integers(1,255)}",
                    "country_from_ip": p["country"],
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                }
            )
            login_rows.append(
                {
                    "login_id": str(uuid.uuid4()),
                    "player_id": pid,
                    "device_id": device_id,
                    "ip_address": session_rows[-1]["ip_address"],
                    "country_from_ip": p["country"],
                    "ts": start_ts,
                    "success_flag": True,
                }
            )

            n_rounds = max(1, int(rng.gamma(3.0, 3.0) * (1 + stake_mult / 10)))
            game_types = _weighted_keys(rng, ref.GAME_TYPES, n_rounds)
            round_offsets = np.sort(rng.integers(0, max(duration_min * 60, 1), size=n_rounds))
            round_ts = start_ts + pd.to_timedelta(round_offsets, unit="s")

            for gt, r_ts in zip(game_types, round_ts):
                _, mean_stake, stake_std, rtp = ref.GAME_TYPES[gt]
                stake = max(0.5, rng.normal(mean_stake * stake_mult, stake_std * stake_mult))
                payout = stake * rtp * rng.exponential(1.0) if rng.random() < 0.35 else 0.0

                round_id_chunks.append(str(uuid.uuid4()))
                session_id_chunks.append(session_id)
                player_id_chunks.append(pid)
                game_type_chunks.append(gt)
                stake_chunks.append(round(float(stake), 2))
                payout_chunks.append(round(float(payout), 2))
                ts_chunks.append(r_ts)

    game_rounds = pd.DataFrame(
        {
            "round_id": round_id_chunks,
            "session_id": session_id_chunks,
            "player_id": player_id_chunks,
            "game_type": game_type_chunks,
            "stake_amount": stake_chunks,
            "payout_amount": payout_chunks,
            "ts": ts_chunks,
        }
    )
    return pd.DataFrame(session_rows), pd.DataFrame(login_rows), game_rounds


def generate_payments(
    rng: np.random.Generator, players: pd.DataFrame, sessions: pd.DataFrame, player_devices: dict
) -> pd.DataFrame:
    rows = []
    sessions_by_player = sessions.groupby("player_id")

    for _, p in players.iterrows():
        pid = p["player_id"]
        if pid not in sessions_by_player.groups:
            continue
        player_sessions = sessions_by_player.get_group(pid).sort_values("start_ts")
        method = _weighted_keys(rng, ref.PAYMENT_METHODS, 1)[0]
        card_bin = str(rng.integers(400000, 599999))

        for _, s in player_sessions.iterrows():
            if rng.random() < 0.55:
                amount = round(float(max(5, rng.lognormal(mean=3.2, sigma=0.9))), 2)
                rows.append(
                    {
                        "payment_id": str(uuid.uuid4()),
                        "player_id": pid,
                        "payment_type": "deposit",
                        "amount": amount,
                        "currency": p["currency"],
                        "method": method,
                        "card_bin": card_bin,
                        "device_id": s["device_id"],
                        "ts": s["start_ts"] - timedelta(minutes=int(rng.integers(1, 20))),
                        "status": "completed",
                    }
                )
            if rng.random() < 0.10:
                amount = round(float(max(10, rng.lognormal(mean=4.0, sigma=1.0))), 2)
                rows.append(
                    {
                        "payment_id": str(uuid.uuid4()),
                        "player_id": pid,
                        "payment_type": "withdrawal",
                        "amount": amount,
                        "currency": p["currency"],
                        "method": method,
                        "card_bin": card_bin,
                        "device_id": s["device_id"],
                        "ts": s["end_ts"] + timedelta(minutes=int(rng.integers(1, 30))),
                        "status": "completed",
                    }
                )

    return pd.DataFrame(rows)


def generate_bonuses(rng: np.random.Generator, players: pd.DataFrame, player_devices: dict) -> pd.DataFrame:
    rows = []
    for _, p in players.iterrows():
        pid = p["player_id"]
        rows.append(
            {
                "bonus_id": str(uuid.uuid4()),
                "player_id": pid,
                "bonus_type": "welcome",
                "claim_ts": p["signup_ts"] + timedelta(minutes=int(rng.integers(1, 60))),
                "wagering_requirement_multiple": 35,
                "bonus_amount": round(float(rng.choice([10, 20, 25, 50])), 2),
                "device_id": player_devices[pid][0],
            }
        )
        if rng.random() < 0.30:
            rows.append(
                {
                    "bonus_id": str(uuid.uuid4()),
                    "player_id": pid,
                    "bonus_type": "reload",
                    "claim_ts": p["signup_ts"] + timedelta(days=int(rng.integers(10, 100))),
                    "wagering_requirement_multiple": 25,
                    "bonus_amount": round(float(rng.choice([5, 10, 15])), 2),
                    "device_id": rng.choice(player_devices[pid]),
                }
            )
    return pd.DataFrame(rows)
