"""Reference implementation only. NOT deployed in this project.

Lambda + DynamoDB (see streaming/lambda_fraud_detector/) is the version
that's actually running, because at this project's event volume (a few
hundred events/sec at peak) it's both cheaper and operationally simpler than
a standing Flink cluster: Kinesis Data Analytics for Apache Flink bills per
KPU-hour whether or not events are flowing, which only pays for itself once
throughput is high and consistent enough to justify a dedicated compute
cluster instead of per-invocation serverless billing.

This file exists to show how the same card-testing rule
(streaming/fraud_rules/rules.py::check_card_testing) would be expressed as a
true stateful streaming operator with Flink's native keyed state and event-time
windowing, which is the more standard approach at higher throughput:
sub-millisecond p99 latency, exactly-once state, and no per-key TTL
housekeeping to reason about, at the cost of running (and paying for) a
cluster continuously.

Would run via Kinesis Data Analytics for Apache Flink or a self-managed
Flink cluster reading from the same `igaming-player-events` Kinesis stream.
"""

from __future__ import annotations

import json

from pyflink.common import Types
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.datastream.connectors.kinesis import FlinkKinesisConsumer
from pyflink.datastream.functions import KeyedProcessFunction, RuntimeContext
from pyflink.datastream.state import ListStateDescriptor

CARD_TESTING_WINDOW_MS = 10 * 60 * 1000
CARD_TESTING_MIN_DISTINCT_BINS = 3


class CardTestingDetector(KeyedProcessFunction):
    """Keyed by player_id. Maintains a rolling list of (card_bin, event_time)
    in Flink-managed state (checkpointed, exactly-once) rather than an
    external store: the operator IS the state store here.
    """

    def open(self, runtime_context: RuntimeContext):
        self.history_state = runtime_context.get_list_state(
            ListStateDescriptor("card_bin_history", Types.TUPLE([Types.STRING(), Types.LONG()]))
        )

    def process_element(self, event: dict, ctx: "KeyedProcessFunction.Context"):
        if event.get("event_type") != "payment" or event.get("payment_type") != "deposit":
            return

        now_ms = ctx.timestamp()
        history = [(bin_, ts) for bin_, ts in self.history_state.get() if now_ms - ts <= CARD_TESTING_WINDOW_MS]
        history.append((event["card_bin"], now_ms))
        self.history_state.update(history)

        distinct_bins = {bin_ for bin_, _ in history}
        if len(distinct_bins) >= CARD_TESTING_MIN_DISTINCT_BINS:
            yield json.dumps(
                {
                    "scenario_type": "card_testing",
                    "entity_type": "payment",
                    "entity_id": event["entity_id"],
                    "player_id": event["player_id"],
                    "triggering_event_ts": event["ts"],
                }
            )


def build_job() -> StreamExecutionEnvironment:
    env = StreamExecutionEnvironment.get_execution_environment()

    consumer = FlinkKinesisConsumer(
        "igaming-player-events",
        Types.STRING(),
        {"aws.region": "eu-west-2", "flink.stream.initpos": "LATEST"},
    )

    (
        env.add_source(consumer)
        .map(json.loads)
        .key_by(lambda e: e["player_id"])
        .process(CardTestingDetector())
        .print()  # in production: sink to the fraud-flags Kinesis stream / DynamoDB
    )

    return env


if __name__ == "__main__":
    build_job().execute("card-testing-detector")
