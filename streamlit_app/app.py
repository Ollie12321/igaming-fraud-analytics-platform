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

from config import get_settings
from streamlit_app import data_access as da

st.set_page_config(page_title="iGaming Fraud & Analytics Platform", layout="wide", page_icon="🎰")

st.title("🎰 iGaming Fraud & Analytics Platform")
st.caption(
    "All data is synthetically generated (no real player, payment or gambling data). "
    "Streaming fraud detection runs on AWS Kinesis + Lambda + DynamoDB; batch analytics "
    "run on a local warehouse via dbt. See README for the reasoning behind that split."
)
if get_settings().use_snapshot_data:
    st.info(
        "This public demo reads a versioned data snapshot rather than a live database, "
        "the same batch-over-always-on argument this project makes about warehousing. "
        "Run it locally against the real pipeline for live queries: see README.",
        icon="📦",
    )

tab_overview, tab_batch_stream, tab_fraud, tab_ltv, tab_gigo, tab_governance = st.tabs(
    [
        "📊 Overview",
        "⏱️ Batch vs. Streaming",
        "🛡️ Real-Time Fraud Detection",
        "💰 LTV & Churn",
        "⚠️ Data Quality → Model Quality",
        "🔒 Data Governance",
    ]
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

    st.divider()
    st.markdown(
        "This project intentionally makes three arguments at once, each with its own tab above: "
        "streaming vs. batch is a design decision (⏱️), data engineering quality is what a downstream "
        "model actually inherits (⚠️), and governance/retention has to be designed in, not bolted on (🔒)."
    )

with tab_batch_stream:
    st.subheader("Same fraud pattern, different processing paradigm")
    st.caption(
        "Pick a fraud scenario and a hypothetical detection interval. The exposure figure below is "
        "computed from this project's real measured event rate and average transaction value for that "
        "scenario, not a made-up number."
    )

    summary = da.load_fraud_summary()
    exposure = da.load_scenario_exposure()
    simulation_days = get_settings().simulation_days

    joined = summary.merge(exposure, on=["scenario_type", "entity_type"], how="left")
    joined["label"] = joined["scenario_type"] + " · " + joined["entity_type"]

    choice = st.selectbox("Fraud scenario", joined["label"].tolist())
    row = joined.loc[joined["label"] == choice].iloc[0]

    events_per_day = row["ground_truth_count"] / simulation_days
    has_amount = pd.notna(row["avg_amount_gbp"])

    intervals = [
        ("Real-time (streaming, as deployed)", 0.0),
        ("Every 1 minute", 1 / 60),
        ("Every 15 minutes", 15 / 60),
        ("Every hour", 1.0),
        ("Every 6 hours", 6.0),
        ("Once a day (daily batch)", 24.0),
    ]
    labels = [i[0] for i in intervals]
    picked_label = st.select_slider("If this scenario were checked...", options=labels, value=labels[-1])
    interval_hours = dict(intervals)[picked_label]

    if interval_hours == 0.0:
        expected_wait_hours = row["avg_detection_latency_seconds"] / 3600
        wait_source = "the detector's actual measured average latency for this scenario"
    else:
        # Average wait for a periodic check against a roughly steady arrival
        # rate is half the check interval: events are as likely to occur
        # right after a check as right before the next one.
        expected_wait_hours = interval_hours / 2
        wait_source = "half the check interval (average wait for a steady arrival rate)"

    expected_extra_events = max(events_per_day * expected_wait_hours / 24, 0.0)

    if expected_wait_hours * 60 < 1:
        wait_display = f"{expected_wait_hours * 3600:.1f} sec"
    elif expected_wait_hours < 1:
        wait_display = f"{expected_wait_hours * 60:,.1f} min"
    else:
        wait_display = f"{expected_wait_hours:.1f} hrs"

    col1, col2, col3 = st.columns(3)
    col1.metric("Real events/day in this data", f"{events_per_day:.2f}")
    col2.metric("Expected time undetected", wait_display)
    if has_amount:
        exposure_gbp = expected_extra_events * row["avg_amount_gbp"]
        exposure_display = f"£{exposure_gbp:,.0f}" if exposure_gbp >= 1 else f"£{exposure_gbp:.2f}"
        col3.metric(
            "Estimated exposure before caught",
            exposure_display,
            help=f"{expected_extra_events:.3f} extra events × £{row['avg_amount_gbp']:,.2f} avg value/event",
        )
    else:
        col3.metric("Estimated extra events before caught", f"{expected_extra_events:.3f}")
    st.caption(f"Expected wait time uses {wait_source}.")

    chart_rows = []
    for label, hours in intervals:
        wait = (row["avg_detection_latency_seconds"] / 3600) if hours == 0.0 else hours / 2
        extra_events = max(events_per_day * wait / 24, 0.0)
        exposure_value = extra_events * row["avg_amount_gbp"] if has_amount else extra_events
        chart_rows.append({"Interval": label, "value": exposure_value})
    chart_df = pd.DataFrame(chart_rows)
    fig = px.bar(
        chart_df,
        x="Interval",
        y="value",
        title=f"{'Exposure (GBP)' if has_amount else 'Extra undetected events'} by detection interval",
    )
    fig.update_traces(
        marker_color=["#e74c3c" if label == picked_label else "#3b82f6" for label in chart_df["Interval"]]
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Why fraud/AML needs streaming**")
        st.markdown(
            "- A missed card-testing or account-takeover event is money out the door within minutes, "
            "not a stat that's slightly stale.\n"
            "- Every hour of delay is proportional, measurable exposure, shown above.\n"
            "- This project's real detector runs in Lambda off Kinesis, with the actual measured "
            "latency you selected above under 'Real-time'."
        )
    with col_b:
        st.markdown("**Why LTV/churn is fine on a daily batch**")
        st.markdown(
            "- A churn score or LTV figure that's a day old costs nothing: nobody actioned on it "
            "in real time anyway, the campaign it feeds runs weekly at most.\n"
            "- Running it as a full warehouse rebuild once a day is cheaper and simpler than "
            "streaming, with zero downside.\n"
            "- See the **LTV & Churn** tab: those numbers are refreshed by the batch dbt pipeline, "
            "not a stream."
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
        naive_total = pv["naive_total_deposits_unconverted_currency"]
        engineered_total = pv["engineered_total_deposits_gbp"]
        dedup_pct = pv["dedup_only_distortion_pct"]
        currency_pct = pv["currency_mixing_distortion_pct"]
        combined_pct = pv["combined_distortion_pct"]

        st.markdown("**Toggle each data engineering fix on or off and watch the reported number change:**")
        c1, c2 = st.columns(2)
        dedup_fixed = c1.checkbox("Deduplicate ingestion retries", value=False)
        currency_fixed = c2.checkbox("Normalise 5 currencies to GBP", value=False)

        gap = naive_total - engineered_total
        dedup_share = dedup_pct / combined_pct if combined_pct else 0
        currency_share = currency_pct / combined_pct if combined_pct else 0
        remaining_share = (0 if dedup_fixed else dedup_share) + (0 if currency_fixed else currency_share)
        live_total = engineered_total + gap * remaining_share
        live_distortion_pct = combined_pct * remaining_share

        col1, col2, col3 = st.columns(3)
        col1.metric("Reported total deposits (live)", f"£{live_total:,.0f}")
        col2.metric("Correct total deposits", f"£{engineered_total:,.0f}")
        col3.metric(
            "Distortion right now",
            f"{live_distortion_pct:+.1f}%",
            delta="Correct" if live_distortion_pct == 0 else f"{live_distortion_pct:.1f}% too high",
            delta_color="normal" if live_distortion_pct == 0 else "inverse",
        )

        if not dedup_fixed and not currency_fixed:
            st.caption(
                f"Untouched raw read: £{naive_total:,.0f} reported vs. £{engineered_total:,.0f} actual. "
                "Tick either box above to fix one issue at a time."
            )
        elif dedup_fixed and currency_fixed:
            st.success("Both fixes applied: the reported number now matches the actual figure exactly.")
        else:
            st.caption(f"One fix applied, {live_distortion_pct:.1f} points of distortion still remaining.")

        st.divider()

        sx = results["self_exclusion_contamination"]
        label_fixed = st.checkbox(
            "Correctly separate self-excluded players from ordinary churn", value=False, key="label_fix"
        )
        mislabelled = sx["self_excluded_players_mislabelled_as_churn_in_naive"]
        total_sx = sx["total_self_excluded_players"]
        if label_fixed:
            st.metric("Self-excluded players mislabelled as ordinary 'churn'", f"0 / {total_sx}")
            st.success("Fixed: self-excluded players are now a distinct population, not marketing-campaign targets.")
        else:
            st.metric("Self-excluded players mislabelled as ordinary 'churn'", f"{mislabelled} / {total_sx}")
            st.warning(
                "A model trained on this label would recommend a win-back marketing campaign for every "
                "one of these accounts, people who have asked the operator to stop letting them gamble."
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
            "discriminate, it's in what a 'positive' prediction means and what action gets taken on it. "
            "Toggling the checkboxes above shows the same lesson at the data layer, before a model is "
            "even involved: bad data engineering costs you the answer, not just the model's confidence in it."
        )

with tab_governance:
    st.subheader("Every column is classified, retained, and erasable on purpose")
    st.caption(
        "Parsed directly from `dbt/models/**/schema.yml`: this is the same metadata that governs access "
        "boundaries and the erasure logic elsewhere in the repo, not a separate copy of it. "
        "See [`docs/data_governance.md`](https://github.com/Ollie12321/igaming-fraud-analytics-platform/blob/main/docs/data_governance.md) for the policy."
    )

    classification = da.load_classification_summary()
    if classification.empty:
        st.info("No classification metadata found. Run this from the repo root.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            fig = px.sunburst(
                classification,
                path=["classification", "retention_category"],
                title="Columns by classification tier → retention category",
            )
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            pii_counts = classification["pii"].map({True: "PII", False: "Not PII"}).value_counts().reset_index()
            pii_counts.columns = ["Category", "Columns"]
            fig = px.pie(pii_counts, names="Category", values="Columns", title="PII vs. non-PII columns", hole=0.4)
            st.plotly_chart(fig, use_container_width=True)

        with st.expander(f"See all {len(classification)} classified columns"):
            st.dataframe(classification, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Retention policy")
    retention_policy = pd.DataFrame(
        [
            {
                "Category": "financial_aml",
                "Basis": "Money Laundering Regulations 2017, reg. 40",
                "Retention": "5 years",
            },
            {"Category": "kyc_regulatory", "Basis": "UK Gambling Commission LCCP / MLR 2017", "Retention": "5 years"},
            {
                "Category": "responsible_gambling",
                "Basis": "LCCP social responsibility requirements",
                "Retention": "5 years",
            },
            {"Category": "fraud_signal", "Basis": "Fraud/AML investigation record-keeping", "Retention": "5 years"},
            {
                "Category": "account_lifetime_plus_aml",
                "Basis": "Account lifetime, then AML retention clock starts",
                "Retention": "Active + 5 years",
            },
        ]
    )
    st.dataframe(retention_policy, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Try it: right-to-erasure vs. AML retention")
    st.caption(
        "UK GDPR Art. 17 gives players a right to erasure. The Money Laundering Regulations 2017 require "
        "5 years of transaction records regardless. Pick a sample player and submit a simulated request "
        "to see how `governance/erasure.py` resolves that conflict for real, no data is modified by this demo."
    )

    demo = da.load_erasure_demo()
    players = demo.get("players", [])
    if not players:
        st.info("No erasure demo data bundled. Run `python -m streamlit_app.export_snapshot`.")
    else:
        options = {
            f"{p['vip_tier']} · KYC: {p['kyc_status']} · self-exclusion: {p['self_exclusion_status']}": p
            for p in players
        }
        picked = st.selectbox("Sample player", list(options.keys()))
        player = options[picked]

        st.markdown(
            f"**Before:** IP address `{player['sample_ip_address']}`, date of birth `{player['date_of_birth']}`"
        )

        if st.button("Submit right-to-erasure request (simulated)", type="primary"):
            hashed_ip = da.pseudonymise(player["sample_ip_address"])
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Pseudonymised** (no AML value on their own)")
                st.code(f"ip_address  →  {hashed_ip}\ndate_of_birth  →  NULL", language=None)
                rows_affected = player["rows_affected"]
                st.caption(
                    f"{rows_affected.get('sessions.ip_address', 0)} session + "
                    f"{rows_affected.get('login_events.ip_address', 0)} login IP records hashed with a "
                    "salted, one-way, deterministic function computed live above, using this app's own "
                    "erasure salt."
                )
            with col2:
                st.markdown("**Retained in full** (regulatory basis)")
                retained_reasons = demo.get("retained_reasons", {})
                retained_rows = [
                    {"Table": table, "This player's rows": player["retained_row_counts"].get(table, 0), "Why": reason}
                    for table, reason in retained_reasons.items()
                    if table != "devices"
                ]
                st.dataframe(pd.DataFrame(retained_rows), use_container_width=True, hide_index=True)
            st.success(
                "Resolved: identifiers with no AML value are gone forever; financial, KYC, and fraud "
                "records survive for their statutory retention window."
            )
