"""State backend used by the windowed fraud rules.

The rules in `rules.py` are backend-agnostic: they call `get`/`put` on
whatever `StateStore` they're given. In AWS this is `DynamoDBStateStore`
(so the real Lambda in `streaming/lambda_fraud_detector/` gets true
low-latency, durable, cross-invocation state). For local development, CI, and
the backtest used to score detector precision/recall against the synthetic
ground truth, `InMemoryStateStore` gives identical semantics for free.

TTL is evaluated against an explicit `as_of` timestamp rather than wall-clock
time, so the exact same rule code can replay months of historical events in
a backtest and get the same answer it would have given running live.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any


class StateStore(ABC):
    @abstractmethod
    def get(self, key: str, as_of: datetime) -> Any | None: ...

    @abstractmethod
    def put(self, key: str, value: Any, as_of: datetime, ttl_seconds: int) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


class InMemoryStateStore(StateStore):
    """Dict-backed store. Used for local runs, backtesting and unit tests."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, datetime]] = {}

    def get(self, key: str, as_of: datetime) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if as_of > expires_at:
            del self._data[key]
            return None
        return value

    def put(self, key: str, value: Any, as_of: datetime, ttl_seconds: int) -> None:
        expires_at = as_of + _seconds(ttl_seconds)
        self._data[key] = (value, expires_at)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class DynamoDBStateStore(StateStore):
    """Backed by a single DynamoDB table (partition key: `pk`, attribute `ttl`
    set as the table's configured TTL attribute so expired items are reaped
    automatically). Used by the deployed Lambda detector.
    """

    def __init__(self, table_name: str, region_name: str) -> None:
        import boto3  # local import so the rest of the module has no hard AWS dependency

        self._table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)

    def get(self, key: str, as_of: datetime) -> Any | None:
        response = self._table.get_item(Key={"pk": key})
        item = response.get("Item")
        if item is None:
            return None
        if item.get("ttl", 0) < as_of.timestamp():
            return None
        return item.get("value")

    def put(self, key: str, value: Any, as_of: datetime, ttl_seconds: int) -> None:
        self._table.put_item(
            Item={
                "pk": key,
                "value": value,
                "ttl": int((as_of + _seconds(ttl_seconds)).timestamp()),
            }
        )

    def delete(self, key: str) -> None:
        self._table.delete_item(Key={"pk": key})


def _seconds(n: int):
    from datetime import timedelta

    return timedelta(seconds=n)


def now_utc() -> datetime:
    return datetime.fromtimestamp(time.time(), tz=timezone.utc)
