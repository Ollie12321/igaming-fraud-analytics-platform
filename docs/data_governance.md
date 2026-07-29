# Data Governance

Governance here is implemented, not just described: classification lives in
dbt metadata that ships with the models it describes, and the right-to-erasure
policy below is a working script exercised by CI on every push
(`.github/workflows/ci.yml`, "Governance right-to-erasure smoke test"), not a
document nobody runs against.

All figures, tables, and identifiers referenced below are synthetic (see
[`docs/data_dictionary.md`](data_dictionary.md)). The policies themselves are
written against real UK regulatory obligations an iGaming operator actually
has, because the point of this document is to show the *shape* of a working
governance program, not to invent a fictional one.

## 1. Classification

Every column in `raw.*` and in the dbt marts is tagged with a classification
tier and a PII flag directly in `dbt/models/**/schema.yml`
(`meta.classification`, `meta.pii`), so sensitivity travels with the model
rather than living in a separate spreadsheet that drifts out of date. Run
`dbt docs generate && dbt docs serve` to browse it alongside full column
lineage.

| Tier | Meaning | Examples in this project |
|---|---|---|
| `public` | Safe to show in any external-facing view | `game_type`, aggregated `fct_fraud_summary` recall figures |
| `internal` | Business data, low risk if it leaked internally, no re-identification risk on its own | timestamps, `country`, `acquisition_channel`, `vip_tier` |
| `confidential` | Directly or indirectly identifies a player | `player_id`, `device_id`, `ip_address`, `card_bin`, financial amounts tied to a player |
| `restricted` | Regulatory or safeguarding sensitivity beyond ordinary PII | `kyc_status`, `self_exclusion_status`, `risk_segment`, `date_of_birth` |

Access control follows the tier: `restricted` columns are only exposed
through the compliance/responsible-gambling views, never through the general
BI layer (`streamlit_app/`), which reads only from `marts`/`intermediate`
and never from `raw` (see `streamlit_app/data_access.py`).

## 2. Retention

Retention periods are set per data category against an actual regulatory
basis, not "keep everything forever":

| Category | Retention | Basis |
|---|---|---|
| `financial_aml` (payments, game rounds, bonuses) | 5 years from end of business relationship | Money Laundering Regulations 2017, reg. 40 |
| `kyc_regulatory` (KYC status, risk segment, date of birth) | 5 years from end of business relationship | MLR 2017, reg. 40; UK Gambling Commission LCCP |
| `responsible_gambling` (self-exclusion status/history) | 6 years, cannot be shortened by an erasure request | LCCP social responsibility code provisions |
| `fraud_signal` (device fingerprints, IPs, fraud flags) | 5 years, or until an active investigation closes if longer | Legitimate interest in fraud/AML prevention (UK GDPR Art. 6(1)(f)) |
| `account_lifetime_plus_aml` (player profile, signup data) | Account lifetime + 5 years post-closure | MLR 2017, reg. 40 |

These map directly onto `meta.retention_category` in the schema files, so
"what's the retention period for this column" is always a one-line lookup
rather than a conversation with compliance.

## 3. Right to erasure vs. AML retention

This is the actual conflict a regulated operator has to resolve on every
deletion request, and it can be resolved neither by deleting everything the
player asks for, nor by refusing every request on the grounds that "we might
need it for AML":

- UK GDPR Art. 17 gives the player a right to erasure of their personal
  data.
- Money Laundering Regulations 2017, reg. 40 requires the operator to
  keep transaction and due-diligence records for 5 years, full stop. Erasure
  law does not override this: Art. 17(3)(b) explicitly carves out an
  exception where retention is necessary for compliance with a legal
  obligation.

`governance/erasure.py` implements the resolution used in practice:

1. Pseudonymise, don't delete, the identifiers that carry no AML value on
   their own: IP addresses (salted one-way hash, so "how many distinct IPs
   touched this account" is still answerable for fraud analytics without the
   IP being recoverable) and exact date of birth (nulled outright; nothing
   downstream needs it once KYC is complete).
2. Retain in full every table that the erasure has no legal basis to
   touch: payments, game rounds, bonuses, KYC/self-exclusion history, and
   fraud investigation records. `player_id` itself is left in place as a
   bare pseudonym; it carries no identifying information alone, and
   replacing it everywhere would break referential integrity across records
   that must legally stay linked and auditable.
3. Write an audit record of exactly what was pseudonymised, what was
   retained and why, and the date the AML retention period on the retained
   data expires, to `governance.erasure_audit_log`.

```bash
python -m governance.erasure --player-id <uuid> --dry-run   # report only
python -m governance.erasure --player-id <uuid>              # apply + audit
```

Sample output against the synthetic dataset:

```
Erasure request for player ed4454cb-6848-4f88-b4a4-f26aaf364220

Pseudonymised (direct identifiers, no AML basis to keep raw):
  sessions.ip_address: 11 row(s)
  login_events.ip_address: 11 row(s)
  players.date_of_birth: 1 row(s)

Retained in full (regulatory basis):
  payments: AML transaction record-keeping (Money Laundering Regulations 2017, reg. 40)
  game_rounds: gameplay/RTP audit trail (UK Gambling Commission LCCP requirement)
  bonuses: bonus/promotional abuse audit trail
  player_attribute_history: KYC status and self-exclusion history (regulatory record)
  fraud_ground_truth: fraud/AML investigation record
  streaming_fraud_flags: fraud/AML investigation record
  devices: fraud-ring linkage evidence; device_id is an opaque fingerprint, not a direct identifier
```

## 4. Lineage

`dbt docs generate` produces a browsable, column-level lineage graph across
`raw.*` -> staging -> intermediate -> marts. Combined with the classification
metadata in section 1, this answers both halves of a typical audit question
in one place: "where did this number come from" (lineage) and "how sensitive
is every column that fed into it" (classification), without cross-referencing
a separate register.

```bash
cd dbt && dbt docs generate && dbt docs serve
```

## 5. Access boundaries in this project

- BI layer (`streamlit_app/`): reads only from `marts`/`intermediate`
  schemas, never `raw`, and never a `restricted`-tier raw column directly.
- ML layer (`ml/naive_vs_engineered.py`): reads from both `raw` and the
  marts by design, since the entire point of that module is to demonstrate
  the difference. This is a deliberate, documented exception, not an
  oversight.
- Streaming detector (`streaming/fraud_rules/`): only ever sees the
  minimum fields a given rule needs (e.g. `card_bin`, device/geo identifiers),
  never the full player record, and never persists anything beyond the
  rolling window state required to evaluate the rule.
