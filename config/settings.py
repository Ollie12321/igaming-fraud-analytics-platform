import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    aws_region: str
    aws_profile: str | None

    # Streaming (real AWS: cheap, pay-per-use pieces)
    kinesis_stream_name: str
    dynamodb_fraud_state_table: str
    dynamodb_fraud_flags_table: str
    s3_raw_bucket: str
    s3_events_prefix: str

    # Warehouse (local: avoids Redshift/MWAA always-on cost)
    warehouse_host: str
    warehouse_port: int
    warehouse_db: str
    warehouse_user: str
    warehouse_password: str

    # Data generation
    num_players: int
    simulation_days: int
    random_seed: int
    fraud_injection_rate: float

    # Governance
    erasure_pseudonymisation_salt: str

    # Streamlit app: read a versioned data snapshot instead of the live warehouse.
    # Used for the public Community Cloud deployment, which has no access to the
    # local Docker warehouse; local runs default to the live database.
    use_snapshot_data: bool

    @property
    def warehouse_sqlalchemy_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.warehouse_user}:{self.warehouse_password}"
            f"@{self.warehouse_host}:{self.warehouse_port}/{self.warehouse_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings(
        aws_region=os.environ.get("AWS_REGION", "eu-west-2"),
        aws_profile=os.environ.get("AWS_PROFILE"),
        kinesis_stream_name=os.environ.get("KINESIS_STREAM_NAME", "igaming-player-events"),
        dynamodb_fraud_state_table=os.environ.get("DYNAMODB_FRAUD_STATE_TABLE", "igaming-fraud-state"),
        dynamodb_fraud_flags_table=os.environ.get("DYNAMODB_FRAUD_FLAGS_TABLE", "igaming-fraud-flags"),
        s3_raw_bucket=os.environ.get("S3_RAW_BUCKET", "igaming-fraud-analytics-raw"),
        s3_events_prefix=os.environ.get("S3_EVENTS_PREFIX", "events"),
        warehouse_host=os.environ.get("WAREHOUSE_HOST", "localhost"),
        warehouse_port=int(os.environ.get("WAREHOUSE_PORT", "5432")),
        warehouse_db=os.environ.get("WAREHOUSE_DB", "igaming"),
        warehouse_user=os.environ.get("WAREHOUSE_USER", "igaming"),
        warehouse_password=os.environ.get("WAREHOUSE_PASSWORD", "igaming"),
        num_players=int(os.environ.get("NUM_PLAYERS", "10000")),
        simulation_days=int(os.environ.get("SIMULATION_DAYS", "150")),
        random_seed=int(os.environ.get("RANDOM_SEED", "42")),
        fraud_injection_rate=float(os.environ.get("FRAUD_INJECTION_RATE", "0.015")),
        erasure_pseudonymisation_salt=os.environ.get("GOVERNANCE_ERASURE_SALT", "local-dev-salt-change-in-production"),
        use_snapshot_data=os.environ.get("USE_SNAPSHOT_DATA", "false").lower() == "true",
    )
