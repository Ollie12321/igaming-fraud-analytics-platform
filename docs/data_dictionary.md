# Data Dictionary

All tables are generated synthetically (see `datagen/`). No real player, payment, or
gambling data is used anywhere in this project. See
[`docs/data_governance.md`](data_governance.md) for how these columns are
classified, retained, and handled under erasure requests.

## Core entities

### `players`
| Column | Type | Notes |
|---|---|---|
| player_id | string (UUID) | Primary key |
| signup_ts | timestamp | |
| country | string | ISO alpha-2 |
| currency | string | ISO 4217 |
| acquisition_channel | string | affiliate / paid_search / organic / referral |
| date_of_birth | date | used only for age/KYC checks |

### `player_attribute_history`
Source of truth for the SCD Type 2 player dimension
(`dbt/models/marts/dim_players_scd2.sql`). One row per change to a tracked
attribute.

| Column | Type | Notes |
|---|---|---|
| player_id | string | FK -> players |
| effective_ts | timestamp | when this state became true |
| vip_tier | string | bronze / silver / gold / platinum |
| kyc_status | string | pending / verified / rejected |
| self_exclusion_status | string | none / self_excluded / cooling_off |
| risk_segment | string | low / medium / high |

### `devices`
| device_id | string | fingerprint hash |
| player_id | string | owning player (nullable for pre-provisioned shared fraud-ring devices) |
| first_seen_ts | timestamp | |
| os | string | |
| is_shared_fraud_ring | bool | ground-truth only, not exposed to detectors |

### `sessions`
| session_id, player_id, device_id, ip_address, country_from_ip, start_ts, end_ts |

### `login_events`
| login_id, player_id, device_id, ip_address, country_from_ip, ts, success_flag |

### `game_rounds` (bets)
| round_id, session_id, player_id, game_type (slots/table/sports), stake_amount, payout_amount, currency, ts |

### `payments`
| payment_id, player_id, payment_type (deposit/withdrawal), amount, currency, method, card_bin, device_id, ts, status |

### `bonuses`
| bonus_id, player_id, bonus_type, claim_ts, wagering_requirement_multiple, bonus_amount, device_id |

### `fraud_ground_truth` (never fed to the detector; used only to score precision/recall)
| entity_type, entity_id, scenario_type, injected_ts, ring_id (nullable) |

## Injected fraud/abuse scenarios

| scenario_type | Pattern | Streaming rule (Lambda + DynamoDB state) | Why streaming, not batch |
|---|---|---|---|
| `bonus_abuse_ring` | N accounts share a device fingerprint / card and all claim the same bonus within a short window | On bonus claim: look up fingerprint -> {player_ids} claimed-set in DynamoDB; flag if already present | Bonus can be withdrawn as cash within minutes of wagering completion |
| `card_testing` | Same account/device, many small deposits with different card BINs in a short window | Sliding window count of distinct `card_bin` per player/device (DynamoDB counter, TTL) | Stolen-card testing must be blocked before a large authorized charge |
| `account_takeover` | Login from a *new device + new geo* combination (for a player with prior history), followed by a withdrawal from that same device | On login: check DynamoDB known-device/known-geo history, stage a pending flag; on withdrawal from the same device: confirm and flag both events | Money must be frozen before it leaves the platform |
| `self_exclusion_breach` | A self-excluded player's device fingerprint reappears on a new login/signup | On login/signup: point lookup against self-exclusion table replica in DynamoDB | Legal/regulatory requirement: must block at login, not next day |
| `bot_betting` | Game-round inter-arrival times have abnormally low variance at high frequency | Rolling variance of inter-bet time per session (DynamoDB state) | Bots need to be stopped mid-session, not after the fact |
| `structuring` | Multiple deposits just under a reporting threshold within 24h | Rolling sum + count of near-threshold deposits per player (DynamoDB state) | AML structuring detection has a regulatory expectation of near-real-time flags |

## Batch-only marts (correctness over latency)

| Mart | Purpose |
|---|---|
| `dim_players_scd2` | Type 2 SCD: point-in-time correct player attributes (VIP tier, KYC, self-exclusion, risk segment) for any historical date |
| `fct_player_ltv` | Lifetime value per player, computed from clean/deduplicated/currency-normalised data |
| `fct_churn_labels` | 28-day forward-looking churn label per player, with self-exclusion tracked separately from organic churn |
| `fct_fraud_summary` | Rollup of streaming fraud flags vs. ground truth: recall and detection latency per scenario |

`ml/naive_vs_engineered.py` builds its two competing feature sets directly
(one from `raw.*`, one from the marts above) rather than via a dedicated dbt
mart. That's the entire point of the comparison: the same underlying data,
read two different ways.
