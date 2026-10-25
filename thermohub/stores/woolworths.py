"""Woolworths product search, via the JSON API behind woolworths.com.au."""

from collections.abc import Iterator, Mapping
import json
from typing import Any
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

from thermohub.models import StoreProduct
from thermohub.stores.base import Store, StoreError, unit_price

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
            json={
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
            },
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/shop/search/products?searchTerm={quote(term)}",
            },
        )
        try:
            data = response.json()
        except ValueError as e:
            raise StoreError("Woolworths sent something other than search results") from e
        return parse_search(data)


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
                image=raw.get("MediumImageFile") or raw.get("SmallImageFile") or None,
                url=f"{SITE}/shop/productdetails/{stockcode}/{raw.get('UrlFriendlyName') or ''}",
            )
        )
    return products


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
