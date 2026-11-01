"""The shop sign-in screen as a phone shows it.

The real Thermohub page runs in a touch-emulated Chromium, and its live sign-in
drives the app's own browser against the fake shop. On a phone the keyboard
only opens when a text box is focused by a tap, so what matters is which
element has focus after the tap, and whether the text box can be seen at all.
"""

import asyncio
from contextlib import closing
from dataclasses import replace
import socket
import threading

from playwright.async_api import async_playwright
import pytest
import uvicorn

from tests.test_browser import CHROMIUM, FakeMart, wait_for
from thermohub import browser as browser_module
from thermohub import main
from thermohub.config import get_settings

# The fake shop's sign-in form: an email box, then a password box, one under the other.
EMAIL_AT = (100, 28)
PASSWORD_AT = (100, 68)
NOTHING_AT = (200, 300)

PHONES = [
    pytest.param((390, 844), id="iphone-14"),
    pytest.param((412, 915), id="pixel-8"),
    pytest.param((375, 667), id="iphone-se"),
    pytest.param((360, 640), id="small-android"),
    pytest.param((768, 1024), id="tablet-upright"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def shop():
    fake = FakeMart()
    await fake.server.start_server()
    yield fake
    await fake.server.close()


@pytest.fixture
async def thermohub(shop, tmp_path, monkeypatch):
    """The real app, on a real port, signing in to the fake shop as if it were Woolworths."""
    monkeypatch.setenv("THERMOHUB_DATA_DIR", str(tmp_path))
    if CHROMIUM:
        monkeypatch.setenv("THERMOHUB_CHROMIUM", CHROMIUM)
    get_settings.cache_clear()
    monkeypatch.setitem(
        browser_module.SITES, "woolworths", replace(shop.site(), id="woolworths", name="Woolworths")
    )
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    await wait_for(lambda: server.started, timeout=30)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(20)
    get_settings.cache_clear()


async def open_sign_in(playwright, url, size, touch=True):
    chromium = await playwright.chromium.launch(executable_path=CHROMIUM)
    context = await chromium.new_context(
        viewport={"width": size[0], "height": size[1]},
        is_mobile=touch,
        has_touch=touch,
        device_scale_factor=2 if touch else 1,
    )
    page = await context.new_page()
    await page.goto(f"{url}/#signin/woolworths")
    await page.wait_for_function(
        "() => { const c = document.querySelector('.live-cover'); return c && c.hidden; }",
        timeout=30_000,
    )
    # The first picture is the blank page; wait until the shop's own page has opened.
    await page.wait_for_function(
        "() => document.querySelector('.live-host').textContent.includes('/login')",
        timeout=30_000,
    )
    await page.wait_for_timeout(2000)
    return chromium, page


async def wait_for_login(shop, count=1, seconds=6):
    """Wait for the shop's form to be sent, without failing if it never is."""
    for _ in range(int(seconds * 20)):
        if len(shop.logins) >= count:
            return
        await asyncio.sleep(0.05)


async def tap_page(page, at, touch=True):
    """Tap a point of the shop's page, shown as a picture on the screen."""
    box = await page.locator(".live-screen").bounding_box()
    x, y = box["x"] + at[0], box["y"] + at[1]
    if touch:
        await page.touchscreen.tap(x, y)
    else:
        await page.mouse.click(x, y)
    await page.wait_for_timeout(700)  # the shop page answers which box has the cursor


FOCUS = """() => {
  const e = document.activeElement, t = document.querySelector('.typing-input');
  const b = t.getBoundingClientRect();
  const at = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
  return {
    focused: e === t, kind: t.type, ready: t.parentElement.classList.contains('ready'),
    fullyOnScreen: b.top >= 0 && b.bottom <= innerHeight && b.width > 0,
    uncovered: at === t, top: Math.round(b.top), height: innerHeight,
  };
}"""


@pytest.mark.anyio
@pytest.mark.parametrize("size", PHONES)
async def test_the_text_box_can_be_seen_and_reached_before_any_tap(thermohub, size):
    async with async_playwright() as playwright:
        chromium, page = await open_sign_in(playwright, thermohub, size)
        try:
            state = await page.evaluate(FOCUS)
        finally:
            await chromium.close()

    assert state["fullyOnScreen"], f"the text box is off screen: {state}"
    assert state["uncovered"], f"something covers the text box: {state}"


@pytest.mark.anyio
@pytest.mark.parametrize("size", PHONES)
async def test_tapping_a_box_on_a_phone_brings_up_the_keyboard(thermohub, shop, size):
    async with async_playwright() as playwright:
        chromium, page = await open_sign_in(playwright, thermohub, size)
        try:
            await tap_page(page, EMAIL_AT)
            state = await page.evaluate(FOCUS)

            # What the keyboard then types goes to the shop's box.
            await page.keyboard.type("me@example.com")
            await page.keyboard.press("Enter")
            await wait_for_login(shop)
        finally:
            await chromium.close()

    assert state["focused"], f"the text box has no focus, so no keyboard: {state}"
    assert state["kind"] == "email"
    assert state["fullyOnScreen"] and state["uncovered"], state
    assert [login["email"] for login in shop.logins] == ["me@example.com"]


@pytest.mark.anyio
async def test_the_keyboard_is_asked_for_inside_the_tap_not_after_the_shop_answers(thermohub):
    # A phone only raises its keyboard for a box focused while the tap is still being
    # handled; asking after the shop page has answered is too late. So look at the very end
    # of the tap, before the page could have said anything.
    async with async_playwright() as playwright:
        chromium, page = await open_sign_in(playwright, thermohub, (390, 844))
        try:
            await page.evaluate(
                """() => {
                  const t = document.querySelector('.typing-input');
                  window.focusedAtTheEndOfTheTap = null;
                  document.querySelector('.live-screen').addEventListener('click', () => {
                    window.focusedAtTheEndOfTheTap = document.activeElement === t;
                  });
                }"""
            )
            box = await page.locator(".live-screen").bounding_box()
            await page.touchscreen.tap(box["x"] + EMAIL_AT[0], box["y"] + EMAIL_AT[1])
            at_the_end = await page.evaluate("() => window.focusedAtTheEndOfTheTap")
        finally:
            await chromium.close()

    assert at_the_end is True


@pytest.mark.anyio
async def test_signing_in_on_a_phone_from_start_to_finish(thermohub, shop):
    async with async_playwright() as playwright:
        chromium, page = await open_sign_in(playwright, thermohub, (390, 844))
        try:
            await tap_page(page, EMAIL_AT)
            email_box = await page.evaluate(FOCUS)
            await page.keyboard.type("me@example.com")
            await tap_page(page, PASSWORD_AT)
            password_box = await page.evaluate(FOCUS)
            await page.keyboard.type("pw")
            await page.keyboard.press("Enter")
            await wait_for_login(shop)
            sent = list(shop.logins)

            await page.get_by_role("button", name="I've signed in").tap()
            await page.get_by_text("You're signed in to Woolworths").wait_for(timeout=30_000)
        finally:
            await chromium.close()

    assert email_box["focused"] and email_box["kind"] == "email", email_box
    assert password_box["focused"] and password_box["kind"] == "password", password_box
    assert sent == [{"email": "me@example.com", "password": "pw"}]


@pytest.mark.anyio
async def test_tapping_something_that_is_not_a_box_leaves_the_keyboard_down(thermohub):
    async with async_playwright() as playwright:
        chromium, page = await open_sign_in(playwright, thermohub, (390, 844))
        try:
            await tap_page(page, NOTHING_AT)
            on_nothing = await page.evaluate(FOCUS)

            await tap_page(page, EMAIL_AT)
            on_box = await page.evaluate(FOCUS)

            await tap_page(page, NOTHING_AT)  # tapping away puts the keyboard away again
            after = await page.evaluate(FOCUS)
        finally:
            await chromium.close()

    assert not on_nothing["focused"]
    assert on_box["focused"]
    assert not after["focused"]


@pytest.mark.anyio
async def test_on_a_computer_the_box_still_takes_the_cursor_for_you(thermohub):
    async with async_playwright() as playwright:
        chromium, page = await open_sign_in(playwright, thermohub, (1280, 900), touch=False)
        try:
            await tap_page(page, EMAIL_AT, touch=False)
            state = await page.evaluate(FOCUS)
        finally:
            await chromium.close()

    assert state["focused"], state
