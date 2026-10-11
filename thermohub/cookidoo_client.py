"""Long-lived Cookidoo connection: login, token persistence and raw shopping list."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
import json
import logging
import os
from typing import Any, cast

from aiohttp import ClientSession, ClientTimeout, CookieJar
from cookidoo_api import (
    Cookidoo,
    CookidooAuthData,
    CookidooAuthException,
    CookidooConfig,
    CookidooException,
    CookidooLocalizationConfig,
    get_localization_options,
)

from thermohub.config import Settings

_LOGGER = logging.getLogger(__name__)


class CookidooClient:
    """Wraps cookidoo-api with lazy login and tokens persisted to the data volume.

    Reusing the stored refresh token across restarts avoids a full
    username/password login (which goes through Cloudflare) every time the
    container starts.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: ClientSession | None = None
        self._api: Cookidoo | None = None
        self._login_lock = asyncio.Lock()

    @property
    def localization(self) -> CookidooLocalizationConfig:
        return self._require_api().localization

    async def start(self) -> None:
        localization = await self._resolve_localization()
        # The OAuth login redirects across domains, which needs an unsafe cookie jar.
        self._session = ClientSession(
            cookie_jar=CookieJar(unsafe=True), timeout=ClientTimeout(total=30)
        )
        self._api = Cookidoo(
            self._session,
            CookidooConfig(
                localization=localization,
                email=self._settings.cookidoo_email,
                password=self._settings.cookidoo_password.get_secret_value(),
            ),
            on_auth_data_update=self._save_tokens,
        )
        token_file = self._settings.token_file
        if token_file.exists():
            try:
                self._api.load_token(token_file)
                _LOGGER.info("Restored Cookidoo tokens from %s", token_file)
            except CookidooException:
                _LOGGER.warning("Ignoring unreadable token file %s", token_file)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    async def get_pantry(self) -> Mapping[str, Any]:
        """Fetch the raw shopping list ("pantry") document.

        cookidoo-api's public getters reduce each entry to a name and a
        quantity string, and need three requests of this same document to
        cover recipes, ingredients and extra items. The raw document also has
        the stable ingredient id, unit and shopping category per entry, which
        the merging and store-matching logic key on. The private calls below
        are the ones the library's own getters use; cookidoo-api is pinned to
        an exact version in requirements.txt so they cannot change underneath.
        """
        api = self._require_api()

        async def fetch() -> Mapping[str, Any]:
            await api._ensure_endpoints()
            url = api.api_endpoint / api._path("pantry:home").format(**vars(api.localization))
            return cast(
                Mapping[str, Any],
                api._ensure_mapping(
                    await api._request_json("get", url, "loading shopping list"),
                    "loading shopping list",
                ),
            )

        return await self._with_login(fetch)

    async def _with_login(self, call: Callable[[], Awaitable[Any]]) -> Any:
        api = self._require_api()
        if api.auth_data is None:
            await self._login()
        try:
            return await call()
        except CookidooAuthException:
            # The stored refresh token was revoked or has expired: start over.
            _LOGGER.info("Cookidoo rejected the stored tokens, logging in again")
            await self._login(force=True)
            return await call()

    async def _login(self, force: bool = False) -> None:
        api = self._require_api()
        async with self._login_lock:
            if force or api.auth_data is None:
                _LOGGER.info("Logging in to Cookidoo as %s", self._settings.cookidoo_email)
                await api.login()

    def _save_tokens(self, auth_data: CookidooAuthData) -> None:
        path = self._settings.token_file
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(auth_data), f)
        tmp.replace(path)

    async def _resolve_localization(self) -> CookidooLocalizationConfig:
        country = self._settings.cookidoo_country.lower()
        language = self._settings.cookidoo_language
        options = await get_localization_options(country=country, language=language)
        if not options:
            raise ValueError(
                f"No Cookidoo site for COOKIDOO_COUNTRY={country!r} and "
                f"COOKIDOO_LANGUAGE={language!r} (Australia is au / en-AU)"
            )
        return options[0]

    def _require_api(self) -> Cookidoo:
        if self._api is None:
            raise RuntimeError("CookidooClient.start() has not been called")
        return self._api
