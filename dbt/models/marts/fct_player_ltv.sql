with daily as (
    select * from {{ ref('int_player_daily_activity_clean') }}
),

players as (
    select * from {{ ref('stg_players') }}
)

select
    p.player_id,
    p.country,
    p.currency,
    p.acquisition_channel,
    p.signup_ts,
    min(d.activity_date) as first_active_date,
    max(d.activity_date) as last_active_date,
    date_part('day', max(d.activity_date) - min(d.activity_date)) + 1 as lifetime_days,
    sum(d.session_count) as total_sessions,
    sum(d.total_stake) as total_stake,
    sum(d.net_gaming_revenue) as ltv_gbp,
    sum(d.deposits_gbp) as total_deposits_gbp,
    sum(d.withdrawals_gbp) as total_withdrawals_gbp
from players p
left join daily d on p.player_id = d.player_id
group by 1, 2, 3, 4, 5
