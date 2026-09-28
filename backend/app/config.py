"""Application settings, loaded from environment variables.

Production safety rails live in ``Settings.validate_for_environment``: the app
refuses to start in production with any dev-only auth, key, or storage backend.
"""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTAKE_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"

    # Postgres connection. In AWS the password is injected from Secrets Manager.
    database_url: str = "postgresql+psycopg://intake:intake@localhost:5432/intake"
    # In AWS the connection is assembled from the RDS-managed secret instead
    # (ECS injects these individually from Secrets Manager).
    db_host: str = ""
    db_port: int = 5432
    db_name: str = "intake"
    db_username: str = ""
    db_password: str = ""
    # Require TLS to the database (RDS also enforces rds.force_ssl=1).
    database_sslmode: str = "prefer"
    database_sslrootcert: str = ""  # e.g. /app/rds-global-bundle.pem for verify-full

    # Public origin used to build patient links, e.g. https://intake.example-dental.com
    public_base_url: str = "http://localhost:5173"

    # Patient link lifetime (requirement: 7 days) and DOB verification lockout.
    link_ttl_days: int = 7
    max_dob_attempts: int = 5
    patient_session_minutes: int = 30

    # --- Staff authentication -------------------------------------------------
    auth_mode: Literal["cognito", "dev"] = "cognito"
    aws_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_domain: str = ""  # e.g. https://my-office.auth.us-east-1.amazoncognito.com
    # Only used when auth_mode == "dev" (never in production).
    dev_auth_secret: str = "dev-only-insecure-staff-secret-change-me"

    # Secret used to sign short-lived patient session tokens. Injected from
    # Secrets Manager in AWS.
    patient_session_secret: str = "dev-only-insecure-patient-session-secret"

    # --- Encryption at rest ---------------------------------------------------
    # "kms": envelope encryption with an AWS KMS customer-managed key.
    # "local": a static AES key from INTAKE_LOCAL_DATA_KEY (dev/test only).
    key_provider: Literal["kms", "local"] = "kms"
    kms_key_id: str = ""
    # base64 of 32 random bytes; dev/test only.
    local_data_key: str = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="

    # --- File storage ---------------------------------------------------------
    storage_backend: Literal["s3", "local"] = "s3"
    s3_bucket: str = ""
    local_storage_dir: str = "./.data/files"
    max_upload_bytes: int = 10 * 1024 * 1024

    # Serve the built React app from this directory if it exists.
    static_dir: str = "./static"

    @model_validator(mode="after")
    def validate_for_environment(self) -> "Settings":
        if self.environment == "production":
            problems = []
            if self.auth_mode != "cognito":
                problems.append("INTAKE_AUTH_MODE must be 'cognito' in production")
            if self.key_provider != "kms":
                problems.append("INTAKE_KEY_PROVIDER must be 'kms' in production")
            if self.storage_backend != "s3":
                problems.append("INTAKE_STORAGE_BACKEND must be 's3' in production")
            if not self.public_base_url.startswith("https://"):
                problems.append("INTAKE_PUBLIC_BASE_URL must be https in production")
            if self.patient_session_secret.startswith("dev-only") or len(self.patient_session_secret) < 32:
                problems.append("INTAKE_PATIENT_SESSION_SECRET must be a strong secret in production")
            if self.database_sslmode not in ("require", "verify-ca", "verify-full"):
                problems.append("INTAKE_DATABASE_SSLMODE must require TLS in production")
            for name in ("cognito_user_pool_id", "cognito_client_id", "kms_key_id", "s3_bucket"):
                if not getattr(self, name):
                    problems.append(f"INTAKE_{name.upper()} is required in production")
            if problems:
                raise ValueError("; ".join(problems))
        return self

    @property
    def sqlalchemy_url(self) -> str:
        if self.db_host:
            from sqlalchemy.engine import URL

            return URL.create("postgresql+psycopg", username=self.db_username, password=self.db_password,
                              host=self.db_host, port=self.db_port, database=self.db_name).render_as_string(hide_password=False)
        return self.database_url

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.aws_region}.amazonaws.com/{self.cognito_user_pool_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
