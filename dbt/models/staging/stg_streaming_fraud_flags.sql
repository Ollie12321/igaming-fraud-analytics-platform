-- Fraud flags raised by the real-time detector (Lambda + DynamoDB in AWS, or
-- streaming/local_backtest.py for local/CI runs of the identical rule logic).
-- Replicated from the hot path (DynamoDB) into the warehouse for reporting;
-- the warehouse copy is never on the critical path for blocking a transaction.

with source as (
    select * from {{ source('raw', 'streaming_fraud_flags') }}
),

deduplicated as (
    select
        *,
        row_number() over (partition by flag_id order by detected_ts) as rn
    from source
)

select
    flag_id,
    scenario_type,
    entity_type,
    entity_id,
    player_id,
    triggering_event_ts,
    detected_ts,
    extract(epoch from (detected_ts - triggering_event_ts)) as detection_latency_seconds,
    severity
from deduplicated
where rn = 1
