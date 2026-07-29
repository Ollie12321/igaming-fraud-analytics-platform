"""Windowed fraud/abuse detection rules.

Each `check_*` function takes one normalised event, a `StateStore`, and
(where needed) a small piece of reference data, and returns zero or more
`Flag`s. Functions are pure aside from the state store, so the exact same
code runs in the deployed Lambda (DynamoDB-backed) and in the local
backtest (in-memory-backed) used to score precision/recall/latency.

Event shape (all events share these keys, plus type-specific ones):
    {"event_type": "login" | "payment" | "bonus_claim" | "game_round",
     "entity_id": str, "player_id": str, "device_id": str, "ts": datetime, ...}
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from streaming.fraud_rules.state_store import StateStore

CARD_TESTING_WINDOW_SECONDS = 600
CARD_TESTING_MIN_DISTINCT_BINS = 3

BONUS_RING_WINDOW_SECONDS = 6 * 3600

ATO_NEW_DEVICE_WINDOW_SECONDS = 15 * 60
ATO_KNOWN_DEVICE_TTL_SECONDS = 400 * 24 * 3600

BOT_MIN_ROUNDS = 100
BOT_MAX_STDDEV_SECONDS = 0.3
BOT_STATE_TTL_SECONDS = 6 * 3600

STRUCTURING_THRESHOLD_GBP = 10_000
STRUCTURING_NEAR_THRESHOLD_RATIO = 0.9
STRUCTURING_WINDOW_SECONDS = 24 * 3600
STRUCTURING_MIN_DEPOSITS = 2


@dataclass
class Flag:
    scenario_type: str
    entity_type: str
    entity_id: str
    player_id: str
    triggering_event_ts: datetime
    severity: str = "high"


def check_card_testing(event: dict, store: StateStore) -> list[Flag]:
    if event["event_type"] != "payment" or event.get("payment_type") != "deposit":
        return []

    key = f"card_testing:{event['player_id']}"
    ts = event["ts"]
    history = store.get(key, ts) or []
    history = [h for h in history if (ts - h["ts"]).total_seconds() <= CARD_TESTING_WINDOW_SECONDS]
    history.append({"card_bin": event["card_bin"], "ts": ts})
    store.put(key, history, ts, ttl_seconds=CARD_TESTING_WINDOW_SECONDS)

    if len({h["card_bin"] for h in history}) >= CARD_TESTING_MIN_DISTINCT_BINS:
        return [Flag("card_testing", "payment", event["entity_id"], event["player_id"], ts)]
    return []


def check_bonus_abuse_ring(event: dict, store: StateStore) -> list[Flag]:
    if event["event_type"] != "bonus_claim":
        return []

    key = f"bonus_ring:{event['device_id']}:{event['bonus_type']}"
    ts = event["ts"]
    claimants: list[str] = store.get(key, ts) or []
    is_ring = len(claimants) >= 1 and event["player_id"] not in claimants
    claimants.append(event["player_id"])
    store.put(key, claimants, ts, ttl_seconds=BONUS_RING_WINDOW_SECONDS)

    if is_ring:
        return [Flag("bonus_abuse_ring", "bonus_claim", event["entity_id"], event["player_id"], ts)]
    return []


def check_account_takeover(event: dict, store: StateStore) -> list[Flag]:
    ts = event["ts"]
    player_id = event["player_id"]
    known_key = f"known_devices:{player_id}"

    if event["event_type"] == "login":
        known_key_geo = f"known_geos:{player_id}"
        known_devices: list[str] = store.get(known_key, ts) or []
        known_geos: list[str] = store.get(known_key_geo, ts) or []
        is_new_device = event["device_id"] not in known_devices
        is_new_geo = event.get("country_from_ip") not in known_geos
        has_history = bool(known_devices)

        if is_new_device:
            store.put(known_key, known_devices + [event["device_id"]], ts, ttl_seconds=ATO_KNOWN_DEVICE_TTL_SECONDS)
        if event.get("country_from_ip") is not None and is_new_geo:
            store.put(
                known_key_geo, known_geos + [event["country_from_ip"]], ts, ttl_seconds=ATO_KNOWN_DEVICE_TTL_SECONDS
            )

        # A new device alone is common (organic second devices): a new device
        # *combined with* a new geo in the same login, for a player with prior
        # history, is what's actually rare and is the real ATO signal.
        if is_new_device and is_new_geo and has_history:
            store.put(
                f"new_device_pending:{player_id}",
                {"device_id": event["device_id"], "login_id": event["entity_id"], "ts": ts},
                ts,
                ttl_seconds=ATO_NEW_DEVICE_WINDOW_SECONDS,
            )
        return []

    if event["event_type"] == "payment" and event.get("payment_type") == "withdrawal":
        pending_key = f"new_device_pending:{player_id}"
        pending = store.get(pending_key, ts)
        if pending and pending["device_id"] == event["device_id"]:
            store.delete(pending_key)  # one flag per suspicious login, not one per withdrawal
            return [
                Flag("account_takeover", "payment", event["entity_id"], player_id, ts),
                Flag("account_takeover", "login", pending["login_id"], player_id, pending["ts"]),
            ]
    return []


def check_self_exclusion_breach(event: dict, self_exclusion_registry: dict[str, datetime]) -> list[Flag]:
    """`self_exclusion_registry` is reference data (player_id -> exclusion
    effective timestamp), synced from the compliance/CRM system into a fast
    lookup table (DynamoDB in AWS), not built incrementally from the stream.
    """
    if event["event_type"] != "login":
        return []

    excluded_since = self_exclusion_registry.get(event["player_id"])
    if excluded_since is not None and event["ts"] > excluded_since:
        return [Flag("self_exclusion_breach", "login", event["entity_id"], event["player_id"], event["ts"])]
    return []


def check_bot_betting(event: dict, store: StateStore) -> list[Flag]:
    if event["event_type"] != "game_round":
        return []

    session_id = event["session_id"]
    ts = event["ts"]
    key = f"bot:{session_id}"
    state = store.get(key, ts) or {"n": 0, "mean": 0.0, "m2": 0.0, "last_ts": None, "flagged": False}

    if state["last_ts"] is not None:
        gap = (ts - state["last_ts"]).total_seconds()
        n = state["n"] + 1
        delta = gap - state["mean"]
        mean = state["mean"] + delta / n
        m2 = state["m2"] + delta * (gap - mean)
        state.update(n=n, mean=mean, m2=m2)

    state["last_ts"] = ts
    store.put(key, state, ts, ttl_seconds=BOT_STATE_TTL_SECONDS)

    if state["n"] >= BOT_MIN_ROUNDS and not state["flagged"]:
        variance = state["m2"] / (state["n"] - 1) if state["n"] > 1 else 0.0
        if math.sqrt(max(variance, 0.0)) < BOT_MAX_STDDEV_SECONDS:
            state["flagged"] = True
            store.put(key, state, ts, ttl_seconds=BOT_STATE_TTL_SECONDS)
            return [Flag("bot_betting", "session", session_id, event["player_id"], ts)]
    return []


def check_structuring(event: dict, store: StateStore) -> list[Flag]:
    if event["event_type"] != "payment" or event.get("payment_type") != "deposit":
        return []
    if event["amount_gbp"] < STRUCTURING_THRESHOLD_GBP * STRUCTURING_NEAR_THRESHOLD_RATIO:
        return []

    ts = event["ts"]
    player_id = event["player_id"]
    key = f"structuring:{player_id}"
    history = store.get(key, ts) or []
    history = [h for h in history if (ts - h["ts"]).total_seconds() <= STRUCTURING_WINDOW_SECONDS]
    history.append({"payment_id": event["entity_id"], "ts": ts})
    store.put(key, history, ts, ttl_seconds=STRUCTURING_WINDOW_SECONDS)

    if len(history) >= STRUCTURING_MIN_DEPOSITS:
        return [Flag("structuring", "payment", event["entity_id"], player_id, ts)]
    return []


ALL_RULES = [
    check_card_testing,
    check_bonus_abuse_ring,
    check_account_takeover,
    check_bot_betting,
    check_structuring,
]
