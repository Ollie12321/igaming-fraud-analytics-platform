with sessions as (
    select * from {{ ref('stg_sessions') }}
),

round_agg as (
    select
        session_id,
        count(*) as round_count,
        sum(stake_amount) as total_stake,
        sum(payout_amount) as total_payout
    from {{ ref('stg_game_rounds') }}
    group by 1
)

select
    s.session_id,
    s.player_id,
    s.device_id,
    s.start_ts,
    s.end_ts,
    s.duration_minutes,
    coalesce(r.round_count, 0) as round_count,
    coalesce(r.total_stake, 0) as total_stake,
    coalesce(r.total_payout, 0) as total_payout,
    coalesce(r.total_stake, 0) - coalesce(r.total_payout, 0) as net_gaming_revenue
from sessions s
left join round_agg r on s.session_id = r.session_id
