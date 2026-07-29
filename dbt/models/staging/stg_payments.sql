-- Same at-least-once duplicate issue as game_rounds, plus currency
-- normalisation: five markets means five currencies, and naively summing
-- raw `amount` across currencies (as the naive feature set deliberately
-- does) silently produces a meaningless LTV number.

with source as (
    select * from {{ source('raw', 'payments') }}
),

deduplicated as (
    select
        *,
        row_number() over (partition by payment_id order by ts) as rn
    from source
),

fx as (
    select * from {{ ref('fx_rates_to_gbp') }}
)

select
    d.payment_id,
    d.player_id,
    d.payment_type,
    d.amount,
    d.currency,
    d.amount * coalesce(fx.rate_to_gbp, 1.0) as amount_gbp,
    d.method,
    d.card_bin,
    d.device_id,
    d.ts,
    d.status
from deduplicated d
left join fx on upper(d.currency) = fx.currency
where d.rn = 1
