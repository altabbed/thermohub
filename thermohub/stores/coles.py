"""Coles product search, read from the data embedded in coles.com.au search pages."""

from collections.abc import Mapping
import json
import re
from typing import Any

from curl_cffi.requests import AsyncSession

from thermohub.models import StoreProduct
from thermohub.stores.base import Store, StoreBlocked, StoreError, unit_price

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
            if "incapsula" in html.lower():
                raise StoreBlocked("Coles' bot protection answered with a challenge page")
            raise StoreError("Coles' search page has changed and can't be read")
        try:
            results = json.loads(match.group(1))["props"]["pageProps"]["searchResults"]["results"]
        except (ValueError, KeyError, TypeError) as e:
            raise StoreError("Coles' search page has changed and can't be read") from e
        return parse_results(results)


def parse_results(results: list[Any]) -> list[StoreProduct]:
    products = []
    for raw in results:
        # Skip sponsored placements and the banner tiles mixed into results.
        if not isinstance(raw, Mapping) or raw.get("_type") != "PRODUCT" or raw.get("adId"):
            continue
        pricing = raw.get("pricing") or {}
        price = pricing.get("now")
        if not raw.get("availability") or not isinstance(price, int | float) or price <= 0:
            continue
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
                image=f"{IMAGES}{images[0]['uri']}" if images and images[0].get("uri") else None,
                url=f"{SITE}/product/{_slug(f'{brand} {name} {size}')}-{raw.get('id')}",
            )
        )
    return products


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
