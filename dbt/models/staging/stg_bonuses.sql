with source as (
    select * from {{ source('raw', 'bonuses') }}
),

deduplicated as (
    select
        *,
        row_number() over (partition by bonus_id order by claim_ts) as rn
    from source
)

select
    bonus_id,
    player_id,
    bonus_type,
    claim_ts,
    wagering_requirement_multiple,
    bonus_amount,
    device_id
from deduplicated
where rn = 1
