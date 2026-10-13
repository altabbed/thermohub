from cookidoo_api import CookidooAuthException, CookidooLocalizationConfig, CookidooRequestException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from tests.pantry_fixture import PANTRY
from tests.test_compare import FakeStore, product
from thermohub.config import Settings, get_settings
from thermohub.cookidoo_client import Account, NotSignedIn, UnknownRegion
from thermohub.main import app, get_cookidoo, get_stores

GOOD_PASSWORD = "right-password"


class FakeCookidoo:
    localization = CookidooLocalizationConfig(
        country_code="au", language="en-AU", url="https://cookidoo.com.au/foundation/en-AU"
    )

    def __init__(self, signed_in=True, error=None, automatic=False, email="me@example.com"):
        self.signed_in = signed_in
        self.error = error
        self.can_sign_in_automatically = automatic
        self.account = Account(country="au", language="en-AU", email=email)

    async def get_pantry(self):
        if self.error:
            raise self.error
        if not self.signed_in and not self.can_sign_in_automatically:
            raise NotSignedIn()
        return PANTRY

    async def sign_in(self, email, password, country, language):
        if country == "zz":
            raise UnknownRegion()
        if password == "offline":
            raise CookidooRequestException("timeout")
        if password != GOOD_PASSWORD:
            raise CookidooAuthException("bad password")
        self.signed_in = True
        self.account = Account(country=country, language=language, email=email)

    async def sign_out(self):
        self.signed_in = False
        self.account = Account(country="au", language="en-AU", signed_out=True)


def fake_stores():
    return [
        FakeStore("woolworths", {"milk": [product("woolworths", "w-milk", 3.10)]}),
        FakeStore("coles", {"milk": [product("coles", "c-milk", 3.00)]}),
    ]


def make_client(cookidoo=None, api_key: str | None = None, stores=None) -> TestClient:
    settings = Settings(_env_file=None, THERMOHUB_API_KEY=api_key)
    cookidoo = cookidoo or FakeCookidoo()
    stores = stores or fake_stores()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_cookidoo] = lambda: cookidoo
    app.dependency_overrides[get_stores] = lambda: stores
    # Not used as a context manager, so the lifespan (real Cookidoo) never runs.
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def connections(client):
    return {c["id"]: c for c in client.get("/api/status").json()["connections"]}


def test_health():
    assert make_client().get("/health").json() == {"status": "ok"}


def test_page_and_its_files_are_served_without_api_key():
    client = make_client(api_key="s3cret")

    page = client.get("/")
    assert page.status_code == 200
    assert "<title>Thermohub</title>" in page.text
    assert page.headers["cache-control"] == "no-cache"
    for path, kind in [("/static/app.js", "javascript"), ("/static/app.css", "text/css")]:
        response = client.get(path)
        assert response.status_code == 200
        assert kind in response.headers["content-type"]


def test_shopping_list():
    body = make_client().get("/api/shopping-list").json()

    names = [item["name"] for item in body["items"]]
    assert "brown onion" in names
    assert "Dishwasher tablets" not in names
    assert len(body["recipes"]) == 3


def test_shopping_list_include_owned():
    body = make_client().get("/api/shopping-list", params={"include_owned": True}).json()

    assert "Dishwasher tablets" in [item["name"] for item in body["items"]]


def test_shopping_list_when_not_connected():
    response = make_client(FakeCookidoo(signed_in=False)).get("/api/shopping-list")

    assert response.status_code == 409
    assert response.json()["detail"].startswith("Connect your Cookidoo account")


@pytest.mark.parametrize(
    ("error", "status"),
    [(CookidooAuthException("bad password"), 409), (CookidooRequestException("timeout"), 502)],
)
def test_shopping_list_cookidoo_errors(error, status):
    response = make_client(FakeCookidoo(error=error)).get("/api/shopping-list")

    assert response.status_code == status
    assert "Cookidoo" in response.json()["detail"]


def test_status_all_connected():
    found = connections(make_client())

    assert found["cookidoo"]["state"] == "connected"
    assert found["cookidoo"]["summary"] == "Connected as me@example.com"
    assert found["woolworths"]["state"] == "connected"
    assert found["woolworths"]["summary"] == "Prices available"
    assert "don't need to sign in" in found["coles"]["note"]


def test_status_nothing_set_up():
    found = connections(make_client(FakeCookidoo(signed_in=False, email=None)))

    assert found["cookidoo"]["state"] == "not_set_up"


def test_status_signed_out_by_cookidoo():
    found = connections(make_client(FakeCookidoo(signed_in=False, email="me@example.com")))

    assert found["cookidoo"]["state"] == "needs_attention"
    assert "me@example.com" in found["cookidoo"]["summary"]


def test_status_problems():
    stores = [
        FakeStore("woolworths", {}),
        FakeStore("coles", {}, fail="Coles blocked the request (HTTP 403)"),
    ]
    client = make_client(FakeCookidoo(error=CookidooRequestException("timeout")), stores=stores)

    found = connections(client)

    assert found["cookidoo"]["state"] == "unavailable"
    assert found["woolworths"]["state"] == "unavailable"
    assert found["coles"]["state"] == "unavailable"
    assert found["coles"]["summary"] == "Thermohub can't reach Coles right now."
    assert found["coles"]["detail"] == "Coles blocked the request (HTTP 403)"


def test_account():
    body = make_client(FakeCookidoo(automatic=True)).get("/api/cookidoo/account").json()

    assert body == {
        "signed_in": True,
        "email": "me@example.com",
        "country": "au",
        "language": "en-AU",
        "automatic": True,
    }


def test_regions():
    regions = make_client().get("/api/cookidoo/regions").json()

    assert {"country": "au", "language": "en-AU", "site": "https://cookidoo.com.au"} in regions


def sign_in(client, password=GOOD_PASSWORD, country="au"):
    return client.post(
        "/api/cookidoo/sign-in",
        json={
            "email": " new@example.com ",
            "password": password,
            "country": country,
            "language": "en-AU",
        },
    )


def test_sign_in():
    cookidoo = FakeCookidoo(signed_in=False, email=None)

    response = sign_in(make_client(cookidoo))

    assert response.status_code == 200
    assert response.json()["signed_in"] is True
    assert response.json()["email"] == "new@example.com"
    assert GOOD_PASSWORD not in response.text


@pytest.mark.parametrize(
    ("password", "country", "status", "message"),
    [
        ("wrong", "au", 400, "didn't accept that email and password"),
        (GOOD_PASSWORD, "zz", 400, "Pick your country"),
        ("offline", "au", 502, "couldn't reach Cookidoo"),
    ],
)
def test_sign_in_problems(password, country, status, message):
    response = sign_in(make_client(FakeCookidoo(signed_in=False)), password, country)

    assert response.status_code == status
    assert message in response.json()["detail"]


def test_sign_out():
    response = make_client().post("/api/cookidoo/sign-out")

    assert response.status_code == 200
    assert response.json()["signed_in"] is False


def test_compare():
    response = make_client().post("/api/compare", json={"items": ["milk"]})

    assert response.status_code == 200
    body = response.json()
    assert body["cheapest"] == "coles"
    assert body["items"][0]["matches"]["woolworths"]["candidates"][0]["price"] == 3.10


def test_compare_needs_items():
    assert make_client().post("/api/compare", json={"items": []}).status_code == 422


def test_api_key_required_when_configured():
    client = make_client(api_key="s3cret")

    assert client.get("/api/shopping-list").status_code == 401
    assert client.get("/api/shopping-list", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/shopping-list", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.post("/api/cookidoo/sign-out").status_code == 401
    assert client.get("/health").status_code == 200


def test_blank_settings_count_as_unset():
    settings = Settings(
        _env_file=None, cookidoo_email="", cookidoo_password="", THERMOHUB_API_KEY=""
    )

    assert settings.cookidoo_email is None
    assert settings.cookidoo_password is None
    assert settings.api_key is None
    assert make_client(api_key="").get("/api/shopping-list").status_code == 200


def test_config_errors_do_not_print_the_password(monkeypatch):
    monkeypatch.setenv("COOKIDOO_PASSWORD", "hunter2")

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, cookidoo_country=["not", "a", "string"])

    assert "hunter2" not in str(error.value)
