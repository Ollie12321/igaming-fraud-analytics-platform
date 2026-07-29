with source as (
    select * from {{ source('raw', 'players') }}
),

deduplicated as (
    select
        *,
        row_number() over (partition by player_id order by signup_ts) as rn
    from source
)

select
    player_id,
    signup_ts,
    upper(country) as country,
    upper(currency) as currency,
    acquisition_channel,
    activity_segment,
    date_of_birth,
    date_part('year', age(signup_ts, date_of_birth)) as age_at_signup
from deduplicated
where rn = 1
