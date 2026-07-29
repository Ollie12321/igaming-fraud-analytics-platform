"""Right-to-erasure (UK GDPR Art. 17) against a warehouse that is also
subject to the Money Laundering Regulations 2017 (reg. 40) five-year
transaction record-keeping requirement.

This is the actual conflict every regulated iGaming operator has to resolve
on every deletion request, and it can't be resolved by deleting everything
or by deleting nothing:

  - Deleting the player's financial/gameplay history outright would breach
    the operator's AML record-keeping obligation and the UK Gambling
    Commission's LCCP requirements.
  - Refusing the request outright would breach UK GDPR: "we might need it
    for AML" is not blanket grounds to ignore an erasure request for data
    that has no AML relevance (an IP address, a date of birth).

The resolution applied here, matching what compliance teams actually do:

  1. Direct/indirect identifiers that carry no AML value on their own
     (IP addresses, exact date of birth) are irreversibly pseudonymised.
  2. The player_id primary key is left in place. On its own it is an opaque
     UUID with no identifying information, and removing it would break
     referential integrity across the financial and fraud-detection tables
     that must legally be retained, so it is treated as the surviving
     pseudonym rather than personal data in its own right.
  3. Financial, gameplay, KYC, and fraud-detection records are retained
     as-is for the regulatory retention window (see docs/data_governance.md)
     because they are the exact records AML law requires the operator to
     keep.
  4. Every erasure run writes an auditable record of what was pseudonymised,
     what was retained, and why, to `governance.erasure_audit_log`.

Usage:
    python -m governance.erasure --player-id <uuid> [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import create_engine, text

from config import get_settings

AML_RETENTION_YEARS = 5

# Text columns that carry no AML/regulatory value on their own: pseudonymised
# (salted, one-way hash) rather than nulled, so fraud analytics that count
# "distinct IPs seen" still work without the IP itself being recoverable.
HASHED_COLUMNS = {
    "sessions": ["ip_address"],
    "login_events": ["ip_address"],
}

# Non-text columns with no AML/regulatory value on their own: nulled outright,
# since a hash string isn't a valid value for a date/timestamp column and
# there's no downstream analytic that needs an exact date of birth once KYC
# checks are complete.
NULLED_COLUMNS = {
    "players": ["date_of_birth"],
}

# Tables retained in full because they hold AML/KYC/fraud records the
# operator has a legal basis (and obligation) to keep.
RETAINED_TABLES = {
    "payments": "AML transaction record-keeping (Money Laundering Regulations 2017, reg. 40)",
    "game_rounds": "gameplay/RTP audit trail (UK Gambling Commission LCCP requirement)",
    "bonuses": "bonus/promotional abuse audit trail",
    "player_attribute_history": "KYC status and self-exclusion history (regulatory record)",
    "fraud_ground_truth": "fraud/AML investigation record",
    "streaming_fraud_flags": "fraud/AML investigation record",
    "devices": "fraud-ring linkage evidence; device_id is an opaque fingerprint, not a direct identifier",
}


@dataclass
class ErasureResult:
    player_id: str
    pseudonymised: dict[str, list[str]] = field(default_factory=dict)
    retained: dict[str, str] = field(default_factory=dict)
    rows_affected: dict[str, int] = field(default_factory=dict)


def _pseudonym(settings, value: str) -> str:
    """Deterministic, salted, one-way pseudonym. Deterministic so re-running
    erasure (or scoring downstream joins on the pseudonym) is idempotent;
    salted + hashed so the original value cannot be recovered.
    """
    digest = hashlib.sha256(f"{settings.erasure_pseudonymisation_salt}:{value}".encode()).hexdigest()
    return f"erased:{digest[:32]}"


def erase_player(player_id: str, dry_run: bool = False) -> ErasureResult:
    settings = get_settings()
    engine = create_engine(settings.warehouse_sqlalchemy_url)
    result = ErasureResult(player_id=player_id)

    with engine.begin() as conn:
        exists = conn.execute(
            text('SELECT 1 FROM "raw"."players" WHERE player_id = :pid'), {"pid": player_id}
        ).fetchone()
        if not exists:
            raise ValueError(f"player_id {player_id!r} not found in raw.players")

        def _count_non_null(table: str, column: str) -> int:
            return (
                conn.execute(
                    text(f'SELECT count(*) FROM "raw"."{table}" WHERE player_id = :pid AND "{column}" IS NOT NULL'),
                    {"pid": player_id},
                ).scalar()
                or 0
            )

        for table, columns in HASHED_COLUMNS.items():
            for column in columns:
                if dry_run:
                    result.rows_affected[f"{table}.{column}"] = _count_non_null(table, column)
                    continue

                # Fetch, pseudonymise in Python (deterministic + salted), write back.
                rows = conn.execute(
                    text(f'SELECT ctid, "{column}" FROM "raw"."{table}" WHERE player_id = :pid'),
                    {"pid": player_id},
                ).fetchall()
                affected = 0
                for row in rows:
                    if row[1] is None:
                        continue
                    new_value = _pseudonym(settings, str(row[1]))
                    conn.execute(
                        text(f'UPDATE "raw"."{table}" SET "{column}" = :new_value WHERE ctid = :ctid'),
                        {"new_value": new_value, "ctid": row[0]},
                    )
                    affected += 1
                result.rows_affected[f"{table}.{column}"] = affected
            result.pseudonymised.setdefault(table, []).extend(columns)

        for table, columns in NULLED_COLUMNS.items():
            for column in columns:
                if dry_run:
                    result.rows_affected[f"{table}.{column}"] = _count_non_null(table, column)
                    continue

                update = conn.execute(
                    text(
                        f'UPDATE "raw"."{table}" SET "{column}" = NULL WHERE player_id = :pid AND "{column}" IS NOT NULL'
                    ),
                    {"pid": player_id},
                )
                result.rows_affected[f"{table}.{column}"] = update.rowcount
            result.pseudonymised.setdefault(table, []).extend(columns)

        result.retained = dict(RETAINED_TABLES)

        if not dry_run:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS governance"))
            conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS governance.erasure_audit_log (
                        erasure_id UUID PRIMARY KEY,
                        player_id_pseudonym TEXT NOT NULL,
                        processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        pseudonymised_fields JSONB NOT NULL,
                        retained_tables JSONB NOT NULL,
                        retention_expires_at DATE NOT NULL
                    )
                    """))
            conn.execute(
                text("""
                    INSERT INTO governance.erasure_audit_log
                        (erasure_id, player_id_pseudonym, pseudonymised_fields, retained_tables, retention_expires_at)
                    VALUES
                        (:erasure_id, :pseudonym, CAST(:pseudonymised_fields AS JSONB), CAST(:retained_tables AS JSONB), :retention_expires_at)
                    """),
                {
                    "erasure_id": str(uuid.uuid4()),
                    "pseudonym": _pseudonym(settings, player_id),
                    "pseudonymised_fields": json.dumps(result.pseudonymised),
                    "retained_tables": json.dumps(result.retained),
                    "retention_expires_at": date.today().replace(year=date.today().year + AML_RETENTION_YEARS),
                },
            )

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--player-id", required=True, help="player_id to process an erasure request for")
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing anything")
    args = parser.parse_args()

    result = erase_player(args.player_id, dry_run=args.dry_run)

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Erasure request for player {args.player_id}")
    print("\nPseudonymised (direct identifiers, no AML basis to keep raw):")
    for table, columns in result.pseudonymised.items():
        for column in columns:
            print(f"  {table}.{column}: {result.rows_affected.get(f'{table}.{column}', 0)} row(s)")
    print("\nRetained in full (regulatory basis):")
    for table, reason in result.retained.items():
        print(f"  {table}: {reason}")


if __name__ == "__main__":
    main()
