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
from thermohub.cookidoo_client import CookidooClient


def make_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        cookidoo_email="me@example.com",
        cookidoo_password="secret",
        THERMOHUB_DATA_DIR=str(tmp_path),
        **overrides,
    )


class FakeApi:
    def __init__(self, logged_in: bool):
        self.auth_data = object() if logged_in else None
        self.logins = 0

    async def login(self):
        self.logins += 1
        self.auth_data = object()


def client_with(api, tmp_path) -> CookidooClient:
    client = CookidooClient(make_settings(tmp_path))
    client._api = api
    return client


@pytest.mark.anyio
async def test_logs_in_on_first_use(tmp_path):
    api = FakeApi(logged_in=False)

    async def call():
        return "ok"

    assert await client_with(api, tmp_path)._with_login(call) == "ok"
    assert api.logins == 1


@pytest.mark.anyio
async def test_reuses_restored_tokens(tmp_path):
    api = FakeApi(logged_in=True)

    async def call():
        return "ok"

    assert await client_with(api, tmp_path)._with_login(call) == "ok"
    assert api.logins == 0


@pytest.mark.anyio
async def test_logs_in_again_when_tokens_are_rejected(tmp_path):
    api = FakeApi(logged_in=True)
    calls = []

    async def call():
        calls.append(api.logins)
        if api.logins == 0:
            raise CookidooAuthException("refresh token expired")
        return "ok"

    assert await client_with(api, tmp_path)._with_login(call) == "ok"
    assert calls == [0, 1]


@pytest.mark.anyio
async def test_gives_up_after_one_fresh_login(tmp_path):
    api = FakeApi(logged_in=True)

    async def call():
        raise CookidooAuthException("still rejected")

    with pytest.raises(CookidooAuthException):
        await client_with(api, tmp_path)._with_login(call)
    assert api.logins == 1


def test_saved_tokens_are_private_and_loadable(tmp_path):
    settings = make_settings(tmp_path / "data")
    auth = CookidooAuthData(access_token="a", refresh_token="r", expires_at=123.0)

    CookidooClient(settings)._save_tokens(auth)

    assert os.stat(settings.token_file).st_mode & 0o777 == 0o600
    api = Cookidoo(session=None)  # load_token makes no requests
    api.load_token(settings.token_file)
    assert api.auth_data == auth


@pytest.mark.anyio
async def test_start_restores_tokens_and_resolves_au(tmp_path):
    settings = make_settings(tmp_path)
    CookidooClient(settings)._save_tokens(
        CookidooAuthData(access_token="a", refresh_token="r", expires_at=123.0)
    )

    client = CookidooClient(settings)
    await client.start()
    try:
        assert client.localization.url == "https://cookidoo.com.au/foundation/en-AU"
        assert client._api.auth_data.refresh_token == "r"
    finally:
        await client.close()


@pytest.mark.anyio
async def test_unknown_site_fails_fast(tmp_path):
    client = CookidooClient(make_settings(tmp_path, cookidoo_country="zz"))

    with pytest.raises(ValueError, match="No Cookidoo site"):
        await client.start()


class FakeCookidooServer:
    """Plain-HTTP stand-in for the Cookidoo API and its OAuth token endpoint."""

    def __init__(self):
        self.auth_headers = []
        app = web.Application()
        app.router.add_get("/shopping/en-AU", self.pantry)
        app.router.add_get("/oidc", self.oidc)
        app.router.add_post("/token", self.token)
        self.server = TestServer(app)

    def url(self, path: str) -> str:
        return str(self.server.make_url(path))

    async def pantry(self, request):
        self.auth_headers.append(request.headers.get("Authorization"))
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
async def started_client(tmp_path, monkeypatch, cookidoo_server):
    """A CookidooClient pointed at the fake server, endpoint discovery stubbed out."""

    async def resolve(*_args):
        return {rel: template for rel, (_service, template) in ENDPOINT_RELS.items()}

    async def localization(**_kwargs):
        url = cookidoo_server.url("/foundation/en-AU")
        return [CookidooLocalizationConfig(country_code="au", language="en-AU", url=url)]

    monkeypatch.setattr("cookidoo_api.cookidoo.resolve_endpoint_paths", resolve)
    monkeypatch.setattr("cookidoo_api.cookidoo.OIDC_DISCOVERY_URL", cookidoo_server.url("/oidc"))
    monkeypatch.setattr("thermohub.cookidoo_client.get_localization_options", localization)
    client = CookidooClient(make_settings(tmp_path))
    await client.start()
    yield client
    await client.close()


@pytest.mark.anyio
async def test_get_pantry_fetches_the_shopping_list(started_client, cookidoo_server):
    started_client._api.apply_auth_data(
        CookidooAuthData(access_token="access", refresh_token="r", expires_at=time.time() + 3600)
    )

    assert await started_client.get_pantry() == PANTRY
    assert cookidoo_server.auth_headers == ["Bearer access"]


@pytest.mark.anyio
async def test_refreshed_tokens_are_persisted(started_client, cookidoo_server, tmp_path):
    started_client._api.apply_auth_data(
        CookidooAuthData(access_token="old", refresh_token="old-refresh", expires_at=0)
    )

    assert await started_client.get_pantry() == PANTRY

    assert cookidoo_server.auth_headers == ["Bearer new"]
    saved = json.loads((tmp_path / "cookidoo_token.json").read_text())
    assert saved["refresh_token"] == "new-refresh"


@pytest.fixture
def anyio_backend():
    return "asyncio"
