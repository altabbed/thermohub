from cookidoo_api import CookidooAuthException, CookidooLocalizationConfig, CookidooRequestException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from tests.pantry_fixture import PANTRY
from thermohub.config import Settings, get_settings
from thermohub.main import app, get_cookidoo


class FakeCookidoo:
    localization = CookidooLocalizationConfig(
        country_code="au", language="en-AU", url="https://cookidoo.com.au/foundation/en-AU"
    )

    def __init__(self, error: Exception | None = None):
        self.error = error

    async def get_pantry(self):
        if self.error:
            raise self.error
        return PANTRY


def make_client(cookidoo=None, api_key: str | None = None) -> TestClient:
    settings = Settings(
        _env_file=None,
        cookidoo_email="me@example.com",
        cookidoo_password="secret",
        THERMOHUB_API_KEY=api_key,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_cookidoo] = lambda: cookidoo or FakeCookidoo()
    # Not used as a context manager, so the lifespan (real Cookidoo login) never runs.
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_health():
    assert make_client().get("/health").json() == {"status": "ok"}


def test_shopping_list():
    response = make_client().get("/api/shopping-list")

    assert response.status_code == 200
    body = response.json()
    names = [item["name"] for item in body["items"]]
    assert "brown onion" in names
    assert "Dishwasher tablets" not in names
    assert len(body["recipes"]) == 3


def test_shopping_list_include_owned():
    body = make_client().get("/api/shopping-list", params={"include_owned": True}).json()

    assert "Dishwasher tablets" in [item["name"] for item in body["items"]]


def test_api_key_required_when_configured():
    client = make_client(api_key="s3cret")

    assert client.get("/api/shopping-list").status_code == 401
    assert client.get("/api/shopping-list", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/shopping-list", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200


def test_empty_api_key_means_no_auth():
    assert make_client(api_key="").get("/api/shopping-list").status_code == 200


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (CookidooAuthException("bad password"), "Cookidoo login failed"),
        (CookidooRequestException("timeout"), "Cookidoo request failed"),
    ],
)
def test_cookidoo_errors_become_502(error, detail):
    response = make_client(FakeCookidoo(error)).get("/api/shopping-list")

    assert response.status_code == 502
    assert response.json()["detail"].startswith(detail)


def test_config_errors_do_not_print_the_password(monkeypatch):
    monkeypatch.delenv("COOKIDOO_EMAIL", raising=False)
    monkeypatch.setenv("COOKIDOO_PASSWORD", "hunter2")

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert "cookidoo_email" in str(error.value)
    assert "hunter2" not in str(error.value)
