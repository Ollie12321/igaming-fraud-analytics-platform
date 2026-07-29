"""iGaming Fraud & Analytics Platform: demo UI.

Run:
    streamlit run streamlit_app/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run` puts this file's own directory on sys.path, not the repo
# root; add the root explicitly so `config`, `ml`, etc. are importable
# regardless of the working directory this is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.express as px
import streamlit as st

from streamlit_app import data_access as da

st.set_page_config(page_title="iGaming Fraud & Analytics Platform", layout="wide", page_icon="🎰")

st.title("🎰 iGaming Fraud & Analytics Platform")
st.caption(
    "All data is synthetically generated (no real player, payment or gambling data). "
    "Streaming fraud detection runs on AWS Kinesis + Lambda + DynamoDB; batch analytics "
    "run on a local warehouse via dbt. See README for the reasoning behind that split."
)

tab_overview, tab_fraud, tab_ltv, tab_gigo = st.tabs(
    ["📊 Overview", "🛡️ Real-Time Fraud Detection", "💰 LTV & Churn", "⚠️ Data Quality → Model Quality"]
)

with tab_overview:
    ltv = da.load_player_ltv()
    churn = da.load_churn_labels()
    scd = da.load_scd_summary()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Players", f"{len(ltv):,}")
    col2.metric("Total LTV (GBP)", f"£{ltv['ltv_gbp'].sum():,.0f}")
    col3.metric("Total Sessions", f"{int(ltv['total_sessions'].fillna(0).sum()):,}")
    churn_rate = churn.loc[~churn["is_self_excluded_as_of"], "is_churned"].mean()
    col4.metric("28-day Churn Rate", f"{churn_rate:.1%}", help="Excludes self-excluded players")

    st.subheader("Player dimension (SCD Type 2): current state")
    current = scd[scd["is_current"]]
    c1, c2 = st.columns(2)
    with c1:
        fig = px.pie(
            current.groupby("vip_tier", as_index=False)["n"].sum(), names="vip_tier", values="n", title="VIP tier"
        )
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig = px.pie(
            current.groupby("self_exclusion_status", as_index=False)["n"].sum(),
            names="self_exclusion_status",
            values="n",
            title="Self-exclusion status",
        )
        st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Built from `dim_players_scd2`, a Type 2 slowly changing dimension derived directly from a "
        "full change-log source, so any historical date can be queried point-in-time-correctly."
    )

with tab_fraud:
    st.subheader("Detector performance vs. injected ground truth")
    st.caption(
        "Every number below is measured, not assumed: labelled fraud/abuse scenarios are injected into the "
        "synthetic data with ground truth that is never shown to the detector, then scored after the fact."
    )
    summary = da.load_fraud_summary()
    display = summary.rename(
        columns={
            "scenario_type": "Scenario",
            "entity_type": "Entity",
            "ground_truth_count": "Ground truth",
            "true_positive_count": "Caught",
            "false_positive_count": "False positives",
            "recall": "Recall",
            "avg_detection_latency_seconds": "Avg latency (s)",
        }
    )
    st.dataframe(display, use_container_width=True, hide_index=True)

    col1, col2 = st.columns(2)
    with col1:
        fig = px.bar(
            summary, x="scenario_type", y="recall", color="entity_type", barmode="group", title="Recall by scenario"
        )
        fig.update_yaxes(range=[0, 1], tickformat=".0%")
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        fig = px.bar(
            summary,
            x="scenario_type",
            y="avg_detection_latency_seconds",
            color="entity_type",
            barmode="group",
            title="Avg detection latency (seconds)",
        )
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Recent flags")
    flags = da.load_recent_flags()
    if flags.empty:
        st.info("No flags loaded yet. Run `python -m streaming.local_backtest` and reload the warehouse.")
    else:
        st.dataframe(flags, use_container_width=True, hide_index=True)

with tab_ltv:
    ltv = da.load_player_ltv().dropna(subset=["ltv_gbp"])
    churn = da.load_churn_labels()

    col1, col2 = st.columns(2)
    with col1:
        fig = px.histogram(ltv, x="ltv_gbp", nbins=50, title="LTV distribution (GBP)")
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        by_country = ltv.groupby("country", as_index=False)["ltv_gbp"].mean().sort_values("ltv_gbp", ascending=False)
        fig = px.bar(by_country, x="country", y="ltv_gbp", title="Avg LTV by market (GBP)")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Churn composition")
    composition = (
        churn.assign(bucket=lambda d: d["is_self_excluded_as_of"].map({True: "Self-excluded", False: "Churned"}))
        .loc[lambda d: d["is_churned"] | d["is_self_excluded_as_of"]]["bucket"]
        .value_counts()
        .reset_index()
    )
    composition.columns = ["Population", "Players"]
    st.dataframe(composition, hide_index=True)
    st.caption(
        "Self-excluded players are tracked separately rather than folded into 'churn'. See the "
        "Data Quality tab for what happens to a model if you don't make that distinction."
    )

with tab_gigo:
    st.subheader("Same model. Same players. Only the pipeline changed.")
    results = da.load_gigo_results()
    if results is None:
        st.info("Run `python -m ml.naive_vs_engineered` to generate this comparison.")
    else:
        pv = results["portfolio_value_distortion"]
        col1, col2, col3 = st.columns(3)
        col1.metric("Naive total deposits", f"£{pv['naive_total_deposits_unconverted_currency']:,.0f}")
        col2.metric("Engineered total deposits (GBP)", f"£{pv['engineered_total_deposits_gbp']:,.0f}")
        col3.metric("Distortion", f"{pv['combined_distortion_pct']:+.1f}%")

        st.caption(
            f"Of that {pv['combined_distortion_pct']:.1f}% distortion, deduplication of ingestion retries "
            f"alone accounts for {pv['dedup_only_distortion_pct']:.1f} points; summing five currencies as "
            f"if they were all GBP accounts for the remaining {pv['currency_mixing_distortion_pct']:.1f} points."
        )

        st.divider()

        sx = results["self_exclusion_contamination"]
        st.metric(
            "Self-excluded players mislabelled as ordinary 'churn' in the naive dataset",
            f"{sx['self_excluded_players_mislabelled_as_churn_in_naive']} / {sx['total_self_excluded_players']}",
        )
        st.caption(
            "A model trained on the naive label would recommend a win-back marketing campaign for every "
            "one of these accounts. The AUC below barely tells you anything is wrong."
        )

        st.divider()

        st.subheader("Churn model: naive vs. engineered features/labels")
        comparison_df = pd.DataFrame(
            {
                "Naive": results["naive"],
                "Engineered": results["engineered"],
            }
        ).T[["auc", "average_precision", "precision", "recall", "positive_rate"]]
        st.dataframe(comparison_df.style.format("{:.3f}"), use_container_width=True)
        st.caption(
            "AUC is close between the two runs, which is exactly the point: a model can look statistically "
            "fine while its labels are semantically wrong. The failure isn't in the model's ability to "
            "discriminate, it's in what a 'positive' prediction means and what action gets taken on it."
        )
