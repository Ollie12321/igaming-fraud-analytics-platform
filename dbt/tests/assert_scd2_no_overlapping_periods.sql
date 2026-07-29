-- Fails if any player has two SCD2 rows whose [valid_from, valid_to) ranges overlap.

with scd as (
    select * from {{ ref('dim_players_scd2') }}
),

joined as (
    select
        a.player_id,
        a.valid_from as a_from,
        a.valid_to as a_to,
        b.valid_from as b_from,
        b.valid_to as b_to
    from scd a
    inner join scd b
        on a.player_id = b.player_id
        and a.valid_from < b.valid_from
    where a.valid_to is null or a.valid_to > b.valid_from
)

select * from joined
