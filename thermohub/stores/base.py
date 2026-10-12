"""Shared plumbing for supermarket product search."""

import asyncio
import logging
import re
import time
from typing import Any

from curl_cffi.requests import AsyncSession, RequestsError, Response

from thermohub.models import StoreId, StoreProduct, UnitPrice

_LOGGER = logging.getLogger(__name__)

# Neither supermarket has a public API, and both sit behind bot protection
# (Akamai at Woolworths, Incapsula at Coles) that rejects ordinary HTTP
# clients. curl_cffi presents Chrome's TLS and HTTP/2 fingerprint instead.
IMPERSONATE = "chrome"
BLOCKED_STATUSES = {401, 403, 429}


class StoreError(Exception):
    """A search failed; the message is shown to the user."""


class StoreBlocked(StoreError):
    """The store's bot protection rejected the request."""


class Store:
    """Product search against one supermarket's website.

    Subclasses implement ``_search``. A session first loads the home page
    so the bot-protection cookies are set, as a browser would; when a
    search is blocked anyway the session is rebuilt once before giving up.
    Results are cached so the status check and repeated comparisons don't
    hit the store again.
    """

    id: StoreId
    name: str
    home_url: str

    def __init__(self, base_url: str | None = None, cache_seconds: float = 1800) -> None:
        self.base_url = (base_url or self.home_url).rstrip("/")
        self._cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, list[StoreProduct]]] = {}
        self._session: AsyncSession | None = None
        self._session_lock = asyncio.Lock()
        # Keep it polite: a comparison of 30 items is 30 searches per store.
        self._concurrency = asyncio.Semaphore(3)

    async def search(self, term: str) -> list[StoreProduct]:
        """Available products for ``term``, in the store's relevance order."""
        key = term.casefold()
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._cache_seconds:
            return cached[1]
        async with self._concurrency:
            session = await self._get_session()
            try:
                products = await self._search(session, term)
            except StoreBlocked:
                _LOGGER.info("%s blocked a search; retrying with a fresh session", self.name)
                await self._reset_session(session)
                products = await self._search(await self._get_session(), term)
        self._cache[key] = (time.monotonic(), products)
        return products

    async def close(self) -> None:
        await self._reset_session()

    async def _search(self, session: AsyncSession, term: str) -> list[StoreProduct]:
        raise NotImplementedError

    async def _get_session(self) -> AsyncSession:
        async with self._session_lock:
            if self._session is None:
                session = AsyncSession(impersonate=IMPERSONATE, timeout=20)
                try:
                    await self._request(session, "GET", f"{self.base_url}/")
                except BaseException:
                    await session.close()
                    raise
                self._session = session
            return self._session

    async def _reset_session(self, stale: AsyncSession | None = None) -> None:
        """Drop the session; with ``stale``, only if no other search replaced it already."""
        async with self._session_lock:
            if self._session is not None and (stale is None or self._session is stale):
                await self._session.close()
                self._session = None

    async def _request(
        self, session: AsyncSession, method: str, url: str, **kwargs: Any
    ) -> Response:
        try:
            response = await session.request(method, url, **kwargs)
        except RequestsError as e:
            reason = str(e).split(" See https://curl.se")[0]  # drop libcurl's doc link
            raise StoreError(f"Couldn't reach {self.name}: {reason}") from e
        if response.status_code in BLOCKED_STATUSES:
            raise StoreBlocked(
                f"{self.name} blocked the request (HTTP {response.status_code}); "
                "its bot protection may be rejecting this server"
            )
        if response.status_code >= 400:
            raise StoreError(f"{self.name} returned HTTP {response.status_code}")
        return response


_MEASURE_RE = re.compile(r"(\d+(?:\.\d+)?)?\s*(kg|g|ml|l|ea|each)\b", re.IGNORECASE)
_PER = {"kg": ("kg", 1), "g": ("kg", 1000), "l": ("L", 1), "ml": ("L", 1000)}


def unit_price(price: Any, measure: Any, text: Any) -> UnitPrice | None:
    """Normalise a store's unit price to per 1 kg, 1 L or 1 each.

    ``measure`` is what the price is quoted per: "100g", "1KG", "1L",
    "1EA" and so on. Returns None when it can't be read.
    """
    if not isinstance(price, int | float) or price <= 0 or not isinstance(measure, str):
        return None
    match = _MEASURE_RE.search(measure)
    if match is None:
        return None
    quantity = float(match.group(1) or 1)
    unit = match.group(2).lower()
    per, scale = _PER.get(unit, ("each", 1))
    if quantity <= 0:
        return None
    return UnitPrice(
        price=round(price * scale / quantity, 4),
        per=per,
        text=text if isinstance(text, str) and text else f"${price:.2f} / {measure}",
    )
