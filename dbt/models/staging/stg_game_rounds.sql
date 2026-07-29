-- Deduplication matters here: at-least-once delivery from the ingestion layer
-- (Kinesis/Firehose retries, re-run DAGs) produces a small but real rate of
-- duplicate round events. Left un-deduplicated, LTV and RTP metrics are
-- overstated. This is deliberately demonstrated in ml/naive_vs_engineered.py.

with source as (
    select * from {{ source('raw', 'game_rounds') }}
),

deduplicated as (
    select
        *,
        row_number() over (partition by round_id order by ts) as rn
    from source
)

select
    round_id,
    session_id,
    player_id,
    game_type,
    stake_amount,
    payout_amount,
    ts
from deduplicated
where rn = 1
