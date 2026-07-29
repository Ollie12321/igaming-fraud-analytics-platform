-- Ground truth labels for injected fraud/abuse scenarios. This table is used
-- ONLY to score the streaming detector after the fact (fct_fraud_summary):
-- it must never be joined into any feature set the detector or ML models see.

with source as (
    select * from {{ source('raw', 'fraud_ground_truth') }}
),

deduplicated as (
    select
        *,
        row_number() over (
            partition by entity_type, entity_id, scenario_type
            order by injected_ts
        ) as rn
    from source
)

select
    entity_type,
    entity_id,
    scenario_type,
    injected_ts,
    ring_id
from deduplicated
where rn = 1
