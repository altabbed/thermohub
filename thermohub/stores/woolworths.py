"""Woolworths product search, via the JSON API behind woolworths.com.au."""

from collections.abc import Iterator, Mapping
import json
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

from thermohub.models import StoreProduct
from thermohub.stores.base import (
    BLOCKED_STATUSES,
    Store,
    StoreBlocked,
    StoreError,
    StoreUnreadable,
    deal,
    unit_price,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

# Calls the search API from inside a woolworths.com.au page, with its cookies.
_FETCH = """async ([url, body]) => {
  const response = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"},
    body: JSON.stringify(body),
  });
  return {status: response.status, text: await response.text()};
}"""

SITE = "https://www.woolworths.com.au"


class Woolworths(Store):
    id = "woolworths"
    name = "Woolworths"
    home_url = SITE

    async def _search(self, session: AsyncSession, term: str) -> list[StoreProduct]:
        response = await self._request(
            session,
            "POST",
            f"{self.base_url}/apis/ui/Search/products",
            json=_request_body(term),
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/shop/search/products?searchTerm={quote(term)}",
            },
        )
        try:
            data = response.json()
        except ValueError as e:
            raise StoreUnreadable("Woolworths sent something other than search results") from e
        return parse_search(data)

    async def search_in_browser(self, page: "Page", term: str) -> list[StoreProduct]:
        # The same search, made by the woolworths.com.au page itself, so the
        # bot protection sees a browser it has already checked.
        if not page.url.startswith(self.base_url):
            await page.goto(f"{self.base_url}/", wait_until="domcontentloaded", timeout=45_000)
        result = await page.evaluate(
            _FETCH, [f"{self.base_url}/apis/ui/Search/products", _request_body(term)]
        )
        status = result["status"]
        if status in BLOCKED_STATUSES:
            raise StoreBlocked(
                f"Woolworths blocked the search in Thermohub's browser too (HTTP {status})"
            )
        if status >= 400:
            raise StoreError(f"Woolworths returned HTTP {status}")
        try:
            return parse_search(json.loads(result["text"]))
        except ValueError as e:
            raise StoreUnreadable("Woolworths sent something other than search results") from e


def _request_body(term: str) -> dict[str, Any]:
    return {
        "searchTerm": term,
        "pageNumber": 1,
        # A full page, so every size and brand of the item is there to choose from.
        "pageSize": 36,
        "sortType": "TraderRelevance",
        "isSpecial": False,
        "isBundle": False,
        "isMobile": False,
        "filters": [],
        "location": f"/shop/search/products?searchTerm={quote(term)}",
        "formatObject": json.dumps({"name": term}),
    }


def parse_search(data: Mapping[str, Any]) -> list[StoreProduct]:
    products = []
    seen = set()
    for raw in _products(data):
        price = raw.get("Price")
        stockcode = raw.get("Stockcode")
        if not raw.get("IsAvailable", True) or not isinstance(price, int | float) or price <= 0:
            continue
        if stockcode in seen:
            continue
        seen.add(stockcode)
        was = raw.get("WasPrice")
        products.append(
            StoreProduct(
                store="woolworths",
                id=str(stockcode),
                name=raw.get("DisplayName") or raw.get("Name") or "",
                size=raw.get("PackageSize") or None,
                price=price,
                was_price=was if isinstance(was, int | float) and was > price else None,
                on_special=bool(raw.get("IsOnSpecial")),
                unit_price=unit_price(
                    raw.get("CupPrice"), raw.get("CupMeasure"), raw.get("CupString")
                ),
                deal=deal(price, texts=_promotion_texts(raw)),
                image=raw.get("MediumImageFile") or raw.get("SmallImageFile") or None,
                url=f"{SITE}/shop/productdetails/{stockcode}/{raw.get('UrlFriendlyName') or ''}",
            )
        )
    return products


def _promotion_texts(raw: Mapping[str, Any]) -> Iterator[Any]:
    """The words on the product's promotion labels, where a "2 for $10" shows."""
    yield raw.get("PromotionDescription")
    for tag in ("CentreTag", "HeaderTag", "ImageTag"):
        label = raw.get(tag)
        if isinstance(label, Mapping):
            yield from label.values()


def _products(data: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """Every product in the results, size variants included.

    Results come grouped: each entry of ``Products`` is a tile holding its
    own ``Products`` list, the product shown plus its other sizes (the 12,
    24 and 36 packs of the same toilet paper).
    """
    for group in data.get("Products") or []:
        if not isinstance(group, Mapping):
            continue
        inner = group.get("Products")
        if isinstance(inner, list):
            yield from (p for p in inner if isinstance(p, Mapping))
        elif "Stockcode" in group:
            yield group
