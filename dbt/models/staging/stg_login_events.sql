with source as (
    select * from {{ source('raw', 'login_events') }}
),

deduplicated as (
    select
        *,
        row_number() over (partition by login_id order by ts) as rn
    from source
)

select
    login_id,
    player_id,
    device_id,
    ip_address,
    country_from_ip,
    ts,
    coalesce(success_flag, true) as success_flag
from deduplicated
where rn = 1
