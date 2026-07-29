with source as (
    select * from {{ source('raw', 'devices') }}
),

deduplicated as (
    select
        *,
        row_number() over (partition by device_id order by first_seen_ts) as rn
    from source
)

select
    device_id,
    player_id as first_seen_player_id,
    first_seen_ts,
    os,
    coalesce(is_shared_fraud_ring, false) as is_shared_fraud_ring
from deduplicated
where rn = 1
