"""Shared plumbing for supermarket product search."""

import asyncio
from collections.abc import Iterable
from contextlib import AbstractAsyncContextManager
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Protocol

from curl_cffi.requests import AsyncSession, RequestsError, Response

from thermohub.models import Deal, StoreId, StoreProduct, UnitPrice

if TYPE_CHECKING:
    from playwright.async_api import Page

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


class StoreUnreadable(StoreError):
    """The store answered with something that can't be read as search results."""


class BrowserPages(Protocol):
    def search_page(self, store_id: str) -> AbstractAsyncContextManager["Page"]: ...


# After plain requests are blocked, search in the browser for this long.
BROWSER_SECONDS = 1800


class Store:
    """Product search against one supermarket's website.

    Subclasses implement ``_search``. A session first loads the home page
    so the bot-protection cookies are set, as a browser would; when a
    search is blocked anyway the session is rebuilt once. If it's still
    blocked and Thermohub's browser is available (``browsers``), the search
    is done in the browser instead, which runs the store's bot checks like
    any browser; searches stay in the browser for a while after that.
    Results are cached so the status check and repeated comparisons don't
    hit the store again.

    In the browser, one page opens the store's website (``open_in_browser``)
    and every search is then made from that page (``search_in_browser``),
    the way the website's own search box does: loading the whole website
    for each search takes ten times as long.
    """

    id: StoreId
    name: str
    home_url: str

    def __init__(
        self,
        base_url: str | None = None,
        cache_seconds: float = 1800,
        browsers: BrowserPages | None = None,
    ) -> None:
        self.base_url = (base_url or self.home_url).rstrip("/")
        self._browsers = browsers
        self._browser_until = 0.0
        self._visit_lock = asyncio.Lock()
        self._visits = 0  # times the search page has opened the website
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
            if self._browsers and time.monotonic() < self._browser_until:
                products = await self.search_with_browser(term)
            else:
                try:
                    products = await self.search_plainly(term)
                except (StoreBlocked, StoreUnreadable) as e:
                    if not self._browsers:
                        raise
                    _LOGGER.info("%s: %s. Searching in the browser instead.", self.name, e)
                    products = await self.search_with_browser(term)
                    self._browser_until = time.monotonic() + BROWSER_SECONDS
        self._cache[key] = (time.monotonic(), products)
        return products

    async def search_plainly(self, term: str) -> list[StoreProduct]:
        """Search with ordinary requests (no browser), retrying once on a fresh session."""
        session = await self._get_session()
        try:
            return await self._search(session, term)
        except StoreBlocked:
            _LOGGER.info("%s blocked a search; retrying with a fresh session", self.name)
            await self._reset_session(session)
            return await self._search(await self._get_session(), term)

    async def open_in_browser(self, page: "Page") -> None:
        """Open the store's website in ``page``, passing its bot checks."""
        await page.goto(f"{self.base_url}/", wait_until="domcontentloaded", timeout=45_000)

    async def search_in_browser(self, page: "Page", term: str) -> list[StoreProduct]:
        """Search from ``page``, which has the store's website open.

        Several searches can run in the same page at once, so this mustn't
        navigate it.
        """
        raise NotImplementedError

    async def search_with_browser(self, term: str) -> list[StoreProduct]:
        """Search in Thermohub's browser, from its page for this store's searches."""
        assert self._browsers is not None
        try:
            async with self._browsers.search_page(self.id) as page:
                visit = await self._visit(page)
                try:
                    return await self.search_in_browser(page, term)
                except StoreBlocked:
                    pass  # the bot check wants to run again
                except StoreError:
                    raise
                except Exception:
                    pass  # the page reloaded under the search (a bot check, or another search)
                await self._visit(page, again=visit)  # unless another search already did
                return await self.search_in_browser(page, term)
        except StoreError:
            raise
        except Exception as e:  # the browser itself failed
            _LOGGER.warning("Searching %s in the browser failed", self.name, exc_info=True)
            reason = str(e).splitlines()[0][:200]
            raise StoreError(f"Couldn't search {self.name} in Thermohub's browser: {reason}") from e

    async def _visit(self, page: "Page", again: int | None = None) -> int:
        """Have the store's website open in ``page``: open it when it isn't, or
        reload it when it's still visit number ``again``. Returns the visit's number.
        """
        async with self._visit_lock:
            if again == self._visits or not page.url.startswith(self.base_url):
                self._visits += 1
                await self.open_in_browser(page)
            return self._visits

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


_DEAL_TEXT = re.compile(r"(\d+)\s*for\s*\$\s*(\d+(?:\.\d{1,2})?)", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def deal(
    price: float, quantity: Any = None, total: Any = None, texts: Iterable[Any] = ()
) -> Deal | None:
    """A multi-buy offer: from the store's own numbers when they make sense,
    else from its wording ("2 for $10", "Pick any 2 for $13"). None unless
    buying that many actually costs less.
    """
    wording = next(
        (
            " ".join(_TAG.sub(" ", t).split())
            for t in texts
            if isinstance(t, str) and _DEAL_TEXT.search(t)
        ),
        None,
    )

    def make(q: Any, t: Any) -> Deal | None:
        if not isinstance(q, int) or isinstance(q, bool) or q < 2:
            return None
        if not isinstance(t, int | float) or t <= 0 or t >= q * price - 0.005:
            return None
        return Deal(quantity=q, price=round(t, 2), text=wording or f"{q} for ${t:.2f}")

    found = make(quantity, total)
    if found is None and wording:
        match = _DEAL_TEXT.search(wording)
        found = make(int(match.group(1)), float(match.group(2)))  # type: ignore[union-attr]
    return found
