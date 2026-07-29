-- Scores the real-time detector against ground truth that was never fed to
-- it. This is the number that actually backs any "we caught X% of fraud"
-- claim, rather than an assumed one.

with ground_truth as (
    -- `player`-level rows are kept in ground truth for audit/traceability but
    -- excluded from scoring here: every rule in streaming/fraud_rules acts on
    -- a specific event (a claim, a payment, a login), never on a player_id
    -- directly, so a player-level row would always read as 0% recall by
    -- construction rather than reflecting anything about detector quality.
    select * from {{ ref('stg_fraud_ground_truth') }}
    where entity_type != 'player'
),

flags as (
    select * from {{ ref('stg_streaming_fraud_flags') }}
),

matched as (
    select
        gt.scenario_type,
        gt.entity_type,
        gt.entity_id,
        f.flag_id,
        f.detection_latency_seconds
    from ground_truth gt
    left join flags f
        on gt.entity_type = f.entity_type
       and gt.entity_id = f.entity_id
       and gt.scenario_type = f.scenario_type
),

false_positives as (
    select f.scenario_type, f.flag_id
    from flags f
    left join ground_truth gt
        on gt.entity_type = f.entity_type
       and gt.entity_id = f.entity_id
       and gt.scenario_type = f.scenario_type
    where gt.entity_id is null
)

select
    m.scenario_type,
    m.entity_type,
    count(distinct m.entity_id) as ground_truth_count,
    count(distinct m.flag_id) as true_positive_count,
    coalesce((
        select count(*) from false_positives fp where fp.scenario_type = m.scenario_type
    ), 0) as false_positive_count,
    least(round(count(distinct m.flag_id)::numeric / nullif(count(distinct m.entity_id), 0), 3), 1.0) as recall,
    round(avg(m.detection_latency_seconds)::numeric, 1) as avg_detection_latency_seconds
from matched m
group by 1, 2
order by 1, 2
