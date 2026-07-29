"""AWS Lambda entry point: consumes a batch of Kinesis records (via the
native Kinesis event source mapping, no polling code required), runs each
event through the same rule functions used in the local backtest, and
persists any resulting flags to DynamoDB.

Deploy: terraform/lambda.tf wires this to the `igaming-player-events`
Kinesis stream and the `igaming-fraud-flags` DynamoDB table.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone

import boto3

from streaming.fraud_rules import rules
from streaming.fraud_rules.state_store import DynamoDBStateStore

REGION = os.environ.get("AWS_REGION", "eu-west-2")
STATE_TABLE = os.environ.get("DYNAMODB_FRAUD_STATE_TABLE", "igaming-fraud-state")
FLAGS_TABLE = os.environ.get("DYNAMODB_FRAUD_FLAGS_TABLE", "igaming-fraud-flags")
EXCLUSION_TABLE = os.environ.get("DYNAMODB_SELF_EXCLUSION_TABLE", "igaming-self-exclusion-registry")

RULES_BY_EVENT_TYPE = {
    "login": [rules.check_account_takeover],
    "payment": [rules.check_card_testing, rules.check_account_takeover, rules.check_structuring],
    "bonus_claim": [rules.check_bonus_abuse_ring],
    "game_round": [rules.check_bot_betting],
}

_state_store = None
_flags_table = None
_exclusion_table = None


def _clients():
    global _state_store, _flags_table, _exclusion_table
    if _state_store is None:
        _state_store = DynamoDBStateStore(STATE_TABLE, REGION)
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        _flags_table = dynamodb.Table(FLAGS_TABLE)
        _exclusion_table = dynamodb.Table(EXCLUSION_TABLE)
    return _state_store, _flags_table, _exclusion_table


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _self_excluded_since(player_id: str, exclusion_table) -> datetime | None:
    response = exclusion_table.get_item(Key={"player_id": player_id})
    item = response.get("Item")
    return _parse_ts(item["excluded_since"]) if item else None


def _write_flag(flags_table, flag: rules.Flag, detected_ts: datetime) -> None:
    import uuid

    flags_table.put_item(
        Item={
            "flag_id": str(uuid.uuid4()),
            "scenario_type": flag.scenario_type,
            "entity_type": flag.entity_type,
            "entity_id": flag.entity_id,
            "player_id": flag.player_id,
            "triggering_event_ts": flag.triggering_event_ts.isoformat(),
            "detected_ts": detected_ts.isoformat(),
            "severity": flag.severity,
        }
    )


def handler(event: dict, context) -> dict:
    """Kinesis-triggered handler. `event["Records"]` is a batch delivered by
    the Kinesis-Lambda event source mapping (base64-encoded payloads).
    """
    store, flags_table, exclusion_table = _clients()
    flags_raised = 0

    for record in event.get("Records", []):
        payload = base64.b64decode(record["kinesis"]["data"])
        player_event = json.loads(payload)
        player_event["ts"] = _parse_ts(player_event["ts"])
        detected_ts = datetime.now(timezone.utc)

        if player_event["event_type"] == "login":
            excluded_since = _self_excluded_since(player_event["player_id"], exclusion_table)
            registry = {player_event["player_id"]: excluded_since} if excluded_since else {}
            for flag in rules.check_self_exclusion_breach(player_event, registry):
                _write_flag(flags_table, flag, detected_ts)
                flags_raised += 1

        for rule_fn in RULES_BY_EVENT_TYPE.get(player_event["event_type"], []):
            for flag in rule_fn(player_event, store):
                _write_flag(flags_table, flag, detected_ts)
                flags_raised += 1

    return {"records_processed": len(event.get("Records", [])), "flags_raised": flags_raised}
