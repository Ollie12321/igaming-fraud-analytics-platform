with source as (
    select * from {{ source('raw', 'player_attribute_history') }}
),

deduplicated as (
    select
        *,
        row_number() over (
            partition by player_id, effective_ts
            order by effective_ts
        ) as rn
    from source
)

select
    player_id,
    effective_ts,
    vip_tier,
    kyc_status,
    self_exclusion_status,
    risk_segment
from deduplicated
where rn = 1
