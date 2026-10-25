import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import time

from cookidoo_api import CookidooAuthException, CookidooLocalizationConfig, CookidooRequestException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
import uvicorn

from tests.pantry_fixture import PANTRY
from tests.test_compare import FakeStore, product
from thermohub import main
from thermohub.browser import SITES
from thermohub.config import Settings, get_settings
from thermohub.cookidoo_client import Account, NotSignedIn, UnknownRegion
from thermohub.main import (
    app,
    get_browsers,
    get_cart_jobs,
    get_comparisons,
    get_cookidoo,
    get_preferences,
    get_shop_checks,
    get_sites,
    get_stores,
)
from thermohub.smarts import Preferences

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


class FakeBrowsers:
    def __init__(self, signed_in=()):
        self.signed = set(signed_in)
        self.locks = {}

    def lock(self, store_id):
        return self.locks.setdefault(store_id, asyncio.Lock())

    def signed_in(self, store_id):
        return store_id in self.signed

    async def sign_out(self, store_id):
        self.signed.discard(store_id)


def fake_stores():
    return [
        FakeStore("woolworths", {"milk": [product("woolworths", "w-milk", 3.10)]}),
        FakeStore("coles", {"milk": [product("coles", "c-milk", 3.00)]}),
    ]


def make_client(
    cookidoo=None,
    api_key: str | None = None,
    stores=None,
    preferences=None,
    browsers=None,
    jobs=None,
    comparisons=None,
    checks=None,
):
    settings = Settings(_env_file=None, THERMOHUB_API_KEY=api_key)
    cookidoo = cookidoo or FakeCookidoo()
    stores = stores or fake_stores()
    preferences = preferences or Preferences(Path(tempfile.mkdtemp()) / "preferences.json")
    browsers = browsers or FakeBrowsers()
    jobs = {} if jobs is None else jobs
    comparisons = {} if comparisons is None else comparisons
    checks = {} if checks is None else checks
    app.dependency_overrides[get_comparisons] = lambda: comparisons
    app.dependency_overrides[get_shop_checks] = lambda: checks
    app.dependency_overrides[get_browsers] = lambda: browsers
    app.dependency_overrides[get_sites] = lambda: SITES
    app.dependency_overrides[get_cart_jobs] = lambda: jobs
    app.dependency_overrides[get_preferences] = lambda: preferences
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
    assert found["woolworths"]["signed_in"] is False
    assert found["woolworths"]["note"] == "Sign in to send items to your Woolworths cart."


def test_status_signed_in_to_a_store():
    found = connections(make_client(browsers=FakeBrowsers(signed_in=["coles"])))

    assert found["coles"]["signed_in"] is True
    assert found["coles"]["note"] is None
    assert found["woolworths"]["signed_in"] is False
    assert found["cookidoo"]["signed_in"] is None


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


def test_compare_with_quantities_and_remembered_choice(tmp_path):
    preferences = Preferences(tmp_path / "preferences.json")
    client = make_client(preferences=preferences)
    item = {"name": "milk", "key": "ref:milk", "quantities": [{"amount": 3, "unit": ""}]}

    assert (
        client.post(
            "/api/choices",
            json={
                "store": "coles",
                "key": "ref:milk",
                "product_id": "c-milk",
                "name": "Milk",
                "quantity": 3,
            },
        ).status_code
        == 204
    )
    body = client.post("/api/compare", json={"items": [item]}).json()

    coles = body["items"][0]["matches"]["coles"]["candidates"][0]
    assert coles["tags"] == ["usual"]
    assert preferences.usual("coles", "ref:milk") == "c-milk"
    assert preferences.usual_packs("coles", "ref:milk") == 3


class SlowStore(FakeStore):
    async def search(self, term):
        await asyncio.sleep(0.2)
        return await super().search(term)


class BrokenStore(FakeStore):
    async def search(self, term):
        raise RuntimeError("bug")


@asynccontextmanager
async def no_lifespan(_app):
    yield


def wait_for_comparison(client, run_id):
    for _ in range(100):
        body = client.get(f"/api/comparisons/{run_id}").json()
        if body["done"]:
            return body
        time.sleep(0.05)
    raise AssertionError("the comparison never finished")


def test_comparison_runs_in_the_background(monkeypatch):
    # A client used as a context manager keeps one event loop for the
    # background task; the real lifespan (Cookidoo) is swapped out.
    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)
    stores = [
        SlowStore("woolworths", {"milk": [product("woolworths", "w-milk", 3.10)]}),
        SlowStore("coles", {"milk": [product("coles", "c-milk", 3.00)]}),
    ]
    runs = {}
    with make_client(stores=stores, comparisons=runs) as client:
        started = client.post("/api/comparisons", json={"items": ["milk", "bread"]})

        assert started.status_code == 202
        job = started.json()
        assert (job["done"], job["total"], job["checked"]) == (
            False,
            2,
            {"woolworths": 0, "coles": 0},
        )
        assert job["result"] is None
        finished = wait_for_comparison(client, job["id"])

    assert finished["checked"] == {"woolworths": 2, "coles": 2}
    assert finished["result"]["cheapest"] == "coles"
    assert finished["error"] is None


def test_comparison_that_fails(monkeypatch):
    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)
    stores = [BrokenStore("woolworths", {}), FakeStore("coles", {})]
    with make_client(stores=stores) as client:
        job = client.post("/api/comparisons", json={"items": ["milk"]}).json()
        finished = wait_for_comparison(client, job["id"])

    assert finished["result"] is None
    assert "Something went wrong" in finished["error"]


def test_old_comparisons_expire():
    client = make_client()

    response = client.get("/api/comparisons/nope")

    assert response.status_code == 404
    assert "Compare again" in response.json()["detail"]


def test_compare_needs_items():
    assert make_client().post("/api/compare", json={"items": []}).status_code == 422


def test_store_accounts():
    accounts = make_client(browsers=FakeBrowsers(signed_in=["coles"])).get("/api/stores").json()

    assert accounts == [
        {
            "id": "woolworths",
            "name": "Woolworths",
            "signed_in": False,
            "cart_url": "https://www.woolworths.com.au/shop/cart",
        },
        {
            "id": "coles",
            "name": "Coles",
            "signed_in": True,
            "cart_url": "https://www.coles.com.au/trolley",
        },
    ]


def test_store_search():
    stores = [
        FakeStore("woolworths", {"milk": [product("woolworths", "w-milk", 3.10)]}),
        FakeStore("coles", {}, fail="Coles blocked the request (HTTP 403)"),
    ]
    client = make_client(stores=stores)

    found = client.get("/api/stores/woolworths/search", params={"q": "milk"})
    assert [p["id"] for p in found.json()] == ["w-milk"]
    assert client.get("/api/stores/coles/search", params={"q": "milk"}).status_code == 502
    assert client.get("/api/stores/aldi/search", params={"q": "milk"}).status_code == 404
    assert client.get("/api/stores/coles/search").status_code == 422


def test_shop_check(monkeypatch):
    from thermohub.diagnose import Check

    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)

    async def check_shop(store, site, browsers, term, report):
        await report(
            Check("Search Coles for “milk”", False, "Coles blocked the request (HTTP 403)")
        )
        await report(
            Check(
                "Open the Coles sign-in page",
                True,
                "It opened",
                {"url": site.sign_in_url},
                b"\xff\xd8",
            )
        )

    monkeypatch.setattr(main, "check_shop", check_shop)
    checks = {}
    with make_client(checks=checks) as client:
        job = client.post("/api/stores/coles/checks").json()
        for _ in range(100):
            job = client.get(f"/api/stores/coles/checks/{job['id']}").json()
            if job["done"]:
                break
            time.sleep(0.02)

    assert job["done"] is True
    blocked, sign_in = job["checks"]
    assert (blocked["ok"], blocked["summary"]) == (False, "Coles blocked the request (HTTP 403)")
    assert sign_in["facts"] == {"url": "https://www.coles.com.au/account"}
    assert sign_in["screenshot"] == "data:image/jpeg;base64,/9g="


def test_shop_check_unknown():
    client = make_client()

    assert client.post("/api/stores/aldi/checks").status_code == 404
    assert client.get("/api/stores/coles/checks/nope").status_code == 404


def test_store_sign_out():
    browsers = FakeBrowsers(signed_in=["coles"])
    client = make_client(browsers=browsers)

    assert client.post("/api/stores/coles/sign-out").status_code == 204
    assert browsers.signed == set()
    assert client.post("/api/stores/aldi/sign-out").status_code == 404


WOOLWORTHS_MILK = "https://www.woolworths.com.au/shop/productdetails/1/milk"


def cart_request(url=WOOLWORTHS_MILK, key="ref:milk"):
    line = {"product_id": "w-milk", "name": "Milk 2L", "url": url, "quantity": 2, "key": key}
    return {"orders": [{"store": "woolworths", "lines": [line]}]}


@pytest.fixture
def fake_fill(monkeypatch):
    calls = []

    async def fill_carts(job, browsers, sites, on_added):
        calls.append(job)
        for order in job.orders:
            order.cart_url = sites[order.store].cart_url
            for line in order.lines:
                line.status = "added"
                on_added(order, line)
            order.state = "done"

    monkeypatch.setattr(main, "fill_carts", fill_carts)
    return calls


def test_send_to_carts(fake_fill, tmp_path):
    preferences = Preferences(tmp_path / "preferences.json")
    client = make_client(preferences=preferences, browsers=FakeBrowsers(["woolworths"]))

    started = client.post("/api/carts", json=cart_request())

    assert started.status_code == 202
    body = started.json()
    assert body["orders"][0]["name"] == "Woolworths"
    assert body["orders"][0]["lines"] == [
        {
            "product_id": "w-milk",
            "name": "Milk 2L",
            "url": WOOLWORTHS_MILK,
            "quantity": 2,
            "key": "ref:milk",
            "status": "waiting",
            "message": None,
            "screenshot": None,
        }
    ]
    [job] = fake_fill
    assert job.orders[0].lines[0].quantity == 2

    progress = client.get(f"/api/carts/{body['id']}").json()
    assert progress["finished"] is True
    assert progress["orders"][0]["state"] == "done"
    assert progress["orders"][0]["cart_url"] == "https://www.woolworths.com.au/shop/cart"
    assert progress["orders"][0]["lines"][0]["status"] == "added"
    # What went into the cart becomes the usual pick, and how many.
    assert preferences.usual("woolworths", "ref:milk") == "w-milk"
    assert preferences.usual_packs("woolworths", "ref:milk") == 2


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/milk",
        "javascript://www.woolworths.com.au/%0Aalert(1)",
        "http://www.woolworths.com.au/shop/productdetails/1/milk",
    ],
)
def test_send_to_carts_only_opens_the_store_own_pages(fake_fill, url):
    client = make_client()

    response = client.post("/api/carts", json=cart_request(url=url))

    assert response.status_code == 400
    assert "woolworths.com.au" in response.json()["detail"]
    assert fake_fill == []


def test_cart_job_failure_shows_screenshot():
    from thermohub.browser import CartJob, CartLine, CartOrder

    line = CartLine("c1", "Saffron", "https://www.coles.com.au/product/c1", status="failed")
    line.screenshot = b"\xff\xd8jpeg"
    order = CartOrder(store="coles", lines=[line], state="done")
    client = make_client(jobs={"j1": CartJob(id="j1", orders=[order])})

    body = client.get("/api/carts/j1").json()

    assert body["orders"][0]["lines"][0]["screenshot"] == "data:image/jpeg;base64,/9hqcGVn"
    assert client.get("/api/carts/nope").status_code == 404


def test_old_cart_jobs_are_cleared(fake_fill):
    jobs = {}
    client = make_client(jobs=jobs)

    ids = [client.post("/api/carts", json=cart_request()).json()["id"] for _ in range(22)]

    assert len(jobs) == 20
    assert ids[0] not in jobs and ids[-1] in jobs


class FakeLiveSignIn:
    instances = []

    def __init__(self, browsers, site, send):
        self.site = site
        self.send = send
        self.actions = []
        self.closed = False
        FakeLiveSignIn.instances.append(self)

    async def start(self, width, height, scale):
        self.actions.append(("start", width, height, scale))
        await self.send(b"\xff\xd8frame")

    async def tap(self, x, y):
        self.actions.append(("tap", x, y))
        await self.send({"type": "editable", "value": True})

    async def type_text(self, text):
        self.actions.append(("text", text))

    async def press(self, key):
        self.actions.append(("key", key))

    async def finish(self):
        self.actions.append(("finish",))
        return True

    async def close(self):
        self.closed = True


@pytest.fixture
def live(monkeypatch):
    FakeLiveSignIn.instances = []
    monkeypatch.setattr(main, "LiveSignIn", FakeLiveSignIn)
    return FakeLiveSignIn.instances


def test_store_sign_in_websocket(live):
    client = make_client(api_key="s3cret")

    with client.websocket_connect("/api/stores/coles/sign-in") as ws:
        ws.send_json({"type": "hello", "width": 400, "height": 800, "scale": 2, "key": "s3cret"})
        assert ws.receive_bytes() == b"\xff\xd8frame"
        ws.send_json({"type": "tap", "x": 10, "y": 20})
        assert ws.receive_json() == {"type": "editable", "value": True}
        ws.send_json({"type": "tap"})  # malformed: ignored
        ws.send_json({"type": "text", "text": "me@example.com"})
        ws.send_json({"type": "key", "key": "Enter"})
        ws.send_json({"type": "done"})
        assert ws.receive_json() == {"type": "result", "signed_in": True}

    [session] = live
    assert session.site.id == "coles"
    assert session.actions == [
        ("start", 400, 800, 2),
        ("tap", 10.0, 20.0),
        ("text", "me@example.com"),
        ("key", "Enter"),
        ("finish",),
    ]
    assert session.closed


@pytest.mark.parametrize(
    ("path", "key", "message"),
    [
        ("/api/stores/coles/sign-in", "wrong", "access key"),
        ("/api/stores/aldi/sign-in", "s3cret", "doesn't know that store"),
    ],
)
def test_store_sign_in_websocket_refusals(live, path, key, message):
    client = make_client(api_key="s3cret")

    with client.websocket_connect(path) as ws:
        ws.send_json({"type": "hello", "key": key})
        reply = ws.receive_json()

    assert reply["type"] == "error"
    assert message in reply["message"]
    assert live == []


def test_store_sign_in_websocket_refuses_other_websites(live):
    from starlette.websockets import WebSocketDisconnect

    client = make_client()

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/stores/coles/sign-in", headers={"origin": "https://evil.example"}
        ) as ws:
            ws.send_json({"type": "hello"})
    with client.websocket_connect(
        "/api/stores/coles/sign-in", headers={"origin": "http://testserver"}
    ) as ws:
        ws.send_json({"type": "hello"})
        assert ws.receive_bytes() == b"\xff\xd8frame"
        ws.send_json({"type": "cancel"})
    assert len(live) == 1


def test_store_sign_in_websocket_while_busy(live):
    browsers = FakeBrowsers()
    client = make_client(browsers=browsers)

    async def hold():
        await browsers.lock("woolworths").acquire()

    asyncio.run(hold())
    with client.websocket_connect("/api/stores/woolworths/sign-in") as ws:
        ws.send_json({"type": "hello"})
        reply = ws.receive_json()

    assert "busy" in reply["message"]
    assert live == []


def test_uvicorn_can_serve_the_sign_in_websocket():
    # TestClient doesn't go through uvicorn, which needs a WebSocket library installed.
    config = uvicorn.Config(app, ws="auto")
    config.load()

    assert config.ws_protocol_class is not None


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
