"""Coles product search, read from the data embedded in coles.com.au search pages."""

from collections.abc import Mapping
import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

from thermohub.models import Deal, StoreProduct
from thermohub.stores.base import Store, StoreBlocked, StoreUnreadable, deal, unit_price

if TYPE_CHECKING:
    from playwright.async_api import Page

SITE = "https://www.coles.com.au"
IMAGES = "https://productimages.coles.com.au/productimages"
_NEXT_DATA_RE = re.compile(r'<script[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


class Coles(Store):
    id = "coles"
    name = "Coles"
    home_url = SITE

    async def _search(self, session: AsyncSession, term: str) -> list[StoreProduct]:
        # The search page is server-rendered by Next.js and carries its
        # results as JSON, so one request gets the same data the page shows
        # (prices for Coles' default store).
        response = await self._request(
            session, "GET", f"{self.base_url}/search/products", params={"q": term}
        )
        html = response.text
        match = _NEXT_DATA_RE.search(html)
        if match is None:
            if "incapsula" in html.lower() or "_incapsula_resource" in html.lower():
                raise StoreBlocked("Coles' bot protection answered with a challenge page")
            raise StoreUnreadable("Coles' search page has changed and can't be read")
        return parse_results(page_results(match.group(1)))

    async def search_in_browser(self, page: "Page", term: str) -> list[StoreProduct]:
        # A real browser passes the bot check (it runs a script, then reloads
        # into the page), and the results are in the same embedded data.
        await page.goto(
            f"{self.base_url}/search/products?q={quote(term)}",
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        try:
            data = await page.wait_for_selector(
                "script#__NEXT_DATA__", state="attached", timeout=25_000
            )
        except Exception as e:
            title = await page.title()
            raise StoreBlocked(
                f"Coles' search page didn't load in Thermohub's browser either "
                f'(it showed "{title[:80]}")'
            ) from e
        return parse_results(page_results(await data.text_content() or ""))


def page_results(next_data: str) -> list[Any]:
    """The search results in a Coles search page's embedded Next.js data."""
    try:
        return json.loads(next_data)["props"]["pageProps"]["searchResults"]["results"]
    except (ValueError, KeyError, TypeError) as e:
        raise StoreUnreadable("Coles' search page has changed and can't be read") from e


def parse_results(results: list[Any]) -> list[StoreProduct]:
    products = []
    seen = set()
    for raw in results:
        # Skip the banner tiles mixed into results. Sponsored products stay:
        # they're real products, shown in the list on Coles' own site too.
        if not isinstance(raw, Mapping) or raw.get("_type") != "PRODUCT":
            continue
        pricing = raw.get("pricing") or {}
        price = pricing.get("now")
        if not raw.get("availability") or not isinstance(price, int | float) or price <= 0:
            continue
        if raw.get("id") in seen:  # a sponsored copy of a product already listed
            continue
        seen.add(raw.get("id"))
        was = pricing.get("was")
        unit = pricing.get("unit") or {}
        brand, name, size = raw.get("brand") or "", raw.get("name") or "", raw.get("size") or ""
        images = raw.get("imageUris") or []
        products.append(
            StoreProduct(
                store="coles",
                id=str(raw.get("id")),
                name=f"{brand} {name}".strip(),
                size=size or None,
                price=price,
                was_price=was if isinstance(was, int | float) and was > price else None,
                on_special=isinstance(was, int | float) and was > price,
                unit_price=unit_price(
                    unit.get("price"),
                    f"{unit.get('ofMeasureQuantity') or 1}{unit.get('ofMeasureUnits') or ''}",
                    pricing.get("comparable"),
                ),
                deal=_deal(price, pricing),
                image=f"{IMAGES}{images[0]['uri']}" if images and images[0].get("uri") else None,
                url=f"{SITE}/product/{_slug(f'{brand} {name} {size}')}-{raw.get('id')}",
            )
        )
    return products


def _deal(price: float, pricing: Mapping[str, Any]) -> Deal | None:
    # "Pick any 2 for $13" comes as {"minQuantity": 2, "reward": 6.5}: the
    # price of each when buying that many.
    multibuy = pricing.get("multiBuyPromotion") or {}
    quantity, each = multibuy.get("minQuantity"), multibuy.get("reward")
    total = quantity * each if isinstance(quantity, int) and isinstance(each, int | float) else None
    return deal(price, quantity, total, [pricing.get("offerDescription")])


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
