from cookidoo_api import CookidooAuthException, CookidooLocalizationConfig, CookidooRequestException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from tests.pantry_fixture import PANTRY
from tests.test_compare import FakeStore, product
from thermohub.config import Settings, get_settings
from thermohub.main import app, get_cookidoo, get_stores


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


def fake_stores():
    return [
        FakeStore("woolworths", {"milk": [product("woolworths", "w-milk", 3.10)]}),
        FakeStore("coles", {"milk": [product("coles", "c-milk", 3.00)]}),
    ]


def make_client(cookidoo=None, api_key: str | None = None, stores=None) -> TestClient:
    settings = Settings(
        _env_file=None,
        cookidoo_email="me@example.com",
        cookidoo_password="secret",
        THERMOHUB_API_KEY=api_key,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_cookidoo] = lambda: cookidoo or FakeCookidoo()
    app.dependency_overrides[get_stores] = lambda: stores or fake_stores()
    # Not used as a context manager, so the lifespan (real Cookidoo login) never runs.
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_health():
    assert make_client().get("/health").json() == {"status": "ok"}


def test_web_ui_is_served_without_api_key():
    response = make_client(api_key="s3cret").get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Thermohub</title>" in response.text


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


def test_status_all_connected():
    body = make_client().get("/api/status").json()

    assert body["cookidoo"] == {"ok": True, "detail": "Signed in as me@example.com"}
    assert [(s["id"], s["prices"]["ok"], s["account"]["ok"]) for s in body["stores"]] == [
        ("woolworths", True, False),
        ("coles", True, False),
    ]


def test_status_reports_failures():
    stores = [
        FakeStore("woolworths", {}),
        FakeStore("coles", {}, fail="Coles blocked the request (HTTP 403)"),
    ]
    client = make_client(FakeCookidoo(CookidooAuthException("bad password")), stores=stores)

    body = client.get("/api/status").json()

    assert body["cookidoo"]["ok"] is False
    assert body["cookidoo"]["detail"].startswith("Cookidoo login failed")
    assert body["stores"][0]["prices"] == {
        "ok": False,
        "detail": "Search worked but found no products",
    }
    assert body["stores"][1]["prices"] == {
        "ok": False,
        "detail": "Coles blocked the request (HTTP 403)",
    }


def test_compare():
    response = make_client().post("/api/compare", json={"items": ["milk"]})

    assert response.status_code == 200
    body = response.json()
    assert body["cheapest"] == "coles"
    assert body["items"][0]["matches"]["woolworths"]["candidates"][0]["price"] == 3.10


def test_compare_needs_items():
    assert make_client().post("/api/compare", json={"items": []}).status_code == 422


def test_compare_requires_api_key_when_configured():
    client = make_client(api_key="s3cret")

    assert client.post("/api/compare", json={"items": ["milk"]}).status_code == 401
    assert client.get("/api/status").status_code == 401
