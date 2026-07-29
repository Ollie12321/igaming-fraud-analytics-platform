"""Great Expectations quality gate for the raw landing zone.

This runs *before* anything is loaded into the warehouse, i.e. it validates
the data as it actually arrives (schema shape, nulls, domain values, sane
ranges): the same job a real ingestion layer does against upstream OLTP/event
exports before they're trusted with a warehouse load.

This is deliberately a different job from the dbt tests in `dbt/models/**/schema.yml`:

- This module (raw layer, pre-load): "is this batch structurally sane enough
  to load at all?" Fails the pipeline fast, before wasting a warehouse load,
  on things like null primary keys, out-of-domain enum values, or negative
  monetary amounts: the kind of defect that usually means an upstream schema
  change or a broken producer, not a business rule.
- dbt tests (warehouse layer, post-transform): "is the *business logic* now
  correct?" Referential integrity across tables, uniqueness *after*
  deduplication, SCD2 period validity, fraud-recall sanity checks, etc.

One deliberate exception: `payments` and `game_rounds` are allowed to contain
duplicate primary keys at this layer. Real event pipelines (Kinesis/Firehose
retries, at-least-once delivery, replayed DAG runs) produce exactly this kind
of duplication, and the synthetic generator injects a small, known rate of it
on purpose so the staging-layer `QUALIFY ROW_NUMBER()` dedup logic (see
`dbt/models/staging/stg_payments.sql`) has something real to prove itself
against. Hard-failing raw ingestion on duplicate rows would just mean
reimplementing the staging layer twice, so this gate only *reports* the
duplicate rate for those two tables instead of failing on it.

Usage:
    python -m quality.raw_data_checks --raw-dir data/raw
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from great_expectations.dataset import PandasDataset

from datagen import reference_data as ref

VALID_CURRENCIES = sorted({currency for currency, _, _ in ref.COUNTRIES.values()})


@dataclass
class CheckResult:
    table: str
    expectation: str
    success: bool
    detail: str = ""


@dataclass
class QualityReport:
    results: list[CheckResult] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.success]

    @property
    def passed(self) -> bool:
        return len(self.failures) == 0

    def add(self, table: str, expectation_result, expectation_name: str) -> None:
        result = expectation_result.get("result", {})
        detail = ""
        if not expectation_result["success"]:
            unexpected_pct = result.get("unexpected_percent")
            unexpected = result.get("partial_unexpected_list")
            detail = f"unexpected={unexpected_pct}% sample={unexpected}"
        self.results.append(CheckResult(table, expectation_name, expectation_result["success"], detail))

    def print_summary(self) -> None:
        print("\n" + "=" * 72)
        print("RAW DATA QUALITY REPORT (Great Expectations)")
        print("=" * 72)
        by_table: dict[str, list[CheckResult]] = {}
        for r in self.results:
            by_table.setdefault(r.table, []).append(r)

        for table, checks in by_table.items():
            n_pass = sum(1 for c in checks if c.success)
            status = "PASS" if n_pass == len(checks) else "FAIL"
            print(f"\n[{status}] {table} ({n_pass}/{len(checks)} expectations passed)")
            for c in checks:
                mark = "  ok " if c.success else " FAIL"
                line = f"    {mark} {c.expectation}"
                if c.detail:
                    line += f" -> {c.detail}"
                print(line)

        if self.info:
            print("\nInfo (reported, not gated):")
            for line in self.info:
                print(f"    - {line}")

        print("\n" + "-" * 72)
        print(
            f"RESULT: {'PASSED' if self.passed else 'FAILED'} "
            f"({len(self.results) - len(self.failures)}/{len(self.results)} expectations passed)"
        )
        print("-" * 72 + "\n")


def _check(report: QualityReport, table: str, df: pd.DataFrame, checks: list[tuple[str, dict]]) -> None:
    ds = PandasDataset(df)
    for method_name, kwargs in checks:
        method = getattr(ds, method_name)
        result = method(**kwargs)
        col_desc = kwargs.get("column") or f"{kwargs.get('column_A', '')},{kwargs.get('column_B', '')}"
        report.add(table, result, f"{method_name}({col_desc})")


def _check_custom(report: QualityReport, table: str, name: str, success: bool, detail: str = "") -> None:
    report.results.append(CheckResult(table, name, success, "" if success else detail))


def _report_duplicate_rate(report: QualityReport, table: str, df: pd.DataFrame, id_col: str) -> None:
    dupe_rate = 1 - (df[id_col].nunique() / len(df)) if len(df) else 0.0
    report.info.append(
        f"{table}.{id_col} duplicate rate = {dupe_rate:.2%} "
        f"(expected: at-least-once ingestion duplicates, deduped in stg_{table}.sql)"
    )


def run_checks(raw_dir: Path) -> QualityReport:
    report = QualityReport()

    def load(name: str) -> pd.DataFrame | None:
        path = raw_dir / f"{name}.parquet"
        if not path.exists():
            report.info.append(f"{name}.parquet not found in {raw_dir}, skipping")
            return None
        return pd.read_parquet(path)

    if (players := load("players")) is not None:
        _check(
            report,
            "players",
            players,
            [
                ("expect_column_values_to_not_be_null", {"column": "player_id"}),
                ("expect_column_values_to_be_unique", {"column": "player_id"}),
                ("expect_column_values_to_not_be_null", {"column": "signup_ts"}),
                ("expect_column_values_to_be_in_set", {"column": "country", "value_set": list(ref.COUNTRIES.keys())}),
                ("expect_column_values_to_be_in_set", {"column": "currency", "value_set": VALID_CURRENCIES}),
                (
                    "expect_column_values_to_be_in_set",
                    {"column": "acquisition_channel", "value_set": list(ref.ACQUISITION_CHANNELS.keys())},
                ),
                (
                    "expect_column_values_to_be_in_set",
                    {"column": "activity_segment", "value_set": list(ref.ACTIVITY_SEGMENTS.keys())},
                ),
            ],
        )

    if (devices := load("devices")) is not None:
        _check(
            report,
            "devices",
            devices,
            [
                ("expect_column_values_to_not_be_null", {"column": "device_id"}),
                ("expect_column_values_to_be_unique", {"column": "device_id"}),
                ("expect_column_values_to_not_be_null", {"column": "first_seen_ts"}),
            ],
        )
        # `player_id` is legitimately null only for pre-provisioned shared
        # fraud-ring devices (written before the ring's player accounts exist).
        # Any *other* null player_id is a real defect, so this checks the
        # relationship directly rather than tolerating an arbitrary null rate.
        unexplained_nulls = devices[devices["player_id"].isna() & ~devices["is_shared_fraud_ring"]]
        _check_custom(
            report,
            "devices",
            "null_player_id_only_on_shared_fraud_ring_devices",
            len(unexplained_nulls) == 0,
            f"{len(unexplained_nulls)} device(s) with null player_id but is_shared_fraud_ring=False",
        )

    if (attrs := load("player_attribute_history")) is not None:
        _check(
            report,
            "player_attribute_history",
            attrs,
            [
                ("expect_column_values_to_not_be_null", {"column": "player_id"}),
                ("expect_column_values_to_not_be_null", {"column": "effective_ts"}),
                ("expect_column_values_to_be_in_set", {"column": "vip_tier", "value_set": ref.VIP_TIERS}),
                ("expect_column_values_to_be_in_set", {"column": "kyc_status", "value_set": ref.KYC_STATUSES}),
                (
                    "expect_column_values_to_be_in_set",
                    {"column": "self_exclusion_status", "value_set": ref.SELF_EXCLUSION_STATUSES},
                ),
                ("expect_column_values_to_be_in_set", {"column": "risk_segment", "value_set": ref.RISK_SEGMENTS}),
            ],
        )

    if (sessions := load("sessions")) is not None:
        _check(
            report,
            "sessions",
            sessions,
            [
                ("expect_column_values_to_not_be_null", {"column": "session_id"}),
                ("expect_column_values_to_be_unique", {"column": "session_id"}),
                ("expect_column_values_to_not_be_null", {"column": "player_id"}),
                (
                    "expect_column_pair_values_A_to_be_greater_than_B",
                    {"column_A": "end_ts", "column_B": "start_ts", "or_equal": True},
                ),
            ],
        )

    if (logins := load("login_events")) is not None:
        _check(
            report,
            "login_events",
            logins,
            [
                ("expect_column_values_to_not_be_null", {"column": "login_id"}),
                ("expect_column_values_to_be_unique", {"column": "login_id"}),
                ("expect_column_values_to_not_be_null", {"column": "player_id"}),
                ("expect_column_values_to_not_be_null", {"column": "ts"}),
            ],
        )

    if (rounds := load("game_rounds")) is not None:
        _check(
            report,
            "game_rounds",
            rounds,
            [
                ("expect_column_values_to_not_be_null", {"column": "round_id"}),
                ("expect_column_values_to_be_between", {"column": "stake_amount", "min_value": 0, "max_value": None}),
                ("expect_column_values_to_be_between", {"column": "payout_amount", "min_value": 0, "max_value": None}),
                (
                    "expect_column_values_to_be_in_set",
                    {"column": "game_type", "value_set": list(ref.GAME_TYPES.keys())},
                ),
            ],
        )
        _report_duplicate_rate(report, "game_rounds", rounds, "round_id")

    if (payments := load("payments")) is not None:
        _check(
            report,
            "payments",
            payments,
            [
                ("expect_column_values_to_not_be_null", {"column": "payment_id"}),
                (
                    "expect_column_values_to_be_between",
                    {"column": "amount", "min_value": 0, "max_value": None, "strict_min": True},
                ),
                ("expect_column_values_to_be_in_set", {"column": "currency", "value_set": VALID_CURRENCIES}),
                (
                    "expect_column_values_to_be_in_set",
                    {"column": "payment_type", "value_set": ["deposit", "withdrawal"]},
                ),
                (
                    "expect_column_values_to_be_in_set",
                    {"column": "method", "value_set": list(ref.PAYMENT_METHODS.keys())},
                ),
                ("expect_column_values_to_be_in_set", {"column": "status", "value_set": ["completed", "declined"]}),
            ],
        )
        _report_duplicate_rate(report, "payments", payments, "payment_id")

    if (bonuses := load("bonuses")) is not None:
        _check(
            report,
            "bonuses",
            bonuses,
            [
                ("expect_column_values_to_not_be_null", {"column": "bonus_id"}),
                ("expect_column_values_to_be_unique", {"column": "bonus_id"}),
                (
                    "expect_column_values_to_be_between",
                    {"column": "bonus_amount", "min_value": 0, "max_value": None, "strict_min": True},
                ),
                ("expect_column_values_to_be_in_set", {"column": "bonus_type", "value_set": ["welcome", "reload"]}),
            ],
        )

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()

    report = run_checks(args.raw_dir)
    report.print_summary()

    if not report.passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
