-- SCD Type 2 player dimension, built directly from a full change-log source
-- (raw.player_attribute_history) using window functions rather than dbt's
-- `snapshot` feature. dbt snapshots are the right tool when you only ever see
-- a table's *current* state and need dbt to diff it on every run; here the
-- upstream system already emits every change as an event, so deriving
-- valid_from/valid_to directly is simpler, is fully reproducible on a single
-- backfill run, and needs no mutable snapshot state.
--
-- This is what makes point-in-time-correct training data possible: "what was
-- this player's VIP tier / self-exclusion status on the day we're building a
-- feature for" is a lookup against this table, not the current row in
-- `players`.

with attribute_history as (
    select * from {{ ref('stg_player_attribute_history') }}
),

with_valid_to as (
    select
        player_id,
        effective_ts as valid_from,
        lead(effective_ts) over (partition by player_id order by effective_ts) as valid_to,
        vip_tier,
        kyc_status,
        self_exclusion_status,
        risk_segment
    from attribute_history
),

players as (
    select * from {{ ref('stg_players') }}
)

select
    {{ dbt_utils.generate_surrogate_key(['p.player_id', 'w.valid_from']) }} as player_dimension_key,
    p.player_id,
    p.country,
    p.currency,
    p.acquisition_channel,
    p.activity_segment,
    p.signup_ts,
    w.valid_from,
    w.valid_to,
    (w.valid_to is null) as is_current,
    w.vip_tier,
    w.kyc_status,
    w.self_exclusion_status,
    w.risk_segment
from with_valid_to w
inner join players p using (player_id)
