"""Application settings, loaded from environment variables.

Two supported production deployments (``INTAKE_DEPLOYMENT``):

* ``aws``    — Cognito staff login, KMS envelope keys, S3 file storage.
* ``office`` — runs on one practice PC: local staff login (password + TOTP
               MFA), an auto-generated key file, local encrypted file storage.

Safety rails live in ``Settings.validate_for_environment``: the app refuses to
start in production with dev auth, dev keys, or a mismatched backend.
"""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTAKE_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    deployment: Literal["aws", "office"] = "aws"

    # Postgres connection.
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
    # Trust X-Forwarded-For for client IPs (true behind the AWS ALB or Caddy; the
    # Windows edition serves clients directly and turns this off).
    trust_forwarded_for: bool = True
    # Office time zone; defines "today" on the dashboard.
    office_timezone: str = "America/New_York"

    # Patient link lifetime (requirement: 7 days) and DOB verification lockout.
    link_ttl_days: int = 7
    max_dob_attempts: int = 5
    patient_session_minutes: int = 30

    # --- Staff authentication -------------------------------------------------
    # cognito: AWS managed login. local: accounts in this database (office PC).
    # dev: pick-a-user login for local development only.
    auth_mode: Literal["cognito", "local", "dev"] = "cognito"
    aws_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_domain: str = ""  # e.g. https://my-office.auth.us-east-1.amazoncognito.com
    # Local auth: session length (sliding) and absolute cap per sign-in.
    staff_session_minutes: int = 60
    staff_session_max_hours: int = 12
    max_login_attempts: int = 5
    login_lockout_minutes: int = 15
    dev_auth_secret: str = "dev-only-insecure-staff-secret-change-me"

    # Secrets. With key_provider=file these come from the key file instead.
    patient_session_secret: str = "dev-only-insecure-patient-session-secret"
    staff_session_secret: str = "dev-only-insecure-staff-session-secret"
    # HMAC key for the returning-patient lookup index (base64, 32 bytes).
    index_key: str = "ZGV2LW9ubHktaW5zZWN1cmUtaW5kZXgta2V5LTMyYg=="

    # --- Encryption at rest ---------------------------------------------------
    # kms: AWS KMS customer-managed key. file: key file generated on first start
    # (office PC). local: static key from INTAKE_LOCAL_DATA_KEY (dev/test only).
    key_provider: Literal["kms", "file", "local"] = "kms"
    kms_key_id: str = ""
    key_file: str = "./.data/intake-keys.json"
    local_data_key: str = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="

    # --- File storage ---------------------------------------------------------
    storage_backend: Literal["s3", "local"] = "s3"
    s3_bucket: str = ""
    local_storage_dir: str = "./.data/files"
    max_upload_bytes: int = 10 * 1024 * 1024

    # --- Retention --------------------------------------------------------------
    # Completed submissions are purged this many years after submission (0 = keep
    # forever). Check your state's dental-record retention law before changing.
    retention_years: int = 10
    # Unfinished forms (expired / cancelled / locked) are purged after this many days.
    draft_retention_days: int = 90

    # --- Built-in backups (Windows desktop edition) ------------------------------
    # Folder for nightly backups: local path or a shared drive (\\server\share\...).
    backup_dir: str = ""
    backup_hour: int = 21
    backup_keep_days: int = 30

    # Optional office-edited copy of the form definition (JSON).
    form_definition_path: str = ""

    # Serve the built React app from this directory if it exists.
    static_dir: str = "./static"

    @model_validator(mode="after")
    def validate_for_environment(self) -> "Settings":
        if self.environment != "production":
            return self
        problems = []
        if not self.public_base_url.startswith("https://"):
            problems.append("INTAKE_PUBLIC_BASE_URL must be https in production")
        if self.deployment == "aws":
            expected = {"auth_mode": "cognito", "key_provider": "kms", "storage_backend": "s3"}
            if self.database_sslmode not in ("require", "verify-ca", "verify-full"):
                problems.append("INTAKE_DATABASE_SSLMODE must require TLS in production")
            for name in ("cognito_user_pool_id", "cognito_client_id", "kms_key_id", "s3_bucket"):
                if not getattr(self, name):
                    problems.append(f"INTAKE_{name.upper()} is required in production")
            for name in ("patient_session_secret", "index_key"):
                v = getattr(self, name)
                if v.startswith("dev-only") or v == Settings.model_fields[name].default or len(v) < 32:
                    problems.append(f"INTAKE_{name.upper()} must be a strong secret in production")
        else:
            expected = {"auth_mode": "local", "key_provider": "file", "storage_backend": "local"}
        for name, value in expected.items():
            if getattr(self, name) != value:
                problems.append(f"INTAKE_{name.upper()} must be '{value}' for a {self.deployment} deployment")
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
