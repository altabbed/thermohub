"""A real browser for signing in to Woolworths and Coles and filling their carts.

Neither store offers a way for other apps to sign in or add to a cart, and
both use bot protection, so Thermohub runs Chromium itself. Each store gets
its own saved browser profile under the data volume, which keeps the
person's sign-in between restarts. Signing in happens in that browser: its
screen is streamed to the Thermohub page and taps and typing are passed
back. Filling a cart opens each product page and presses the store's own
"Add to cart" button, as a person would.
"""

import asyncio
import base64
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, Route, async_playwright
from playwright.async_api import Error as PlaywrightError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoreSite:
    id: str
    name: str
    sign_in_url: str
    account_url: str
    cart_url: str


SITES = {
    "woolworths": StoreSite(
        id="woolworths",
        name="Woolworths",
        sign_in_url="https://www.woolworths.com.au/shop/securelogin",
        account_url="https://www.woolworths.com.au/shop/myaccount",
        cart_url="https://www.woolworths.com.au/shop/cart",
    ),
    "coles": StoreSite(
        id="coles",
        name="Coles",
        # The account page sends signed-out visitors to Coles' sign-in page.
        sign_in_url="https://www.coles.com.au/account",
        account_url="https://www.coles.com.au/account",
        cart_url="https://www.coles.com.au/trolley",
    ),
}

# Pages that only show to signed-out visitors.
_SIGNED_OUT_URL = re.compile(
    r"log-?in|sign-?in|securelogin|/auth|oauth|authori[sz]e", re.IGNORECASE
)


class SignedOut(Exception):
    """The store asked to sign in again."""


class Browsers:
    """One Chromium profile per store, started when needed and closed when idle."""

    def __init__(self, data_dir: Path, executable: str | None = None, idle_seconds: float = 300):
        self._root = data_dir / "browser"
        self._executable = executable
        self._idle_seconds = idle_seconds
        self._playwright: Playwright | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._start_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._idle: dict[str, asyncio.TimerHandle] = {}
        self._pages_open: dict[str, int] = {}

    def lock(self, store_id: str) -> asyncio.Lock:
        """Held while signing in or filling a cart, so the two never share a page."""
        return self._locks.setdefault(store_id, asyncio.Lock())

    async def context(self, store_id: str) -> BrowserContext:
        self._cancel_idle(store_id)
        async with self._start_lock:
            if store_id in self._contexts:
                return self._contexts[store_id]
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            profile = self._root / store_id
            profile.mkdir(parents=True, exist_ok=True)
            context = await self._playwright.chromium.launch_persistent_context(
                str(profile),
                executable_path=self._executable,
                headless=True,
                user_agent=self._user_agent(),
                viewport={"width": 1280, "height": 900},
                locale="en-AU",
                timezone_id="Australia/Sydney",
                # No sandbox: the container has no user namespaces for it.
                # Small /dev/shm in Docker. Don't advertise automation.
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_default_args=["--enable-automation"],
            )
            context.on("close", lambda _: self._contexts.pop(store_id, None))
            self._contexts[store_id] = context
            return context

    @asynccontextmanager
    async def page(self, store_id: str) -> AsyncIterator[Page]:
        """A page in the store's browser for a quick job (a search, a check), closed after.

        Pictures, videos and fonts aren't downloaded: they're slow and not needed.
        """
        self._pages_open[store_id] = self._pages_open.get(store_id, 0) + 1
        try:
            page = await (await self.context(store_id)).new_page()
            try:
                await page.route("**/*", _skip_heavy)
                yield page
            finally:
                try:
                    await page.close()
                except PlaywrightError:
                    pass  # the browser closed under it
        finally:
            self._pages_open[store_id] -= 1
            self.release(store_id)

    def release(self, store_id: str) -> None:
        """Close the store's browser once it has been idle for a while (it uses memory)."""
        self._cancel_idle(store_id)
        if self._pages_open.get(store_id):
            return  # still in use; the last page out schedules it
        loop = asyncio.get_running_loop()
        self._idle[store_id] = loop.call_later(
            self._idle_seconds, lambda: asyncio.ensure_future(self._close_context(store_id))
        )

    def signed_in(self, store_id: str) -> bool:
        path = self._root / f"{store_id}.json"
        try:
            return bool(json.loads(path.read_text(encoding="utf-8")).get("signed_in"))
        except (OSError, ValueError):
            return False

    def set_signed_in(self, store_id: str, signed_in: bool) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / f"{store_id}.json").write_text(
            json.dumps({"signed_in": signed_in, "checked_at": time.time()}), encoding="utf-8"
        )

    async def sign_out(self, store_id: str) -> None:
        """Forget the store's sign-in by throwing its browser profile away."""
        async with self.lock(store_id):
            await self._close_context(store_id)
            shutil.rmtree(self._root / store_id, ignore_errors=True)
            self.set_signed_in(store_id, False)

    async def close(self) -> None:
        for store_id in list(self._contexts):
            await self._close_context(store_id)
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _close_context(self, store_id: str) -> None:
        self._cancel_idle(store_id)
        context = self._contexts.pop(store_id, None)
        if context is not None:
            try:
                await context.close()
            except Exception:  # already gone
                _LOGGER.debug("Closing the %s browser failed", store_id, exc_info=True)

    def _cancel_idle(self, store_id: str) -> None:
        handle = self._idle.pop(store_id, None)
        if handle is not None:
            handle.cancel()

    def _user_agent(self) -> str:
        # Headless Chromium says "HeadlessChrome"; present as the same version of Chrome.
        major = "141"
        executable = self._executable or (
            self._playwright.chromium.executable_path if self._playwright else None
        )
        if executable:
            try:
                output = subprocess.run(
                    [executable, "--version"], capture_output=True, text=True, timeout=10
                ).stdout
                found = re.search(r"(\d+)\.\d+\.\d+", output)
                major = found.group(1) if found else major
            except (OSError, subprocess.SubprocessError):
                pass
        return (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36"
        )


async def _skip_heavy(route: Route) -> None:
    if route.request.resource_type in ("image", "media", "font"):
        await route.abort()
    else:
        await route.continue_()


async def looks_signed_out(page: Page) -> bool:
    if _SIGNED_OUT_URL.search(page.url):
        return True
    return await page.locator("input[type=password]").count() > 0


async def settle(page: Page, idle_ms: int = 8000) -> None:
    """Let redirects and scripts finish: shops' pages keep loading for a while."""
    try:
        await page.wait_for_load_state("networkidle", timeout=idle_ms)
    except PlaywrightError:
        pass
    await page.wait_for_timeout(800)


# A box for a sign-in email, username or password.
_LOGIN_FIELD = (
    "input[type=email]:visible, input[type=password]:visible, "
    "input[autocomplete=username]:visible, input[autocomplete=email]:visible"
)
# What leads to the sign-in form, best first.
_LOGIN_LINKS = [
    re.compile(r"^\s*(log|sign)\s*-?\s*in\b", re.IGNORECASE),
    re.compile(r"\bmy\s*account\b", re.IGNORECASE),
]


async def has_login_form(page: Page) -> bool:
    for frame in page.frames:
        try:
            if await frame.locator(_LOGIN_FIELD).count():
                return True
        except PlaywrightError:
            continue  # a frame that went away
    return False


async def find_login_form(page: Page, steps: int = 3) -> list[str]:
    """From where the page is, tap "Log in" or "My account" until the sign-in form shows.

    Shops move their sign-in around: Woolworths' sign-in address shows its
    home page, with the form behind "My account" and "Log in". Returns the
    words tapped, in order.
    """
    tapped: list[str] = []
    for _ in range(steps):
        if await has_login_form(page):
            break
        target = None
        for pattern in _LOGIN_LINKS:
            for role in ("link", "button"):
                found = page.get_by_role(role, name=pattern)  # type: ignore[arg-type]
                for k in range(min(await found.count(), 6)):
                    if await found.nth(k).is_visible():
                        target = found.nth(k)
                        break
                if target:
                    break
            if target:
                break
        if target is None:
            break
        tapped.append(" ".join((await target.inner_text()).split())[:40] or "(unlabelled)")
        try:
            await target.click(timeout=5000)
        except PlaywrightError:
            break
        await settle(page)
    return tapped


async def check_signed_in(page: Page, site: StoreSite) -> bool:
    for attempt in (1, 2):
        try:
            await page.goto(site.account_url, wait_until="domcontentloaded", timeout=45_000)
            break
        except PlaywrightError as e:
            # A sign-in form submitted a moment ago is still loading; let it land.
            if attempt == 2 or "interrupted by another navigation" not in str(e):
                raise
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except PlaywrightError:
                pass
    await page.wait_for_timeout(1500)  # let sign-in redirects finish
    return not await looks_signed_out(page)


# ---- Live sign-in ------------------------------------------------------------------

# What kind of box has the keyboard focus: "password", "code", "email", "tel",
# "number" or "text"; "frame" when it's inside an embedded page; null for none.
_FOCUSED_FIELD = """() => {
  const e = document.activeElement;
  if (!e) return null;
  if (e.tagName === "IFRAME") return "frame";
  if (e.isContentEditable || e.tagName === "TEXTAREA") return "text";
  if (e.tagName !== "INPUT") return null;
  const notText = ["button", "submit", "checkbox", "radio", "hidden", "image", "reset", "file"];
  if (notText.includes(e.type)) return null;
  if (e.type === "password") return "password";
  if ((e.autocomplete || "").includes("one-time-code")) return "code";
  if (["email", "tel", "number"].includes(e.type)) return e.type;
  return e.inputMode === "numeric" ? "number" : "text";
}"""
_KEYS = {"Backspace", "Enter", "Tab", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"}

Send = Callable[[bytes | dict[str, Any]], Awaitable[None]]


class LiveSignIn:
    """The store's sign-in page, streamed to the person and driven by their taps and typing."""

    def __init__(self, browsers: Browsers, site: StoreSite, send: Send) -> None:
        self._browsers = browsers
        self._site = site
        self._send = send
        self.page: Page | None = None
        self._cdp: Any = None

    async def start(self, width: int, height: int, scale: float = 1.0) -> None:
        width = max(320, min(int(width), 1280))
        height = max(480, min(int(height), 1400))
        context = await self._browsers.context(self._site.id)
        self.page = await context.new_page()
        await self.page.set_viewport_size({"width": width, "height": height})
        self._cdp = await context.new_cdp_session(self.page)
        self._cdp.on("Page.screencastFrame", self._frame)
        scale = max(1.0, min(float(scale), 2.0))
        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 70,
                "maxWidth": int(width * scale),
                "maxHeight": int(height * scale),
            },
        )
        self.page.on("framenavigated", self._navigated)
        await self._open_sign_in()

    async def _open_sign_in(self) -> None:
        page = self._require_page()
        try:
            await page.goto(self._site.sign_in_url, wait_until="domcontentloaded", timeout=45_000)
            await settle(page)
            tapped = await find_login_form(page)
            if tapped:
                _LOGGER.info("%s sign-in: tapped %s to reach the form", self._site.name, tapped)
        except Exception as e:  # the person can still use the page or try again
            _LOGGER.info("Opening the %s sign-in page failed: %s", self._site.name, e)

    def _frame(self, event: dict[str, Any]) -> None:
        asyncio.ensure_future(
            self._cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})
        )
        asyncio.ensure_future(self._send(base64.b64decode(event["data"])))

    def _navigated(self, frame: Any) -> None:
        if self.page is not None and frame == self.page.main_frame:
            asyncio.ensure_future(self._send({"type": "address", "url": frame.url}))

    async def tap(self, x: float, y: float) -> None:
        page = self._require_page()
        await page.mouse.click(x, y)
        await page.wait_for_timeout(150)
        field = await _focused_field(page)
        await self._send({"type": "editable", "value": field is not None, "field": field})

    async def type_text(self, text: str) -> None:
        await self._require_page().keyboard.insert_text(text[:500])

    async def press(self, key: str) -> None:
        if key in _KEYS:
            await self._require_page().keyboard.press(key)

    async def scroll(self, dy: float) -> None:
        await self._require_page().mouse.wheel(0, max(-3000, min(float(dy), 3000)))

    async def back(self) -> None:
        await self._require_page().go_back(wait_until="domcontentloaded", timeout=30_000)

    async def restart(self) -> None:
        await self._open_sign_in()

    async def finish(self) -> bool:
        """Check whether the person is now signed in, remember it, and close the page."""
        page = self._require_page()
        try:
            await self._cdp.send("Page.stopScreencast")
            signed_in = await check_signed_in(page, self._site)
        except Exception as e:  # the store's page misbehaved; count it as not signed in
            _LOGGER.info("Checking the %s sign-in failed: %s", self._site.name, e)
            signed_in = False
        finally:
            await self.close()
        self._browsers.set_signed_in(self._site.id, signed_in)
        return signed_in

    async def close(self) -> None:
        if self.page is not None and not self.page.is_closed():
            await self.page.close()
        self.page = None
        self._browsers.release(self._site.id)

    def _require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("Sign-in has not started")
        return self.page


async def _focused_field(page: Page) -> str | None:
    try:
        field = await page.evaluate(_FOCUSED_FIELD)
        if field != "frame":
            return field
        for frame in page.frames[1:]:
            inner = await frame.evaluate(_FOCUSED_FIELD)
            if inner and inner != "frame":
                return inner
    except Exception:  # the tap started a navigation
        return None
    return "text"


# ---- Filling a cart ---------------------------------------------------------------

_ADD = re.compile(r"add\b.*\b(cart|trolley)|^\s*add\s*$", re.IGNORECASE)
_MORE = re.compile(r"increase|add one|one more|increment|plus", re.IGNORECASE)


@dataclass
class CartLine:
    product_id: str
    name: str
    url: str
    quantity: int = 1
    key: str | None = None
    status: str = "waiting"  # waiting, adding, added, failed, skipped
    message: str | None = None
    screenshot: bytes | None = None


@dataclass
class CartOrder:
    store: str
    lines: list[CartLine]
    state: str = "waiting"  # waiting, running, done, needs_sign_in, failed
    cart_url: str = ""
    message: str | None = None


@dataclass
class CartJob:
    id: str
    orders: list[CartOrder]
    started: float = field(default_factory=time.time)

    @property
    def finished(self) -> bool:
        return all(o.state in ("done", "needs_sign_in", "failed") for o in self.orders)


async def add_to_cart(page: Page, line: CartLine) -> None:
    """Open the product page and press its add button, then step up the quantity."""
    await page.goto(line.url, wait_until="domcontentloaded", timeout=45_000)
    if await looks_signed_out(page):
        raise SignedOut()
    add = page.get_by_role("button", name=_ADD).first
    await add.wait_for(state="visible", timeout=20_000)
    await add.click()
    await page.wait_for_timeout(1200)
    if await looks_signed_out(page):
        raise SignedOut()
    for _ in range(line.quantity - 1):
        more = page.get_by_role("button", name=_MORE).first
        await more.click(timeout=10_000)
        await page.wait_for_timeout(700)


async def fill_carts(
    job: CartJob,
    browsers: Browsers,
    sites: dict[str, StoreSite],
    on_added: Callable[[CartOrder, CartLine], None] | None = None,
) -> None:
    """Add every line of every order to its store's cart, one product page at a time."""
    for order in job.orders:
        site = sites[order.store]
        order.cart_url = site.cart_url
        if not browsers.signed_in(order.store):
            _skip(order, "needs_sign_in")
            continue
        async with browsers.lock(order.store):
            order.state = "running"
            try:
                await _fill_cart(order, site, browsers, on_added)
            except Exception:
                _LOGGER.exception("Filling the %s cart failed", site.name)
                _skip(order, "failed", "Thermohub's browser stopped working. Try again.")
            finally:
                browsers.release(order.store)


async def _fill_cart(
    order: CartOrder,
    site: StoreSite,
    browsers: Browsers,
    on_added: Callable[[CartOrder, CartLine], None] | None,
) -> None:
    page = await (await browsers.context(order.store)).new_page()
    try:
        for line in order.lines:
            line.status = "adding"
            try:
                await add_to_cart(page, line)
            except SignedOut:
                browsers.set_signed_in(order.store, False)
                _skip(order, "needs_sign_in")
                return
            except Exception as e:
                _LOGGER.info("Adding %s to the %s cart failed: %s", line.name, site.name, e)
                line.status = "failed"
                line.message = f"Couldn't find the add button on the {site.name} page."
                try:
                    line.screenshot = await page.screenshot(type="jpeg", quality=55)
                except Exception:
                    line.screenshot = None
            else:
                line.status = "added"
                if on_added:
                    on_added(order, line)
        order.state = "done"
    finally:
        await page.close()


def _skip(order: CartOrder, state: str, message: str | None = None) -> None:
    """End the order early; lines not yet added are skipped."""
    order.state = state
    order.message = message
    for line in order.lines:
        if line.status in ("waiting", "adding"):
            line.status = "skipped"


async def self_test(executable: str | None) -> None:
    """Start Chromium and render a page: `python -m thermohub.browser` in the image."""
    import tempfile

    browsers = Browsers(Path(tempfile.mkdtemp()), executable)
    try:
        page = await (await browsers.context("self-test")).new_page()
        await page.set_content("<button>Add to cart</button>")
        assert await page.get_by_role("button", name=_ADD).count() == 1
        print("Chromium works:", await page.evaluate("navigator.userAgent"))
    finally:
        await browsers.close()


if __name__ == "__main__":
    import os

    asyncio.run(self_test(os.environ.get("THERMOHUB_CHROMIUM") or None))
