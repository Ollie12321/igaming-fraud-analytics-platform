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

VIP_TIER_INFO = {
    "bronze": "Entry tier, no deposit threshold. Standard bonus terms and support queue.",
    "silver": "Reached after sustained deposits. Slightly better bonus wagering terms than bronze.",
    "gold": "High-value tier: bespoke offers, faster withdrawals, a named account manager.",
    "platinum": "The operator's biggest spenders. Also the tier fraud/AML teams watch closest, because it's "
    "where account-takeover and structuring payouts are largest in absolute terms.",
}
KYC_INFO = {
    "pending": "Identity/age verification hasn't completed yet. Withdrawals are restricted until it does.",
    "verified": "Identity confirmed against official documents, required before withdrawals under UK Gambling "
    "Commission rules.",
    "rejected": "Verification failed (document mismatch, underage, sanctions hit). Account is deposit-only.",
}
SELF_EXCLUSION_INFO = {
    "none": "No self-exclusion in place.",
    "cooling_off": "A short, player-initiated break. The account reactivates automatically; not the same as "
    "self-exclusion.",
    "self_excluded": "A player-initiated, effectively irreversible request to stop gambling (an LCCP "
    "responsible-gambling requirement). Must never be re-marketed to, which is exactly why folding these "
    "players into 'churn' is a serious labelling mistake. See the Data Quality tab.",
}
RISK_SEGMENT_INFO = {
    "low": "Standard monitoring, no active flags.",
    "medium": "Elevated monitoring: deposit velocity, device sharing, or other soft signals.",
    "high": "Active fraud/AML monitoring, e.g. structuring-pattern deposits or fraud-ring device links.",
}
CLASSIFICATION_INFO = {
    "public": "No restriction. Safe to share externally, e.g. game type codes.",
    "internal": "Employee-only; not published externally, but not sensitive on its own.",
    "confidential": "Business-sensitive or personal data requiring access control, e.g. financial totals.",
    "restricted": "Highest sensitivity: direct identifiers or special-category data with legal access limits, "
    "e.g. date of birth, IP address.",
}
FRAUD_SCENARIO_INFO = {
    "account_takeover": "Someone other than the account owner logs in with stolen credentials and drains funds.",
    "bonus_abuse_ring": "Linked accounts (shared device/IP) systematically farm the same signup or reload bonus.",
    "bot_betting": "Automated scripts place bets, usually to exploit an odds error or launder funds through play.",
    "card_testing": "Stolen card numbers are validated with many small deposits before one large fraudulent one.",
    "self_exclusion_breach": "A player who self-excluded logs back in, which the operator must legally prevent.",
    "structuring": "Deposits are split into amounts just under a reporting threshold to evade AML detection.",
}


def beginner_box(title: str, body: str) -> None:
    with st.expander(f"🎓 New to this? {title}"):
        st.markdown(body)


def explain_segment(
    options: list[str],
    info: dict[str, str],
    *,
    key: str,
    prompt: str = "Pick a segment for a plain-English explanation",
) -> None:
    """Always-works chart explainer.

    Streamlit's plotly `on_select` is unreliable for pie/donut charts (Plotly
    doesn't emit a usable selection event for those traces), so we pair every
    explainer chart with an explicit segment picker instead. Same outcome for
    the visitor, works on every chart type and every device.
    """
    if not options:
        return
    picked = st.radio(prompt, options, horizontal=True, key=key)
    text = info.get(str(picked).lower()) or info.get(str(picked))
    if text:
        st.info(f"**{picked}**: {text}")
    else:
        st.info(f"**{picked}**")


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

tab_overview, tab_scd, tab_batch_stream, tab_fraud, tab_ltv, tab_gigo, tab_governance = st.tabs(
    [
        "📊 Overview",
        "🕰️ Slowly Changing Dimensions",
        "⏱️ Batch vs. Streaming",
        "🛡️ Real-Time Fraud Detection",
        "💰 LTV & Churn",
        "⚠️ Data Quality → Model Quality",
        "🔒 Data Governance",
    ]
)

with tab_overview:
    beginner_box(
        "What is this dashboard?",
        "This is the analytics layer of a simulated online gambling platform. Every player, bet, payment and "
        "fraud case here is synthetic, nobody's real data was used, but the pipeline that produced it, and every "
        "number below, is real: generated and measured by actually running the code in this repository, not "
        "written by hand. Use the tabs above to explore different parts of the platform. Most charts respond to "
        "clicks, and most numbers have a hover tooltip (the small `?`) explaining what they mean.",
    )

    ltv = da.load_player_ltv()
    churn = da.load_churn_labels()
    scd = da.load_scd_summary()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Players", f"{len(ltv):,}", help="Total registered player accounts in the simulation.")
    col2.metric(
        "Total LTV (GBP)",
        f"£{ltv['ltv_gbp'].sum():,.0f}",
        help="Lifetime Value: total deposits plus net gaming result, minus withdrawals, summed across every "
        "player and converted to GBP.",
    )
    col3.metric(
        "Total Sessions",
        f"{int(ltv['total_sessions'].fillna(0).sum()):,}",
        help="A session is one continuous period of play, from login to logout or timeout.",
    )
    churn_rate = churn.loc[~churn["is_self_excluded_as_of"], "is_churned"].mean()
    col4.metric(
        "28-day Churn Rate",
        f"{churn_rate:.1%}",
        help="Share of players with no activity in the 28 days after the observation date. Self-excluded "
        "players are deliberately excluded from this figure rather than counted as churned, see the "
        "Data Quality tab for why that distinction matters.",
    )

    st.subheader("Player dimension (SCD Type 2): current state")
    current = scd[scd["is_current"]]
    c1, c2 = st.columns(2)
    with c1:
        vip_df = current.groupby("vip_tier", as_index=False)["n"].sum()
        fig = px.pie(vip_df, names="vip_tier", values="n", title="VIP tier")
        st.plotly_chart(fig, use_container_width=True)
        explain_segment(vip_df["vip_tier"].tolist(), VIP_TIER_INFO, key="pick_vip")
    with c2:
        sx_df = current.groupby("self_exclusion_status", as_index=False)["n"].sum()
        fig = px.pie(
            sx_df,
            names="self_exclusion_status",
            values="n",
            title="Self-exclusion status",
        )
        st.plotly_chart(fig, use_container_width=True)
        explain_segment(sx_df["self_exclusion_status"].tolist(), SELF_EXCLUSION_INFO, key="pick_selfexcl")

    st.caption(
        "Built from `dim_players_scd2`, a Type 2 slowly changing dimension derived directly from a "
        "full change-log source, so any historical date can be queried point-in-time-correctly. "
        "See the Slowly Changing Dimensions tab for why that matters, with a worked example."
    )

    st.divider()
    st.markdown(
        "This project intentionally makes four arguments at once, each with its own tab above: "
        "history matters, not just current state (🕰️), streaming vs. batch is a design decision (⏱️), "
        "data engineering quality is what a downstream model actually inherits (⚠️), and "
        "governance/retention has to be designed in, not bolted on (🔒)."
    )

with tab_scd:
    st.subheader("Why 'the current value' isn't good enough")
    beginner_box(
        "What's a Slowly Changing Dimension?",
        "A player's VIP tier, KYC status, self-exclusion status, and risk segment all change over time. If a "
        "database only stores the *current* value of each, you lose the ability to answer 'what was true back "
        "then?', which matters for fraud investigations, regulatory reporting, and training a model without "
        "cheating (using information from the future that the business didn't actually have at the time). A "
        "'Slowly Changing Dimension Type 2' (SCD2) keeps every version of a row, each stamped with the exact "
        "window of time it was true (`valid_from` / `valid_to`), so any historical date can be queried "
        "correctly, not just 'now'.",
    )

    timeline = da.load_scd_timeline()
    timeline["valid_from"] = pd.to_datetime(timeline["valid_from"])
    valid_to_dt = pd.to_datetime(timeline["valid_to"])
    now_reference = max(valid_to_dt.max(), timeline["valid_from"].max()) + pd.Timedelta(days=14)
    timeline["valid_to_display"] = valid_to_dt.fillna(now_reference)

    version_counts = timeline.groupby("player_id").size()
    player_options = version_counts.sort_values(ascending=False).index.tolist()
    picked_player = st.selectbox(
        "Pick a player with a recorded history",
        player_options,
        format_func=lambda pid: f"{pid[:8]}… ({version_counts[pid]} recorded attribute changes)",
    )
    player_rows = timeline[timeline["player_id"] == picked_player].sort_values("valid_from").reset_index(drop=True)

    attr_cols = ["vip_tier", "kyc_status", "self_exclusion_status", "risk_segment"]
    melted = [
        {
            "Attribute": attr.replace("_", " ").title(),
            "Value": row[attr],
            "Start": row["valid_from"],
            "Finish": row["valid_to_display"],
        }
        for _, row in player_rows.iterrows()
        for attr in attr_cols
    ]
    fig = px.timeline(
        pd.DataFrame(melted),
        x_start="Start",
        x_end="Finish",
        y="Attribute",
        color="Value",
        title=f"Attribute history for player {picked_player[:8]}…",
    )
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Each bar is one SCD2 row: a period during which that attribute value was actually true. Derived "
        "directly from a full change-log source (`raw.player_attribute_history`) using window functions, no "
        "mutable snapshot state needed. Hover a bar for exact dates."
    )

    st.divider()
    st.subheader("Try it: ask the same question two ways")
    st.caption("Imagine you're a fraud analyst investigating something that happened on a specific date.")
    min_d, max_d = player_rows["valid_from"].min().date(), now_reference.date()
    picked_date = st.slider("Pick a date to investigate", min_value=min_d, max_value=max_d, value=min_d)

    # Compare at day granularity, matching what the slider actually offers:
    # a valid_from later on the same calendar day as `picked_date` should
    # still count as covering it.
    valid_from_date = player_rows["valid_from"].dt.date
    valid_to_date = player_rows["valid_to_display"].dt.date
    point_in_time_row = player_rows[(valid_from_date <= picked_date) & (valid_to_date >= picked_date)]
    current_row = player_rows[player_rows["is_current"]]

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Correct: point-in-time SCD2 lookup**")
        if not point_in_time_row.empty:
            r = point_in_time_row.iloc[0]
            st.write(f"Risk segment on {picked_date}: **{r['risk_segment']}**")
            st.write(f"KYC status on {picked_date}: **{r['kyc_status']}**")
        else:
            st.write("No record covers this date (before signup or after the data window).")
    with col2:
        st.markdown("**Naive: 'just join the current row'**")
        r_naive = current_row.iloc[0]
        st.write(f"Risk segment 'as of' {picked_date}: **{r_naive['risk_segment']}**")
        st.write(f"KYC status 'as of' {picked_date}: **{r_naive['kyc_status']}**")

    if not point_in_time_row.empty:
        r_correct = point_in_time_row.iloc[0]
        if r_correct["risk_segment"] != r_naive["risk_segment"] or r_correct["kyc_status"] != r_naive["kyc_status"]:
            st.error(
                "Mismatch for this date: the naive 'current value' lookup gives the wrong answer. This player "
                "happens to show it clearly; for how often this goes wrong across the whole dataset, see below."
            )
        else:
            st.success("No mismatch for this particular date/player. Try a different date, or see the number below.")

    gigo = da.load_gigo_results()
    if gigo and "scd_point_in_time_lookup" in gigo:
        sp = gigo["scd_point_in_time_lookup"]
        total = sp["total_lookups"]
        mismatches = sp["mismatches_using_current_attributes_instead_of_scd"]
        pct = mismatches / total * 100 if total else 0
        st.metric(
            "Across every real fraud-investigation lookup in this dataset",
            f"{mismatches} / {total} wrong ({pct:.1f}%)",
            help="Every fraud/abuse ground-truth event has a timestamp. This compares the player's risk "
            "segment looked up point-in-time via SCD2 (correct) against looked-up from the current/latest "
            "row (naive) for every one of those events.",
        )

    with st.expander("See the SQL: naive current-state join vs. correct point-in-time join"):
        st.code(
            "-- Naive: always joins today's attributes, even for a historical event\n"
            "select e.*, p.risk_segment\n"
            "from fraud_events e\n"
            "join players p on p.player_id = e.player_id;  -- 'players' only ever has the current row",
            language="sql",
        )
        st.code(
            "-- Correct: joins the attribute value that was true AT THE TIME of the event\n"
            "-- (this is the real query from dbt/models/marts/fct_churn_labels.sql)\n"
            "select p.player_id, p.self_exclusion_status\n"
            "from dim_players_scd2 p\n"
            "where p.valid_from <= :as_of_date\n"
            "  and (p.valid_to is null or p.valid_to > :as_of_date);",
            language="sql",
        )

with tab_batch_stream:
    st.subheader("Same fraud pattern, different processing paradigm")
    beginner_box(
        "Why would you ever check for fraud less often than 'always'?",
        "Checking continuously (streaming) costs more to build and run than checking on a schedule (batch). "
        "That cost is worth it exactly when the delay between something happening and someone noticing it "
        "translates directly into money lost or harm done, and not worth it when it doesn't. This tab lets you "
        "put a real number on that delay for different fraud types, using this project's own measured data.",
    )
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

    choice = st.selectbox(
        "Fraud scenario",
        joined["label"].tolist(),
        help="Each option is a distinct fraud/abuse pattern injected into the synthetic data. See the "
        "Real-Time Fraud Detection tab for what each one means.",
    )
    row = joined.loc[joined["label"] == choice].iloc[0]
    scenario_key = row["scenario_type"]
    if scenario_key in FRAUD_SCENARIO_INFO:
        st.caption(f"ℹ️ {FRAUD_SCENARIO_INFO[scenario_key]}")

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
    picked_label = st.select_slider(
        "If this scenario were checked...",
        options=labels,
        value=labels[-1],
        help="Drag towards 'Real-time' to see what the actually-deployed streaming detector achieves; drag "
        "towards 'daily batch' to see what you'd be risking if you ran this the same way as LTV/churn.",
    )
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
    col1.metric(
        "Real events/day in this data",
        f"{events_per_day:.2f}",
        help="Ground-truth count for this scenario divided by the number of days simulated.",
    )
    col2.metric(
        "Expected time undetected",
        wait_display,
        help="How long, on average, this type of event would sit undetected at the selected check interval.",
    )
    if has_amount:
        exposure_gbp = expected_extra_events * row["avg_amount_gbp"]
        exposure_display = f"£{exposure_gbp:,.0f}" if exposure_gbp >= 1 else f"£{exposure_gbp:.2f}"
        col3.metric(
            "Estimated exposure before caught",
            exposure_display,
            help=f"{expected_extra_events:.3f} extra events × £{row['avg_amount_gbp']:,.2f} avg value/event "
            "(the average GBP value at risk per event of this type, measured from this project's own "
            "payment/stake data).",
        )
    else:
        col3.metric(
            "Estimated extra events before caught",
            f"{expected_extra_events:.3f}",
            help="This scenario type (a login event) has no direct monetary value attached, so this counts "
            "events rather than pounds.",
        )
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
    beginner_box(
        "How do you know if a fraud detector is actually any good?",
        "You inject known fraud/abuse patterns into the data with a hidden 'ground truth' label, never shown "
        "to the detector, then check afterwards what fraction it actually caught (recall), how many innocent "
        "events it wrongly flagged (false positives), and how quickly it flagged the real ones (latency). "
        "That's exactly what this tab shows: nothing here is asserted, it's all scored after the fact.",
    )
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
        scenario_options = summary["scenario_type"].drop_duplicates().tolist()
        explain_segment(scenario_options, FRAUD_SCENARIO_INFO, key="pick_fraud_scenario")
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
        st.caption(
            "Note the log-scale-worthy gap: most rules fire in under a second. Account takeover (login) is the "
            "exception, see the footnote in the README, it waits for a second confirming signal by design."
        )

    st.subheader("Recent flags")
    flags = da.load_recent_flags()
    if flags.empty:
        st.info("No flags loaded yet. Run `python -m streaming.local_backtest` and reload the warehouse.")
    else:
        st.dataframe(flags, use_container_width=True, hide_index=True)

with tab_ltv:
    beginner_box(
        "What's LTV and why does churn get measured this way?",
        "Lifetime Value (LTV) is roughly 'how much money has this player generated for the operator, in total, "
        "converted to one currency'. Churn is 'has this player gone quiet'. Both are computed on a daily batch "
        "schedule (see the Batch vs. Streaming tab for why that's the right call here) rather than continuously.",
    )
    ltv = da.load_player_ltv().dropna(subset=["ltv_gbp"])
    churn = da.load_churn_labels()

    col1, col2 = st.columns(2)
    with col1:
        fig = px.histogram(ltv, x="ltv_gbp", nbins=50, title="LTV distribution (GBP)")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Heavily right-skewed, as is typical: a small share of players account for a large share of value.")
    with col2:
        by_country = ltv.groupby("country", as_index=False)["ltv_gbp"].mean().sort_values("ltv_gbp", ascending=False)
        fig = px.bar(by_country, x="country", y="ltv_gbp", title="Avg LTV by market (GBP)")
        st.plotly_chart(fig, use_container_width=True)
        country = st.radio(
            "Pick a market for a plain-English explanation",
            by_country["country"].tolist(),
            horizontal=True,
            key="pick_ltv_country",
        )
        row = by_country.loc[by_country["country"] == country].iloc[0]
        n_players = int((ltv["country"] == country).sum())
        st.info(
            f"**{country}**: average LTV £{row['ltv_gbp']:,.0f} across {n_players:,} players in this market. "
            "Figures are GBP-normalised deposits plus net gaming result, minus withdrawals."
        )

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
    beginner_box(
        "What does 'garbage in, garbage out' actually mean here?",
        "Every card below is a real, specific way a naive database query silently produces a wrong number, "
        "each shown with the actual broken SQL and the actual fix. Some of them measurably change the "
        "figures in this exact dataset, tick the box and watch the number move. A few didn't happen to be "
        "triggered by this particular synthetic run, those are labelled clearly, but the SQL pattern is real "
        "and common in production. The lesson underneath all ten: a model trained on any of this can look "
        "statistically fine and still be quietly wrong.",
    )
    results = da.load_gigo_results()
    if results is None:
        st.info("Run `python -m ml.naive_vs_engineered` to generate this comparison.")
    else:
        pv = results["portfolio_value_distortion"]
        naive_total = pv["naive_total_deposits_unconverted_currency"]
        engineered_total = pv["engineered_total_deposits_gbp"]
        dedup_pct = pv["dedup_only_distortion_pct"]
        declined_pct = pv.get("declined_payments_distortion_pct", 0.0)
        currency_pct = pv["currency_mixing_distortion_pct"]
        combined_pct = pv["combined_distortion_pct"]
        gap = naive_total - engineered_total
        dedup_share = dedup_pct / combined_pct if combined_pct else 0
        declined_share = declined_pct / combined_pct if combined_pct else 0
        currency_share = currency_pct / combined_pct if combined_pct else 0

        st.markdown("#### Live deposit calculator: tick each fix and watch the total move")
        c1, c2, c3 = st.columns(3)
        dedup_fixed = c1.checkbox("1. Deduplicate ingestion retries", value=False)
        currency_fixed = c2.checkbox("2. Normalise 5 currencies to GBP", value=False)
        declined_fixed = c3.checkbox("3. Exclude declined/failed payments", value=False)

        remaining_share = (
            (0 if dedup_fixed else dedup_share)
            + (0 if currency_fixed else currency_share)
            + (0 if declined_fixed else declined_share)
        )
        live_total = engineered_total + gap * remaining_share
        live_distortion_pct = combined_pct * remaining_share

        m1, m2, m3 = st.columns(3)
        m1.metric("Reported total deposits (live)", f"£{live_total:,.0f}")
        m2.metric("Correct total deposits", f"£{engineered_total:,.0f}")
        m3.metric(
            "Distortion right now",
            f"{live_distortion_pct:+.1f}%",
            delta="Correct" if live_distortion_pct == 0 else f"{live_distortion_pct:.1f}% too high",
            delta_color="normal" if live_distortion_pct == 0 else "inverse",
        )
        if live_distortion_pct == 0:
            st.success("All three fixes applied: the reported number now matches the actual figure exactly.")
        else:
            st.caption(f"Untouched raw read: £{naive_total:,.0f}. Tick boxes above to fix issues one at a time.")

        with st.expander("See the SQL for cards 1–3"):
            st.markdown("**1. Deduplicate ingestion retries**")
            st.code(
                "-- Broken: sums every row exactly as it arrived, including at-least-once retries\n"
                "select sum(amount) from raw.payments where payment_type = 'deposit';",
                language="sql",
            )
            st.code(
                "-- Fixed (real query, dbt/models/staging/stg_payments.sql)\n"
                "with deduplicated as (\n"
                "    select *, row_number() over (partition by payment_id order by ts) as rn\n"
                "    from raw.payments\n"
                ")\n"
                "select sum(amount) from deduplicated where rn = 1 and payment_type = 'deposit';",
                language="sql",
            )
            st.markdown("**2. Normalise currencies to GBP**")
            st.code(
                "-- Broken: EUR, USD, GBP, CAD, SEK all summed as if they were equal\n"
                "select sum(amount) from raw.payments where payment_type = 'deposit';",
                language="sql",
            )
            st.code(
                "-- Fixed (real query, dbt/models/staging/stg_payments.sql)\n"
                "select sum(amount * fx.rate_to_gbp)\n"
                "from raw.payments p\n"
                "join fx_rates_to_gbp fx on upper(p.currency) = fx.currency\n"
                "where p.payment_type = 'deposit';",
                language="sql",
            )
            st.markdown("**3. Exclude declined/failed payments**")
            st.code(
                "-- Broken: counts a card that was declined as if the deposit went through\n"
                "select sum(amount_gbp) from stg_payments where payment_type = 'deposit';",
                language="sql",
            )
            st.code(
                "-- Fixed (real query, dbt/models/intermediate/int_player_daily_activity_clean.sql)\n"
                "select sum(amount_gbp) from stg_payments\n"
                "where payment_type = 'deposit' and status = 'completed';",
                language="sql",
            )
            st.caption(
                f"In this run: dedup alone = {dedup_pct:.1f}pp of distortion, currency mixing = {currency_pct:.1f}pp, "
                f"declined payments = {declined_pct:.3f}pp (small here; a real operator processes this at far "
                "higher volume, where it compounds)."
            )

        st.divider()
        st.markdown("#### Live engagement calculator")
        bs = results.get("bot_session_contamination", {})
        if bs:
            bot_fixed = st.checkbox("4. Exclude bot-inflated sessions from wagering totals", value=False)
            naive_stake = bs["naive_total_stake_incl_bots_gbp"]
            engineered_stake = bs["engineered_total_stake_excl_bots_gbp"]
            live_stake = engineered_stake if bot_fixed else naive_stake
            stake_pct = (naive_stake - engineered_stake) / engineered_stake * 100 if engineered_stake else 0
            n1, n2 = st.columns(2)
            n1.metric("Reported total wagered (live)", f"£{live_stake:,.0f}")
            n2.metric(
                "Bot-driven distortion right now",
                f"{0 if bot_fixed else stake_pct:+.3f}%",
                help=f"{bs['bot_session_count']} bot-flagged sessions out of the full session pool.",
            )
            with st.expander("See the SQL for card 4"):
                st.code(
                    "-- Broken: every session's stake counts towards player engagement, including bots\n"
                    "select sum(stake_amount) from raw.game_rounds;",
                    language="sql",
                )
                st.code(
                    "select sum(gr.stake_amount)\n"
                    "from raw.game_rounds gr\n"
                    "left join bot_flagged_sessions bs on bs.session_id = gr.session_id\n"
                    "where bs.session_id is null;",
                    language="sql",
                )
                st.caption(
                    f"Only {bs['bot_session_count']} sessions in this run were bot-flagged, so the £ effect is "
                    "small here, but a live botnet runs continuously, not once."
                )

        st.divider()
        st.markdown("#### Live labelling calculator")
        sx = results["self_exclusion_contamination"]
        label_fixed = st.checkbox("5. Correctly separate self-excluded players from ordinary churn", value=False)
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
        with st.expander("See the SQL for card 5"):
            st.code(
                "-- Broken: 'last activity long ago' looks identical for churn and self-exclusion\n"
                "select player_id, (last_active_date <= as_of_date) as is_churned\n"
                "from player_activity;",
                language="sql",
            )
            st.code(
                "-- Fixed (real pattern, dbt/models/marts/fct_churn_labels.sql)\n"
                "select p.player_id,\n"
                "       (aa.player_id is null) as is_churned,\n"
                "       (s.self_exclusion_status = 'self_excluded') as is_self_excluded_as_of\n"
                "from players p\n"
                "left join activity_after aa using (player_id)\n"
                "left join player_status_as_of s using (player_id);  -- flagged and held out, not merged in",
                language="sql",
            )

        st.divider()
        st.markdown("#### Point-in-time correctness")
        sp = results.get("scd_point_in_time_lookup", {})
        if sp:
            scd_fixed = st.checkbox("6. Use point-in-time SCD lookups, not 'current attribute' joins", value=False)
            total = sp["total_lookups"]
            mismatches = sp["mismatches_using_current_attributes_instead_of_scd"]
            pct = mismatches / total * 100 if total else 0
            if scd_fixed:
                st.metric("Historical lookups using the wrong attribute value", f"0 / {total}")
                st.success("Fixed: every lookup uses the value that was actually true at the time.")
            else:
                st.metric("Historical lookups using the wrong attribute value", f"{mismatches} / {total} ({pct:.1f}%)")
                st.warning(
                    "A model or investigation using 'current' attributes silently uses information that "
                    "didn't exist yet, or misses a state that has since changed. Full worked example in the "
                    "Slowly Changing Dimensions tab."
                )

        st.divider()
        st.markdown("#### Avoid join fan-out")
        fd = results.get("join_fanout_demo", {})
        if fd:
            fanout_fixed = st.checkbox("7. Aggregate before joining, not after", value=False)
            correct_rows = fd["players"]
            naive_rows = fd["naive_one_to_many_join_rows"]
            if fanout_fixed:
                st.metric("Rows produced", f"{correct_rows:,}", help="One row per player, as intended.")
                st.success("Fixed: deposits and stakes are pre-aggregated to one row per player before joining.")
            else:
                st.metric(
                    "Rows produced",
                    f"{naive_rows:,}",
                    delta=f"{naive_rows / correct_rows:,.0f}× more rows than players",
                    delta_color="inverse",
                    help=f"{fd['payments']:,} payments joined one-to-many against {fd['game_rounds']:,} game "
                    "rounds, per player, with no aggregation first.",
                )
                st.warning(
                    "Every payment row gets duplicated once per game round for that player. Any SUM() run "
                    "on top of this join is now wrong by an enormous, silent multiple."
                )
            with st.expander("See the SQL for card 7"):
                st.code(
                    "-- Broken: one-to-many joined against one-to-many, no aggregation first\n"
                    "select p.player_id, sum(pay.amount) as deposits, sum(gr.stake_amount) as stake\n"
                    "from players p\n"
                    "join payments pay on pay.player_id = p.player_id\n"
                    "join game_rounds gr on gr.player_id = p.player_id  -- cartesian product per player\n"
                    "group by p.player_id;",
                    language="sql",
                )
                st.code(
                    "-- Fixed: aggregate each fact table to one row per player BEFORE joining\n"
                    "with deposits as (select player_id, sum(amount) as deposits from payments group by 1),\n"
                    "     stakes   as (select player_id, sum(stake_amount) as stake from game_rounds group by 1)\n"
                    "select p.player_id, d.deposits, s.stake\n"
                    "from players p\n"
                    "left join deposits d using (player_id)\n"
                    "left join stakes s using (player_id);",
                    language="sql",
                )

        st.divider()
        st.markdown("#### Other classic failure modes (not triggered in this synthetic run, but real)")
        st.caption(
            "This dataset happens to be clean on these three, so there's no live number to move, but each "
            "pattern below is a genuinely common way production pipelines get quietly wrong answers."
        )
        illustrative_cards = [
            (
                "8. Guard against NULL silently zeroing an aggregate",
                "null_fixed",
                "select avg(bonus_amount) as avg_bonus\nfrom bonuses;\n"
                "-- a NULL bonus_amount is silently dropped by avg(), and would silently NULL out\n"
                "-- an entire row total if summed into amount + bonus_amount elsewhere",
                "select avg(coalesce(bonus_amount, 0)) as avg_bonus,\n"
                "       count(*) filter (where bonus_amount is null) as missing_bonus_amounts\n"
                "from bonuses;",
                "No NULLs in `bonus_amount` in this dataset, but a single unexpected NULL is one of the most "
                "common silent-corruption bugs in production SQL.",
            ),
            (
                "9. Normalise timestamps to one timezone before daily aggregation",
                "tz_fixed",
                "select date(event_ts) as day, count(*)\nfrom sessions\ngroup by 1;\n"
                "-- truncating a mixed-timezone timestamp to a date silently shifts some rows a day early or late",
                "select date(event_ts at time zone 'UTC') as day_utc, count(*)\n" "from sessions\ngroup by 1;",
                "Every timestamp in this dataset is already stored consistently in UTC, but mixed-timezone "
                "truncation is a classic source of an off-by-one-day error in daily batch reports.",
            ),
            (
                "10. Store money as NUMERIC/DECIMAL, not FLOAT",
                "float_fixed",
                "create table payments (\n    amount float  -- binary floating point can't represent 0.10 exactly\n);\n"
                "-- sum(amount) over millions of rows accumulates visible rounding drift",
                "create table payments (\n    amount numeric(12,2)  -- exact decimal arithmetic, no drift\n);",
                "This project already uses NUMERIC throughout, so there's no drift to show, but FLOAT/DOUBLE "
                "for money is a real, well-documented bug class once enough rows accumulate.",
            ),
        ]
        cols = st.columns(3)
        for col, (title, key, naive_sql, fixed_sql, note) in zip(cols, illustrative_cards):
            with col:
                st.markdown(f"**{title}**")
                fixed = st.checkbox("Show the fix", value=False, key=key)
                st.code(fixed_sql if fixed else naive_sql, language="sql")
                st.caption(note)

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
            "The ten cards above show the same lesson at the data layer, before a model is even involved: "
            "bad data engineering costs you the answer, not just the model's confidence in it."
        )

with tab_governance:
    st.subheader("Every column is classified, retained, and erasable on purpose")
    beginner_box(
        "What does 'data governance' actually involve, day to day?",
        "Three concrete things: knowing how sensitive each piece of data is (classification), knowing how long "
        "you're legally required or allowed to keep it (retention), and having a real, working process for "
        "when a customer asks you to delete their data even though other laws require you to keep some of it "
        "anyway (erasure vs. retention). All three are below, with a live example you can run yourself.",
    )
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
            class_options = classification["classification"].drop_duplicates().tolist()
            explain_segment(class_options, CLASSIFICATION_INFO, key="pick_classification")
        with col2:
            pii_counts = classification["pii"].map({True: "PII", False: "Not PII"}).value_counts().reset_index()
            pii_counts.columns = ["Category", "Columns"]
            fig = px.pie(pii_counts, names="Category", values="Columns", title="PII vs. non-PII columns", hole=0.4)
            st.plotly_chart(fig, use_container_width=True)
            pii_info = {
                "pii": "Directly or indirectly identifies a real person, e.g. date of birth, IP "
                "address, device fingerprint. Subject to UK GDPR.",
                "not pii": "Aggregate or categorical data with no path back to an individual on its "
                "own, e.g. a game type code or a country-level total.",
            }
            explain_segment(pii_counts["Category"].tolist(), pii_info, key="pick_pii")

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
