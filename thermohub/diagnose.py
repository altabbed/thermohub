"""Checks for when a shop isn't working: what Thermohub sees when it searches
and signs in.

The Settings page runs them for one shop at a time. ``python -m
thermohub.diagnose`` prints them for both; ``--deep`` adds the raw shape of
each shop's data, for working out what changed on their side.
"""

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any

from curl_cffi.requests import AsyncSession
from playwright.async_api import Page

from thermohub.browser import (
    BLOCKED_WORDS,
    SITES,
    Browsers,
    StoreSite,
    find_login_form,
    has_login_form,
    settle,
    sign_in_state,
)
from thermohub.stores import Coles, Store, StoreError, Woolworths
from thermohub.stores.base import IMPERSONATE, first_line

_FIELDS = """() => [...document.querySelectorAll('input, button')]
  .filter(e => e.offsetParent !== null)
  .slice(0, 12)
  .map(e => [
    e.tagName.toLowerCase(), e.type,
    e.name || e.id || e.getAttribute('aria-label') || (e.textContent || '').trim().slice(0, 30),
    e.autocomplete || '',
  ].join(' '))"""


_NET_ERROR = re.compile(r"net::(ERR_[A-Z_]+)")


def _reason(e: BaseException, site_name: str) -> str:
    """The error in a sentence: a page that couldn't be reached says so plainly."""
    text = first_line(e, 300)
    network = _NET_ERROR.search(text)
    if network:
        return f"Couldn't open the {site_name} website from Thermohub ({network.group(1)})"
    return text


@dataclass
class Check:
    name: str
    ok: bool
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)
    screenshot: bytes | None = None


async def search_plainly(store: Store, term: str) -> Check:
    """The ordinary way Thermohub searches: web requests that look like Chrome."""
    name = f"Search {store.name} for “{term}”"
    started = time.monotonic()
    try:
        products = await store.search_plainly(term)
    except StoreError as e:
        return Check(name, False, str(e), {"seconds": round(time.monotonic() - started, 1)})
    finally:
        await store.close()
    return Check(
        name,
        bool(products),
        f"Found {len(products)} products" if products else "The search worked but found nothing",
        {
            "seconds": round(time.monotonic() - started, 1),
            "first": [f"{p.name} · {p.size or ''} · ${p.price:.2f}" for p in products[:5]],
        },
    )


async def search_with_browser(store: Store, page: Page, terms: Sequence[str]) -> Check:
    """The same searches in Thermohub's browser, which runs the shop's bot
    checks: the shop's website opens once, then each search is made from it."""
    name = f"Search {store.name} for “{terms[0]}” in Thermohub's browser"
    facts: dict[str, Any] = {}
    found: dict[str, int] = {}
    products = []
    started = time.monotonic()
    try:
        await store.open_in_browser(page)
        facts["opening_seconds"] = round(time.monotonic() - started, 1)
        facts["search_seconds"] = []
        for term in terms:
            searched = time.monotonic()
            results = await store.search_in_browser(page, term)
            facts["search_seconds"].append(round(time.monotonic() - searched, 1))
            found[term] = len(results)
            products = products or results
    except Exception as e:
        facts.update(await _page_facts(page))
        facts["found"] = found
        return Check(name, False, _reason(e, store.name), facts, await _screenshot(page))
    facts["found"] = found
    facts["first"] = [f"{p.name} · {p.size or ''} · ${p.price:.2f}" for p in products[:5]]
    if not products:
        return Check(name, False, "The search worked but found nothing", facts)
    each = sum(facts["search_seconds"]) / len(facts["search_seconds"])
    summary = (
        f"Found {found[terms[0]]} products. Opening the {store.name} website took "
        f"{facts['opening_seconds']:.1f}s, then each search {each:.1f}s"
    )
    return Check(name, True, summary, facts)


async def sign_in_page(site: StoreSite, page: Page) -> Check:
    """Does the shop's sign-in form show, tapping through "Log in" if needed?"""
    name = f"Open the {site.name} sign-in page"
    try:
        response = await page.goto(site.sign_in_url, wait_until="domcontentloaded", timeout=45_000)
        await settle(page)
        opened_at = page.url
        tapped = await find_login_form(page)
        found = await has_login_form(page)
    except Exception as e:
        return Check(name, False, _reason(e, site.name), await _page_facts(page))
    facts = await _page_facts(page)
    facts["status"] = response.status if response else None
    facts["opened_at"] = opened_at
    facts["tapped"] = tapped
    if facts["blocked"]:
        summary = f"The page says: “{facts['blocked']}”. Bot protection may be stopping it."
        ok = False
    elif found:
        how = f" after tapping {' → '.join(tapped)}" if tapped else ""
        summary, ok = f"The sign-in form showed{how}", True
    else:
        tried = f" (tapped {' → '.join(tapped)})" if tapped else ""
        summary, ok = f"No sign-in form showed{tried}", False
    return Check(name, ok, summary, facts, await _screenshot(page))


async def account_page(site: StoreSite, page: Page) -> Check:
    """Is Thermohub's browser signed in to the shop? Opens the account page."""
    name = f"Check the {site.name} sign-in"
    try:
        await page.goto(site.account_url, wait_until="domcontentloaded", timeout=45_000)
        await settle(page)
        opened_at = page.url
        state = await sign_in_state(page)
    except Exception as e:
        return Check(name, False, _reason(e, site.name), await _page_facts(page))
    facts = await _page_facts(page)
    facts["opened_at"] = opened_at
    facts["signed_in"] = state
    if state is None:
        summary = "Couldn't tell whether you're signed in"
        if facts["blocked"]:
            summary += f": the page says “{facts['blocked']}”"
        return Check(name, False, summary, facts, await _screenshot(page))
    summary = "Signed in" if state else "Not signed in: the shop shows its sign-in form"
    return Check(name, True, summary, facts, await _screenshot(page))


async def check_shop(
    store: Store,
    site: StoreSite,
    browsers: Browsers,
    terms: Sequence[str] = ("milk", "bread", "eggs"),
    report: Callable[[Check], Awaitable[None]] | None = None,
) -> list[Check]:
    """Every check for one shop, in order. ``store`` is a fresh one, so nothing
    is cached. The plain search is for the first of ``terms``; in the browser,
    for each of them, to time a search after the website has opened."""
    checks = []

    async def done(check: Check) -> None:
        checks.append(check)
        if report:
            await report(check)

    await done(await search_plainly(store, terms[0]))
    try:
        async with browsers.page(site.id) as page:
            await done(await search_with_browser(store, page, terms))
        async with browsers.page(site.id) as page:
            await done(await sign_in_page(site, page))
        async with browsers.page(site.id) as page:
            await done(await account_page(site, page))
    except Exception as e:
        await done(Check("Start Thermohub's browser", False, first_line(e, 300)))
    return checks


async def _page_facts(page: Page) -> dict[str, Any]:
    facts: dict[str, Any] = {"url": page.url}
    try:
        facts["title"] = await page.title()
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        found = BLOCKED_WORDS.search(text or "") or BLOCKED_WORDS.search(facts["title"])
        facts["blocked"] = found.group(0) if found else None
        facts["text"] = " ".join((text or "").split())[:300]
        facts["fields"] = await page.evaluate(_FIELDS)
        facts["frames"] = [f.url for f in page.frames[1:] if f.url and f.url != "about:blank"][:6]
    except Exception as e:  # the page went away
        facts.setdefault("blocked", None)
        facts.setdefault("fields", [])
        facts.setdefault("frames", [])
        facts["error"] = first_line(e)
    return facts


async def _screenshot(page: Page) -> bytes | None:
    try:
        return await page.screenshot(type="jpeg", quality=55)
    except Exception:
        return None


# ---- Raw shapes, for the command line -------------------------------------------


async def raw_shapes(store_id: str, terms: list[str]) -> dict[str, Any]:
    """What each shop's search data looks like: keys, counts and samples."""
    base = SITES[store_id].account_url.split("/", 3)
    home = f"{base[0]}//{base[2]}"
    out: dict[str, Any] = {}
    async with AsyncSession(impersonate=IMPERSONATE, timeout=30) as session:
        first = await session.get(f"{home}/")
        out["home"] = {
            "status": first.status_code,
            "bytes": len(first.content),
            "cookies": sorted(session.cookies.keys()),
        }
        for term in terms:
            if store_id == "coles":
                response = await session.get(f"{home}/search/products", params={"q": term})
                out[term] = _coles_shape(response.status_code, response.text)
            else:
                from thermohub.stores.woolworths import _request_body

                response = await session.post(
                    f"{home}/apis/ui/Search/products",
                    json=_request_body(term),
                    headers={"Accept": "application/json, text/plain, */*", "Origin": home},
                )
                out[term] = _woolworths_shape(response.status_code, response.text)
    return out


def _coles_shape(status: int, html: str) -> dict[str, Any]:
    shape: dict[str, Any] = {"status": status, "bytes": len(html)}
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL)
    shape["title"] = title.group(1).strip()[:120] if title else None
    shape["incapsula"] = "incapsula" in html.lower()
    match = re.search(r'<script[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    shape["next_data"] = bool(match)
    if not match:
        shape["text"] = " ".join(re.sub(r"<[^>]+>", " ", html).split())[:300]
        return shape
    data = json.loads(match.group(1))
    props = data.get("props", {}).get("pageProps", {})
    shape["pageProps"] = sorted(props)[:30]
    results = (props.get("searchResults") or {}).get("results") or []
    products = [r for r in results if r.get("_type") == "PRODUCT"]
    shape["results"] = {
        "all": len(results),
        "products": len(products),
        "sponsored": sum(1 for r in products if r.get("adId")),
        "unavailable": sum(1 for r in products if not r.get("availability")),
    }
    shape["product_keys"] = sorted({k for r in products for k in r})
    shape["pricing_keys"] = sorted({k for r in products for k in (r.get("pricing") or {})})
    shape["sample"] = [
        {
            "name": f"{r.get('brand')} {r.get('name')}",
            "size": r.get("size"),
            "ad": bool(r.get("adId")),
            "available": r.get("availability"),
            "pricing": {
                k: (r.get("pricing") or {}).get(k)
                for k in ("now", "was", "comparable", "offerDescription", "multiBuyPromotion")
            },
            "unit": (r.get("pricing") or {}).get("unit"),
        }
        for r in products[:6]
    ]
    return shape


def _woolworths_shape(status: int, text: str) -> dict[str, Any]:
    shape: dict[str, Any] = {"status": status, "bytes": len(text)}
    try:
        data = json.loads(text)
    except ValueError:
        shape["text"] = " ".join(re.sub(r"<[^>]+>", " ", text).split())[:300]
        return shape
    shape["keys"] = sorted(data)[:30]
    tiles = data.get("Products") or []
    products = [p for t in tiles for p in (t.get("Products") or [t])]
    shape["results"] = {"tiles": len(tiles), "products": len(products)}
    shape["product_keys"] = sorted({k for p in products for k in p})
    tags = [
        {
            k: p.get(k)
            for k in ("DisplayName", "Price", "PromotionDescription", "CentreTag", "HeaderTag")
        }
        for p in products
        if p.get("CentreTag") or p.get("PromotionDescription") or p.get("HeaderTag")
    ]
    shape["promotions"] = tags[:4]
    shape["sample"] = [
        {
            k: p.get(k)
            for k in ("DisplayName", "PackageSize", "Price", "CupString", "Unit", "IsAvailable")
        }
        for p in products[:6]
    ]
    return shape


# ---- Command line ---------------------------------------------------------------------


async def _main(args: argparse.Namespace) -> None:
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    browsers = Browsers(Path(tempfile.mkdtemp()), os.environ.get("THERMOHUB_CHROMIUM") or None)
    try:
        for store_id in args.store or ["woolworths", "coles"]:
            store = {"woolworths": Woolworths, "coles": Coles}[store_id]()
            print(f"\n=== {store.name} ===", flush=True)

            async def report(check: Check, store_id: str = store_id) -> None:
                print(f"[{'ok' if check.ok else 'FAIL'}] {check.name}: {check.summary}")
                for key, value in check.facts.items():
                    print(f"      {key}: {value}")
                if shots and check.screenshot:
                    name = re.sub(r"[^a-z0-9]+", "-", check.name.lower()).strip("-")[:60]
                    (shots / f"{store_id}-{name}.jpg").write_bytes(check.screenshot)

            await check_shop(store, SITES[store_id], browsers, args.terms, report)
            if args.deep:
                try:
                    shape = await raw_shapes(store_id, args.terms)
                except Exception as e:
                    shape = {"error": str(e)[:300]}
                print("--- raw data ---")
                print(json.dumps(shape, indent=1, default=str)[:20_000])
    finally:
        await browsers.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", action="append", choices=["woolworths", "coles"])
    parser.add_argument("--terms", nargs="+", default=["milk"])
    parser.add_argument(
        "--deep", action="store_true", help="print the raw shape of each shop's data"
    )
    parser.add_argument("--shots", help="save screenshots in this folder")
    asyncio.run(_main(parser.parse_args()))
