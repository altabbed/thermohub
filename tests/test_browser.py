"""The store browser, driven against a small fake supermarket website."""

import asyncio
import os
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from tests.store_fixtures import (
    coles_product,
    coles_search_page,
    woolworths_product,
    woolworths_search,
)
from thermohub.browser import (
    Browsers,
    CartJob,
    CartLine,
    CartOrder,
    LiveSignIn,
    StoreSite,
    check_signed_in,
    fill_carts,
)
from thermohub.stores import Coles, StoreBlocked, Woolworths

CHROMIUM = os.environ.get("THERMOHUB_TEST_CHROMIUM") or (
    "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
)

PRODUCT = """<!doctype html><title>{pid}</title><h1>Product {pid}</h1>
<button id="add" aria-label="Add {pid} to cart">Add</button>
<button id="more" aria-label="Increase quantity" hidden>+</button>
<script>
  const post = () => fetch("/cart/add/{pid}", {{method: "POST"}});
  document.getElementById("add").onclick = () => post().then(() => {{
    document.getElementById("add").hidden = true;
    document.getElementById("more").hidden = false;
  }});
  document.getElementById("more").onclick = post;
</script>"""

# Like Woolworths now: the sign-in address shows the home page, and the form
# is behind "My account", then "Log in".
HOME = """<!doctype html><title>Fakemart</title>
<header><input type="search" placeholder="Search products">
<button id="account">My account</button>
<nav id="menu" hidden><a href="/login">Log in</a> <a href="/join">Sign up</a></nav></header>
<script>
  const menu = document.getElementById("menu");
  document.getElementById("account").onclick = () => (menu.hidden = false);
</script>"""

LOGIN = """<!doctype html><title>Sign in</title>
<style>input { display: block; width: 300px; height: 40px }</style>
<form method="post" action="/login">
  <input id="email" name="email" type="email">
  <input id="password" name="password" type="password">
  <button>Sign in</button>
</form>"""


class FakeMart:
    def __init__(self):
        self.cart = {}
        app = web.Application()
        app.router.add_get("/login", self.login_page)
        app.router.add_get("/home", self.home)
        app.router.add_post("/login", self.login)
        app.router.add_get("/account", self.account)
        app.router.add_get("/product/{pid}", self.product)
        app.router.add_post("/cart/add/{pid}", self.add)
        self.server = TestServer(app)

    def url(self, path):
        return str(self.server.make_url(path))

    def site(self):
        return StoreSite(
            id="fakemart",
            name="Fakemart",
            sign_in_url=self.url("/login"),
            account_url=self.url("/account"),
            cart_url=self.url("/cart"),
        )

    async def home(self, request):
        return web.Response(text=HOME, content_type="text/html")

    async def login_page(self, request):
        return web.Response(text=LOGIN, content_type="text/html")

    async def login(self, request):
        form = await request.post()
        if form.get("password") != "pw":
            return web.Response(text=LOGIN, content_type="text/html")
        response = web.HTTPFound("/account")
        response.set_cookie("session", "ok", max_age=86400)
        raise response

    async def account(self, request):
        if request.cookies.get("session") != "ok":
            raise web.HTTPFound("/login")
        return web.Response(text="<h1>Your account</h1>", content_type="text/html")

    async def product(self, request):
        if request.cookies.get("session") != "ok":
            raise web.HTTPFound("/login")
        pid = request.match_info["pid"]
        if pid == "broken":
            return web.Response(text="<h1>Sold out</h1>", content_type="text/html")
        return web.Response(text=PRODUCT.format(pid=pid), content_type="text/html")

    async def add(self, request):
        pid = request.match_info["pid"]
        self.cart[pid] = self.cart.get(pid, 0) + 1
        return web.json_response(self.cart)


@pytest.fixture
async def mart():
    fake = FakeMart()
    await fake.server.start_server()
    yield fake
    await fake.server.close()


@pytest.fixture
async def browsers(tmp_path):
    instance = Browsers(tmp_path, CHROMIUM)
    yield instance
    await instance.close()


async def sign_in_with_cookie(browsers, mart):
    context = await browsers.context("fakemart")
    await context.add_cookies(
        [{"name": "session", "value": "ok", "url": mart.url("/"), "expires": 4102444800}]
    )


async def wait_for(condition, timeout=10):
    for _ in range(int(timeout * 20)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


async def center(page, selector):
    box = await page.locator(selector).bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


@pytest.mark.anyio
async def test_live_sign_in(browsers, mart):
    sent = []

    async def send(message):
        sent.append(message)

    session = LiveSignIn(browsers, mart.site(), send)
    await session.start(400, 700)
    await wait_for(lambda: any(isinstance(m, bytes) for m in sent))
    frame = next(m for m in sent if isinstance(m, bytes))
    assert frame[:2] == b"\xff\xd8"  # a JPEG

    await session.tap(*await center(session.page, "#email"))
    assert sent[-1] == {"type": "editable", "value": True, "field": "email"}
    await session.type_text("me@example.com")
    await session.tap(*await center(session.page, "#password"))
    assert sent[-1] == {"type": "editable", "value": True, "field": "password"}
    await session.type_text("pw")
    await session.tap(5, 600)
    assert sent[-1] == {"type": "editable", "value": False, "field": None}
    await session.tap(*await center(session.page, "#password"))
    await session.press("Enter")
    await session.page.wait_for_url("**/account")

    assert await session.finish() is True
    assert browsers.signed_in("fakemart") is True
    assert any(
        m == {"type": "address", "url": mart.url("/account")} for m in sent if isinstance(m, dict)
    )


@pytest.mark.anyio
async def test_sign_in_taps_through_to_the_form(browsers, mart):
    from dataclasses import replace

    from thermohub.diagnose import sign_in_page

    site = replace(mart.site(), sign_in_url=mart.url("/home"))

    async def send(message):
        pass

    session = LiveSignIn(browsers, site, send)
    await session.start(400, 700)
    assert session.page.url == mart.url("/login")
    await session.close()

    async with browsers.page("fakemart") as page:
        check = await sign_in_page(site, page)
    assert check.ok
    assert check.facts["tapped"] == ["My account", "Log in"]
    assert check.summary == "The sign-in form showed after tapping My account → Log in"


@pytest.mark.anyio
async def test_wrong_password_is_not_signed_in(browsers, mart):
    async def send(message):
        pass

    session = LiveSignIn(browsers, mart.site(), send)
    await session.start(400, 700)
    await session.tap(*await center(session.page, "#password"))
    await session.type_text("nope")
    await session.press("Enter")
    await session.page.wait_for_load_state()

    assert await session.finish() is False
    assert browsers.signed_in("fakemart") is False


@pytest.mark.anyio
async def test_fill_cart(browsers, mart):
    await sign_in_with_cookie(browsers, mart)
    browsers.set_signed_in("fakemart", True)
    added = []
    order = CartOrder(
        store="fakemart",
        lines=[
            CartLine("p1", "Onions", mart.url("/product/p1"), 1, key="ref:onion"),
            CartLine("p2", "Tomatoes", mart.url("/product/p2"), 3),
            CartLine("broken", "Saffron", mart.url("/product/broken"), 1),
        ],
    )
    job = CartJob(id="job", orders=[order])

    await fill_carts(
        job, browsers, {"fakemart": mart.site()}, lambda o, line: added.append(line.name)
    )

    assert mart.cart == {"p1": 1, "p2": 3}
    assert [line.status for line in order.lines] == ["added", "added", "failed"]
    assert order.lines[2].screenshot[:2] == b"\xff\xd8"
    assert order.state == "done"
    assert order.cart_url == mart.url("/cart")
    assert added == ["Onions", "Tomatoes"]
    assert job.finished


@pytest.mark.anyio
async def test_fill_cart_when_signed_out(browsers, mart):
    browsers.set_signed_in("fakemart", True)
    order = CartOrder(
        store="fakemart",
        lines=[
            CartLine("p1", "Onions", mart.url("/product/p1")),
            CartLine("p2", "Tomatoes", mart.url("/product/p2")),
        ],
    )

    await fill_carts(CartJob(id="job", orders=[order]), browsers, {"fakemart": mart.site()})

    assert order.state == "needs_sign_in"
    assert [line.status for line in order.lines] == ["skipped", "skipped"]
    assert browsers.signed_in("fakemart") is False
    assert mart.cart == {}


@pytest.mark.anyio
async def test_fill_cart_never_signed_in_skips_without_a_browser(browsers, mart):
    order = CartOrder(store="fakemart", lines=[CartLine("p1", "Onions", mart.url("/product/p1"))])

    await fill_carts(CartJob(id="job", orders=[order]), browsers, {"fakemart": mart.site()})

    assert order.state == "needs_sign_in"
    assert order.lines[0].status == "skipped"
    assert browsers._contexts == {}


@pytest.mark.anyio
async def test_fill_cart_when_the_browser_will_not_start(tmp_path, mart):
    broken = Browsers(tmp_path, "/nonexistent/chromium")
    broken.set_signed_in("fakemart", True)
    order = CartOrder(store="fakemart", lines=[CartLine("p1", "Onions", mart.url("/product/p1"))])
    job = CartJob(id="job", orders=[order])

    try:
        await fill_carts(job, broken, {"fakemart": mart.site()})
    finally:
        await broken.close()

    assert order.state == "failed"
    assert "browser" in order.message
    assert order.lines[0].status == "skipped"
    assert job.finished


@pytest.mark.anyio
async def test_sign_in_survives_a_restart_and_sign_out_forgets_it(tmp_path, mart):
    first = Browsers(tmp_path, CHROMIUM)
    await sign_in_with_cookie(first, mart)
    await first.close()

    second = Browsers(tmp_path, CHROMIUM)
    try:
        page = await (await second.context("fakemart")).new_page()
        assert await check_signed_in(page, mart.site()) is True
        await second.sign_out("fakemart")
        page = await (await second.context("fakemart")).new_page()
        assert await check_signed_in(page, mart.site()) is False
    finally:
        await second.close()


CHALLENGE = """<html><head><title>Pardon Our Interruption</title></head><body>
<p>Incapsula incident</p>
<script>document.cookie = "incap=ok; path=/"; location.reload();</script></body></html>"""


class BotProtectedShop:
    """A shop whose bot check needs a browser: a cookie that only a script sets."""

    def __init__(self):
        self.plain_searches = 0
        self.browser_searches = 0
        app = web.Application()
        app.router.add_get("/", self.home)
        app.router.add_get("/search/products", self.coles_search)
        app.router.add_post("/apis/ui/Search/products", self.woolworths_search)
        self.server = TestServer(app)

    def url(self):
        return str(self.server.make_url("")).rstrip("/")

    async def home(self, request):
        page = (
            '<html><body>Shop<script>document.cookie = "incap=ok; path=/";</script></body></html>'
        )
        return web.Response(text=page, content_type="text/html")

    async def coles_search(self, request):
        if request.cookies.get("incap") != "ok":
            self.plain_searches += 1
            return web.Response(text=CHALLENGE, content_type="text/html")
        self.browser_searches += 1
        page = coles_search_page(
            coles_product(409499, "Coles", "Bananas", 0.68, size="approx 170g")
        )
        return web.Response(text=page, content_type="text/html")

    async def woolworths_search(self, request):
        if request.cookies.get("incap") != "ok":
            self.plain_searches += 1
            return web.Response(status=403, text="Access Denied")
        self.browser_searches += 1
        body = await request.json()
        return web.json_response(
            woolworths_search(
                woolworths_product(1, f"{body['searchTerm'].title()}", 0.80, size="each")
            )
        )


@pytest.fixture
async def shop():
    fake = BotProtectedShop()
    await fake.server.start_server()
    yield fake
    await fake.server.close()


@pytest.mark.anyio
async def test_coles_search_falls_back_to_the_browser(browsers, shop):
    coles = Coles(base_url=shop.url(), browsers=browsers)
    try:
        [banana] = await coles.search("bananas")
        await coles.search("milk")  # straight to the browser now
    finally:
        await coles.close()

    assert (banana.name, banana.size, banana.price) == ("Coles Bananas", "approx 170g", 0.68)
    # Challenged: the plain search, its retry with a fresh session, and the
    # browser's first visit (its script then sets the cookie and reloads).
    assert shop.plain_searches == 3
    assert shop.browser_searches == 2


@pytest.mark.anyio
async def test_woolworths_search_falls_back_to_the_browser(browsers, shop):
    woolworths = Woolworths(base_url=shop.url(), browsers=browsers)
    try:
        [banana] = await woolworths.search("bananas")
    finally:
        await woolworths.close()

    assert banana.name == "Bananas each"
    assert shop.browser_searches == 1


@pytest.mark.anyio
async def test_without_a_browser_a_block_is_reported(shop):
    coles = Coles(base_url=shop.url())
    try:
        with pytest.raises(StoreBlocked):
            await coles.search("bananas")
    finally:
        await coles.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"
