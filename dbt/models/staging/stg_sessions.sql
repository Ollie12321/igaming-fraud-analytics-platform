with source as (
    select * from {{ source('raw', 'sessions') }}
),

deduplicated as (
    select
        *,
        row_number() over (partition by session_id order by start_ts) as rn
    from source
)

select
    session_id,
    player_id,
    device_id,
    ip_address,
    country_from_ip,
    start_ts,
    end_ts,
    extract(epoch from (end_ts - start_ts)) / 60.0 as duration_minutes
from deduplicated
where rn = 1
