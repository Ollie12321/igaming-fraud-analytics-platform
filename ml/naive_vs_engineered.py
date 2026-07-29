"""The centrepiece demonstration of this project: the same churn model,
trained on the same underlying players, twice.

  "naive" = queried directly from raw.* tables the way an analyst who skips
            the modelled warehouse layer would: no deduplication of
            at-least-once ingestion retries, no currency normalisation across
            five markets, bot-inflated sessions included at face value, and
            self-excluded players silently folded into the "churned" class.

  "engineered" = read from the dbt marts (dim_players_scd2, fct_player_ltv,
                 fct_churn_labels): deduplicated, GBP-normalised, bot sessions
                 excluded, and self-excluded players held out of the churn
                 population because they didn't leave for a reason any
                 win-back model could act on.

Both are fed into the same LogisticRegression, on the same train/test split,
so the only variable is data quality. Results are written to
ml/artifacts/comparison_results.json for the Streamlit app to render.

Usage:
    python -m ml.naive_vs_engineered
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine, text

from config import get_settings

ARTIFACT_DIR = Path("ml/artifacts")
RANDOM_STATE = 42
CHURN_WINDOW_DAYS = 28


def _engine():
    return create_engine(get_settings().warehouse_sqlalchemy_url)


def _as_of_date(engine) -> pd.Timestamp:
    query = "select observation_date from public_marts.fct_churn_labels limit 1"
    return pd.read_sql(text(query), engine)["observation_date"].iloc[0]


def build_naive_dataset(engine, as_of: pd.Timestamp) -> pd.DataFrame:
    """Straight off raw.*: duplicates, mixed currencies, bot sessions and
    self-exclusion all included exactly as they arrived.
    """
    query = """
        with rounds as (
            select player_id, ts as event_ts, stake_amount as stake, 0.0 as deposit, 0.0 as withdrawal
            from raw.game_rounds
        ),
        pays as (
            select
                player_id, ts as event_ts, 0.0 as stake,
                case when payment_type = 'deposit' then amount else 0 end as deposit,
                case when payment_type = 'withdrawal' then amount else 0 end as withdrawal
            from raw.payments
        ),
        activity as (select * from rounds union all select * from pays),
        agg as (
            select
                player_id,
                count(*) filter (where stake > 0) as total_rounds_naive,
                sum(stake) as total_stake_naive,
                sum(deposit) as total_deposits_naive,
                sum(withdrawal) as total_withdrawals_naive,
                min(event_ts) as first_activity,
                max(event_ts) as last_activity
            from activity
            group by player_id
        )
        select a.*, p.signup_ts
        from agg a
        join raw.players p using (player_id)
    """
    df = pd.read_sql(text(query), engine)
    df["tenure_days"] = (df["last_activity"] - df["signup_ts"]).dt.days.clip(lower=0)
    df = df[df["first_activity"] <= as_of].copy()
    df["is_churned_naive"] = df["last_activity"] <= as_of
    return df


def build_engineered_dataset(engine) -> pd.DataFrame:
    """From the dbt marts: deduplicated, GBP-normalised, bot-excluded, and
    self-excluded players are known and can be deliberately held out.
    """
    query = """
        select
            ltv.player_id,
            ltv.total_sessions,
            ltv.total_stake as total_stake_engineered,
            ltv.total_deposits_gbp,
            ltv.total_withdrawals_gbp,
            ltv.ltv_gbp,
            ltv.signup_ts,
            ltv.last_active_date,
            churn.is_churned,
            churn.is_self_excluded_as_of
        from public_marts.fct_player_ltv ltv
        inner join public_marts.fct_churn_labels churn using (player_id)
    """
    df = pd.read_sql(text(query), engine)
    df["tenure_days"] = (df["last_active_date"] - df["signup_ts"]).dt.days.clip(lower=0)
    return df


def _dedup_only_distortion_pct(engine) -> float:
    """How much of the distortion is just double-counted ingestion retries,
    holding currency handling constant (both sides converted to GBP)? Isolates
    the dedup effect from the much larger currency-mixing effect.
    """
    query = """
        select
            sum(amount * fx.rate_to_gbp) as raw_with_dupes_gbp,
            sum(amount * fx.rate_to_gbp) filter (where rn = 1) as deduped_gbp
        from (
            select *, row_number() over (partition by payment_id order by ts) as rn
            from raw.payments
        ) p
        join public.fx_rates_to_gbp fx on upper(p.currency) = fx.currency
        where payment_type = 'deposit'
    """
    row = pd.read_sql(text(query), engine).iloc[0]
    return float((row["raw_with_dupes_gbp"] - row["deduped_gbp"]) / row["deduped_gbp"] * 100)


def _fit_and_score(X: pd.DataFrame, y: pd.Series) -> dict:
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=RANDOM_STATE, stratify=y)
    # class_weight="balanced" because churn is rare (~2-3% positive); without
    # it, a 0.5 decision threshold never fires and precision/recall are
    # meaninglessly 0 for both models, which would hide rather than reveal
    # the data-quality gap this comparison exists to show.
    model = make_pipeline(
        StandardScaler(), LogisticRegression(max_iter=1000, random_state=RANDOM_STATE, class_weight="balanced")
    )
    model.fit(X_train, y_train)
    proba = model.predict_proba(X_test)[:, 1]
    preds = model.predict(X_test)

    return {
        "n_train": len(X_train),
        "n_test": len(X_test),
        "positive_rate": float(y.mean()),
        "auc": float(roc_auc_score(y_test, proba)),
        "average_precision": float(average_precision_score(y_test, proba)),
        "precision": float(precision_score(y_test, preds, zero_division=0)),
        "recall": float(recall_score(y_test, preds, zero_division=0)),
    }


def run() -> dict:
    engine = _engine()
    as_of = _as_of_date(engine)

    naive = build_naive_dataset(engine, as_of)
    engineered = build_engineered_dataset(engine)

    naive_features = naive[["total_rounds_naive", "total_stake_naive", "total_deposits_naive", "tenure_days"]]
    naive_labels = naive["is_churned_naive"]
    naive_metrics = _fit_and_score(naive_features, naive_labels)

    engineered_clean = engineered[~engineered["is_self_excluded_as_of"]]
    engineered_features = engineered_clean[
        ["total_sessions", "total_stake_engineered", "total_deposits_gbp", "tenure_days"]
    ]
    engineered_labels = engineered_clean["is_churned"]
    engineered_metrics = _fit_and_score(engineered_features, engineered_labels)

    # Aggregate distortion: what does "total portfolio value" look like if you
    # never deduplicate ingestion retries or convert currency? Isolated so the
    # two effects aren't conflated into one unexplained number.
    naive_total_deposits = float(naive["total_deposits_naive"].sum())
    engineered_total_deposits = float(engineered["total_deposits_gbp"].sum())
    deposit_distortion_pct = (naive_total_deposits - engineered_total_deposits) / engineered_total_deposits * 100
    dedup_only_pct = _dedup_only_distortion_pct(engine)

    n_self_excluded_mislabelled = int((engineered["is_self_excluded_as_of"] & engineered["is_churned"]).sum())

    results = {
        "as_of_date": str(as_of),
        "naive": naive_metrics,
        "engineered": engineered_metrics,
        "auc_delta": engineered_metrics["auc"] - naive_metrics["auc"],
        "portfolio_value_distortion": {
            "naive_total_deposits_unconverted_currency": round(naive_total_deposits, 2),
            "engineered_total_deposits_gbp": round(engineered_total_deposits, 2),
            "combined_distortion_pct": round(deposit_distortion_pct, 1),
            "dedup_only_distortion_pct": round(dedup_only_pct, 1),
            "currency_mixing_distortion_pct": round(deposit_distortion_pct - dedup_only_pct, 1),
        },
        "self_exclusion_contamination": {
            "self_excluded_players_mislabelled_as_churn_in_naive": n_self_excluded_mislabelled,
            "total_self_excluded_players": int(engineered["is_self_excluded_as_of"].sum()),
        },
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ARTIFACT_DIR / "comparison_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")
    return results


if __name__ == "__main__":
    run()
