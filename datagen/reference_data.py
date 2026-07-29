"""Static reference distributions used across the synthetic data generator.

Kept in one place so the generator, the streaming rules and the dbt models all
agree on the same category values.
"""

COUNTRIES = {
    # country: (currency, weight, base_deposit_multiplier)
    "GB": ("GBP", 0.42, 1.0),
    "IE": ("EUR", 0.10, 0.9),
    "SE": ("SEK", 0.12, 0.8),
    "DK": ("DKK", 0.08, 0.85),
    "DE": ("EUR", 0.13, 0.95),
    "CA": ("CAD", 0.15, 1.1),
}

ACQUISITION_CHANNELS = {
    "affiliate": 0.45,
    "paid_search": 0.25,
    "organic": 0.20,
    "referral": 0.10,
}

GAME_TYPES = {
    # game_type: (weight, mean_stake, stake_std, rtp)
    "slots": (0.55, 2.5, 3.0, 0.96),
    "table_games": (0.20, 8.0, 6.0, 0.985),
    "live_casino": (0.15, 6.0, 5.0, 0.97),
    "sports_betting": (0.10, 12.0, 10.0, 0.93),
}

PAYMENT_METHODS = {
    "debit_card": 0.55,
    "credit_card": 0.15,
    "e_wallet": 0.22,
    "bank_transfer": 0.08,
}

VIP_TIERS = ["bronze", "silver", "gold", "platinum"]
KYC_STATUSES = ["pending", "verified", "rejected"]
SELF_EXCLUSION_STATUSES = ["none", "cooling_off", "self_excluded"]
RISK_SEGMENTS = ["low", "medium", "high"]

ACTIVITY_SEGMENTS = {
    # segment: (weight, sessions_per_week_lambda, stake_multiplier)
    "casual": (0.55, 0.8, 1.0),
    "regular": (0.32, 2.5, 2.2),
    "vip": (0.13, 5.0, 8.0),
}

FRAUD_SCENARIOS = [
    "bonus_abuse_ring",
    "card_testing",
    "account_takeover",
    "self_exclusion_breach",
    "bot_betting",
    "structuring",
]

# AML reporting threshold used by the structuring scenario (illustrative, not jurisdiction-specific)
STRUCTURING_THRESHOLD_GBP = 10_000

# Mirrors dbt/seeds/fx_rates_to_gbp.csv. The streaming detector can't wait for
# a batch warehouse pipeline to tell it what a deposit is worth in GBP, so it
# carries its own small, hardcoded copy of the same reference rates.
FX_RATES_TO_GBP = {
    "GBP": 1.00,
    "EUR": 0.85,
    "SEK": 0.075,
    "DKK": 0.115,
    "CAD": 0.59,
}
