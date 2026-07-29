-- Batch-only bot detection: flags sessions with an abnormally high round
-- count and abnormally *low* variance in inter-bet timing. This is the same
-- underlying signal the streaming Lambda rule checks incrementally, but this
-- version only ever runs once a day, after the session is long over. It is
-- kept deliberately in this project to make the streaming-vs-batch trade-off
-- concrete: this query WOULD catch the bot, just many hours too late to stop
-- the money moving.

with round_gaps as (
    select
        session_id,
        player_id,
        ts,
        extract(epoch from (ts - lag(ts) over (partition by session_id order by ts))) as gap_seconds
    from {{ ref('stg_game_rounds') }}
),

session_stats as (
    select
        session_id,
        player_id,
        count(*) as round_count,
        avg(gap_seconds) as avg_gap_seconds,
        stddev(gap_seconds) as stddev_gap_seconds
    from round_gaps
    where gap_seconds is not null
    group by 1, 2
)

select
    session_id,
    player_id,
    round_count,
    avg_gap_seconds,
    stddev_gap_seconds
from session_stats
where round_count >= 100
  and stddev_gap_seconds < 0.3
