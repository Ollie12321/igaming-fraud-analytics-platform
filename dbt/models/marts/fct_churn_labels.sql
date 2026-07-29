-- Engineered churn labelling. Two things a naive query typically gets wrong,
-- fixed here:
--   1. Point-in-time correctness: self-exclusion status is looked up AS OF
--      the observation date via the SCD2 dimension, not "as it is today".
--   2. Self-excluded players are flagged separately rather than silently
--      counted as "churned": they didn't leave because of anything a
--      win-back campaign or a churn model could influence, so mixing them
--      into the training population corrupts what the model learns.

with as_of as (
    select
        (select max(activity_date) from {{ ref('int_player_daily_activity_clean') }}) - interval '28 days'
        as as_of_date
),

activity_before as (
    select d.player_id, max(d.activity_date) as last_active_before_asof
    from {{ ref('int_player_daily_activity_clean') }} d
    cross join as_of
    where d.activity_date <= as_of.as_of_date
    group by 1
),

activity_after as (
    select distinct d.player_id
    from {{ ref('int_player_daily_activity_clean') }} d
    cross join as_of
    where d.activity_date > as_of.as_of_date
      and d.activity_date <= as_of.as_of_date + interval '28 days'
),

player_status_as_of as (
    select dp.player_id, dp.self_exclusion_status
    from {{ ref('dim_players_scd2') }} dp
    cross join as_of
    where dp.valid_from <= as_of.as_of_date
      and (dp.valid_to is null or dp.valid_to > as_of.as_of_date)
),

players as (
    select * from {{ ref('stg_players') }}
)

select
    p.player_id,
    (select as_of_date from as_of) as observation_date,
    ab.last_active_before_asof,
    coalesce(s.self_exclusion_status, 'none') as self_exclusion_status_as_of,
    (s.self_exclusion_status = 'self_excluded') as is_self_excluded_as_of,
    (aa.player_id is null) as is_churned
from players p
inner join activity_before ab on p.player_id = ab.player_id
left join activity_after aa on p.player_id = aa.player_id
left join player_status_as_of s on p.player_id = s.player_id
where p.signup_ts <= (select as_of_date from as_of)
