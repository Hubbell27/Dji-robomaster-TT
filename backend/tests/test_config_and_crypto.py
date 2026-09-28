import pytest

from app import crypto
from app.config import Settings


def test_production_rejects_dev_settings():
    with pytest.raises(ValueError) as e:
        Settings(environment="production", auth_mode="dev", key_provider="local", storage_backend="local")
    msg = str(e.value)
    assert "AUTH_MODE" in msg and "KEY_PROVIDER" in msg and "STORAGE_BACKEND" in msg


def test_production_accepts_hardened_settings():
    Settings(environment="production", auth_mode="cognito", key_provider="kms", storage_backend="s3",
             public_base_url="https://intake.example.com", patient_session_secret="x" * 48,
             database_sslmode="verify-full", cognito_user_pool_id="us-east-1_abc", cognito_client_id="abc",
             kms_key_id="arn:aws:kms:us-east-1:1:key/1", s3_bucket="bucket")


def test_envelope_encryption_roundtrip_and_aad_binding():
    blob = crypto.encrypt_json({"a": 1}, "intake:1:form")
    assert crypto.decrypt_json(blob, "intake:1:form") == {"a": 1}
    with pytest.raises(Exception):
        crypto.decrypt_json(blob, "intake:2:form")
