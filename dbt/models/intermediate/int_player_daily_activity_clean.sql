-- The "engineered" daily activity mart: deduplicated source data, currency
-- normalised to GBP, and bot sessions excluded (see int_bot_sessions.sql).

with clean_sessions as (
    select *
    from {{ ref('int_session_activity') }}
    where session_id not in (select session_id from {{ ref('int_bot_sessions') }})
),

session_daily as (
    select
        player_id,
        date_trunc('day', start_ts) as activity_date,
        count(*) as session_count,
        sum(total_stake) as total_stake,
        sum(net_gaming_revenue) as net_gaming_revenue
    from clean_sessions
    group by 1, 2
),

payments_daily as (
    select
        player_id,
        date_trunc('day', ts) as activity_date,
        sum(case when payment_type = 'deposit' then amount_gbp else 0 end) as deposits_gbp,
        sum(case when payment_type = 'withdrawal' then amount_gbp else 0 end) as withdrawals_gbp
    from {{ ref('stg_payments') }}
    where status = 'completed'
    group by 1, 2
)

select
    coalesce(s.player_id, p.player_id) as player_id,
    coalesce(s.activity_date, p.activity_date) as activity_date,
    coalesce(s.session_count, 0) as session_count,
    coalesce(s.total_stake, 0) as total_stake,
    coalesce(s.net_gaming_revenue, 0) as net_gaming_revenue,
    coalesce(p.deposits_gbp, 0) as deposits_gbp,
    coalesce(p.withdrawals_gbp, 0) as withdrawals_gbp
from session_daily s
full outer join payments_daily p
    on s.player_id = p.player_id and s.activity_date = p.activity_date
