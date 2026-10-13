import json
import os
import time

from aiohttp import web
from aiohttp.test_utils import TestServer
from cookidoo_api import (
    Cookidoo,
    CookidooAuthData,
    CookidooAuthException,
    CookidooLocalizationConfig,
)
from cookidoo_api.well_known import ENDPOINT_RELS
import pytest

from tests.pantry_fixture import PANTRY
from thermohub.config import Settings
from thermohub.cookidoo_client import CookidooClient, NotSignedIn, UnknownRegion, regions

RIGHT_PASSWORD = "right-password"


def make_settings(tmp_path, **overrides) -> Settings:
    return Settings(_env_file=None, THERMOHUB_DATA_DIR=str(tmp_path), **overrides)


class FakeCookidooServer:
    """Plain-HTTP stand-in for the Cookidoo API and its OAuth token endpoint."""

    def __init__(self):
        self.auth_headers = []
        self.reject = 0  # answer this many shopping list requests with 401
        app = web.Application()
        app.router.add_get("/shopping/en-AU", self.pantry)
        app.router.add_get("/oidc", self.oidc)
        app.router.add_post("/token", self.token)
        self.server = TestServer(app)

    def url(self, path: str) -> str:
        return str(self.server.make_url(path))

    async def pantry(self, request):
        self.auth_headers.append(request.headers.get("Authorization"))
        if self.reject:
            self.reject -= 1
            return web.json_response({"error": "invalid_token"}, status=401)
        return web.json_response(PANTRY)

    async def oidc(self, request):
        return web.json_response({"token_endpoint": self.url("/token")})

    async def token(self, request):
        assert (await request.post())["refresh_token"] == "old-refresh"
        return web.json_response(
            {"access_token": "new", "refresh_token": "new-refresh", "expires_in": 600}
        )


@pytest.fixture
async def cookidoo_server():
    fake = FakeCookidooServer()
    await fake.server.start_server()
    yield fake
    await fake.server.close()


@pytest.fixture
def logins(monkeypatch, cookidoo_server):
    """Point Cookidoo at the fake server and fake its password login.

    Returns the emails each login was attempted with.
    """

    async def resolve(*_args):
        return {rel: template for rel, (_service, template) in ENDPOINT_RELS.items()}

    async def localization(country=None, language=None):
        if country == "zz":
            return []
        url = cookidoo_server.url("/foundation/en-AU")
        return [CookidooLocalizationConfig(country_code="au", language="en-AU", url=url)]

    attempts = []

    async def login(self):
        attempts.append(self._cfg.email)
        if self._cfg.password != RIGHT_PASSWORD:
            raise CookidooAuthException("Login failed due to authorization failure")
        self._apply_tokens(
            {"access_token": f"access-{len(attempts)}", "refresh_token": "r", "expires_in": 3600}
        )

    monkeypatch.setattr("cookidoo_api.cookidoo.resolve_endpoint_paths", resolve)
    monkeypatch.setattr("cookidoo_api.cookidoo.OIDC_DISCOVERY_URL", cookidoo_server.url("/oidc"))
    monkeypatch.setattr("thermohub.cookidoo_client.get_localization_options", localization)
    monkeypatch.setattr(Cookidoo, "login", login)
    return attempts


@pytest.fixture
async def make_client(tmp_path, logins):
    clients = []

    async def make(**settings):
        client = CookidooClient(make_settings(tmp_path, **settings))
        await client.start()
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.close()


ENV_DETAILS = {"cookidoo_email": "env@example.com", "cookidoo_password": RIGHT_PASSWORD}


@pytest.mark.anyio
async def test_nothing_set_up(make_client, logins):
    client = await make_client()

    assert client.signed_in is False
    assert client.can_sign_in_automatically is False
    with pytest.raises(NotSignedIn):
        await client.get_pantry()
    assert logins == []


@pytest.mark.anyio
async def test_sign_in_from_the_page(make_client, cookidoo_server, tmp_path):
    client = await make_client()

    await client.sign_in("me@example.com", RIGHT_PASSWORD, "au", "en-AU")

    assert client.signed_in is True
    assert await client.get_pantry() == PANTRY
    assert cookidoo_server.auth_headers == ["Bearer access-1"]
    account = json.loads((tmp_path / "cookidoo_account.json").read_text())
    assert account == {
        "country": "au",
        "language": "en-AU",
        "email": "me@example.com",
        "signed_out": False,
    }
    assert RIGHT_PASSWORD not in (tmp_path / "cookidoo_account.json").read_text()
    assert RIGHT_PASSWORD not in (tmp_path / "cookidoo_token.json").read_text()


@pytest.mark.anyio
async def test_wrong_password(make_client, tmp_path):
    client = await make_client()

    with pytest.raises(CookidooAuthException):
        await client.sign_in("me@example.com", "wrong", "au", "en-AU")

    assert client.signed_in is False
    assert not (tmp_path / "cookidoo_account.json").exists()


@pytest.mark.anyio
async def test_unknown_region(make_client):
    client = await make_client()

    with pytest.raises(UnknownRegion):
        await client.sign_in("me@example.com", RIGHT_PASSWORD, "zz", "xx")


@pytest.mark.anyio
async def test_sign_in_survives_a_restart(make_client, logins):
    first = await make_client()
    await first.sign_in("me@example.com", RIGHT_PASSWORD, "au", "en-AU")

    second = await make_client()

    assert second.signed_in is True
    assert second.account.email == "me@example.com"
    assert await second.get_pantry() == PANTRY
    assert logins == ["me@example.com"]


@pytest.mark.anyio
async def test_sign_out(make_client, tmp_path):
    client = await make_client()
    await client.sign_in("me@example.com", RIGHT_PASSWORD, "au", "en-AU")

    await client.sign_out()

    assert client.signed_in is False
    assert not (tmp_path / "cookidoo_token.json").exists()
    with pytest.raises(NotSignedIn):
        await client.get_pantry()
    restarted = await make_client()
    assert restarted.signed_in is False


@pytest.mark.anyio
async def test_details_from_container_settings_sign_in_automatically(make_client, logins):
    client = await make_client(**ENV_DETAILS)

    assert client.can_sign_in_automatically is True
    assert await client.get_pantry() == PANTRY
    assert logins == ["env@example.com"]
    assert client.account.email == "env@example.com"


@pytest.mark.anyio
async def test_signing_out_stops_automatic_sign_in(make_client, logins):
    client = await make_client(**ENV_DETAILS)
    await client.get_pantry()

    await client.sign_out()

    with pytest.raises(NotSignedIn):
        await client.get_pantry()
    assert logins == ["env@example.com"]
    restarted = await make_client(**ENV_DETAILS)
    assert restarted.can_sign_in_automatically is False


@pytest.mark.anyio
async def test_page_sign_in_with_another_account_wins(make_client, logins):
    client = await make_client(**ENV_DETAILS)

    await client.sign_in("other@example.com", RIGHT_PASSWORD, "au", "en-AU")

    assert client.can_sign_in_automatically is False
    restarted = await make_client(**ENV_DETAILS)
    assert restarted.account.email == "other@example.com"


@pytest.mark.anyio
async def test_rejected_sign_in_asks_to_sign_in_again(make_client, cookidoo_server, tmp_path):
    client = await make_client()
    await client.sign_in("me@example.com", RIGHT_PASSWORD, "au", "en-AU")
    cookidoo_server.reject = 1

    with pytest.raises(NotSignedIn):
        await client.get_pantry()

    assert client.signed_in is False
    assert client.account.email == "me@example.com"
    assert not (tmp_path / "cookidoo_token.json").exists()


@pytest.mark.anyio
async def test_rejected_sign_in_with_container_details_signs_in_again(
    make_client, cookidoo_server, logins
):
    client = await make_client(**ENV_DETAILS)
    await client.get_pantry()
    cookidoo_server.reject = 1

    assert await client.get_pantry() == PANTRY
    assert logins == ["env@example.com", "env@example.com"]


@pytest.mark.anyio
async def test_unknown_saved_region_falls_back_to_australia(make_client, tmp_path):
    (tmp_path / "cookidoo_account.json").write_text(
        json.dumps({"country": "zz", "language": "xx", "email": None, "signed_out": False})
    )

    client = await make_client()

    assert client.account.country == "au"
    assert client.localization.language == "en-AU"


@pytest.mark.anyio
async def test_get_pantry_sends_the_saved_token(make_client, cookidoo_server):
    client = await make_client()
    client._api.apply_auth_data(
        CookidooAuthData(access_token="access", refresh_token="r", expires_at=time.time() + 3600)
    )

    assert await client.get_pantry() == PANTRY
    assert cookidoo_server.auth_headers == ["Bearer access"]


@pytest.mark.anyio
async def test_refreshed_tokens_are_persisted(make_client, cookidoo_server, tmp_path):
    client = await make_client()
    client._api.apply_auth_data(
        CookidooAuthData(access_token="old", refresh_token="old-refresh", expires_at=0)
    )

    assert await client.get_pantry() == PANTRY

    assert cookidoo_server.auth_headers == ["Bearer new"]
    saved = json.loads((tmp_path / "cookidoo_token.json").read_text())
    assert saved["refresh_token"] == "new-refresh"


def test_saved_files_are_private(tmp_path):
    settings = make_settings(tmp_path / "data")
    auth = CookidooAuthData(access_token="a", refresh_token="r", expires_at=123.0)

    CookidooClient(settings)._save_tokens(auth)

    assert os.stat(settings.token_file).st_mode & 0o777 == 0o600
    api = Cookidoo(session=None)  # load_token makes no requests
    api.load_token(settings.token_file)
    assert api.auth_data == auth


@pytest.mark.anyio
async def test_regions_lists_each_site_once():
    found = await regions()

    pairs = [(r.country_code, r.language) for r in found]
    assert ("au", "en-AU") in pairs
    assert len(pairs) == len(set(pairs))


@pytest.fixture
def anyio_backend():
    return "asyncio"
