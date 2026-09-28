import base64
import io
import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="intake-test-")
os.environ.update({
    "INTAKE_ENVIRONMENT": "test",
    "INTAKE_DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db"),
    "INTAKE_DATABASE_SSLMODE": "disable",
    "INTAKE_AUTH_MODE": "dev",
    "INTAKE_KEY_PROVIDER": "local",
    "INTAKE_STORAGE_BACKEND": "local",
    "INTAKE_LOCAL_STORAGE_DIR": f"{_tmp}/files",
    "INTAKE_PUBLIC_BASE_URL": "https://intake.test",
    "INTAKE_STATIC_DIR": f"{_tmp}/no-static",
})

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.db import Base, get_engine, get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Location, Role, StaffUser  # noqa: E402


@pytest.fixture(autouse=True)
def db_schema():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with get_sessionmaker()() as db:
        main = Location(name="Main Street Dental", address="1 Main St", phone="555-0100")
        north = Location(name="Northside Dental")
        db.add_all([main, north])
        db.flush()
        db.add_all([
            StaffUser(email="admin@office.test", full_name="Ada Admin", role=Role.admin, locations=[main]),
            StaffUser(email="desk@office.test", full_name="Frank Desk", role=Role.front_desk, locations=[main]),
            StaffUser(email="north@office.test", full_name="Nora North", role=Role.front_desk, locations=[north]),
        ])
        db.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def staff_headers(client, email):
    token = client.post("/api/dev/login", json={"email": email}).json()["id_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(client):
    return staff_headers(client, "admin@office.test")


@pytest.fixture
def desk(client):
    return staff_headers(client, "desk@office.test")


@pytest.fixture
def north(client):
    return staff_headers(client, "north@office.test")


def jpeg_bytes(size=(800, 500), color=(30, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def signature_data_url() -> str:
    buf = io.BytesIO()
    img = Image.new("RGBA", (400, 120), (255, 255, 255, 0))
    for x in range(20, 380):
        img.putpixel((x, 60 + (x % 20) - 10), (0, 0, 0, 255))
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
