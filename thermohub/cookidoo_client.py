"""Long-lived Cookidoo connection: sign-in, token persistence and raw shopping list."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any, TypeVar, cast

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
_T = TypeVar("_T")


class NotSignedIn(Exception):
    """Cookidoo needs someone to sign in from the Settings page."""


class UnknownRegion(ValueError):
    """No Cookidoo site for the requested country and language."""


@dataclass
class Account:
    """What Thermohub remembers about the Cookidoo account. Never the password."""

    country: str
    language: str
    email: str | None = None
    # Set by signing out, so sign-in details from the environment stop applying.
    signed_out: bool = False


class CookidooClient:
    """Wraps cookidoo-api with sign-in from the web page and saved tokens.

    The password is used once, to sign in; afterwards the OAuth tokens
    (saved to the data volume and refreshed automatically) keep the
    account connected across restarts. When Cookidoo stops accepting them,
    the user signs in again from the Settings page. Sign-in details given
    as environment variables are the exception: they are used to sign in
    automatically until someone signs out on the page.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: ClientSession | None = None
        self._api: Cookidoo | None = None
        self._account = Account(
            country=settings.cookidoo_country, language=settings.cookidoo_language
        )
        self._lock = asyncio.Lock()

    @property
    def account(self) -> Account:
        return self._account

    @property
    def signed_in(self) -> bool:
        return self._api is not None and self._api.auth_data is not None

    @property
    def can_sign_in_automatically(self) -> bool:
        return self._env_credentials() is not None

    @property
    def localization(self) -> CookidooLocalizationConfig:
        return self._require_api().localization

    async def start(self) -> None:
        self._account = self._load_account()
        # The OAuth login redirects across domains, which needs an unsafe cookie jar.
        self._session = ClientSession(
            cookie_jar=CookieJar(unsafe=True), timeout=ClientTimeout(total=30)
        )
        try:
            self._api = await self._new_api(self._account.country, self._account.language)
        except UnknownRegion:
            _LOGGER.warning(
                "No Cookidoo site for %s/%s; using Australia",
                self._account.country,
                self._account.language,
            )
            self._account = Account(country="au", language="en-AU", email=self._account.email)
            self._api = await self._new_api("au", "en-AU")
        token_file = self._settings.token_file
        if token_file.exists():
            try:
                self._api.load_token(token_file)
                _LOGGER.info("Restored Cookidoo sign-in from %s", token_file)
            except CookidooException:
                _LOGGER.warning("Ignoring unreadable token file %s", token_file)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    async def sign_in(self, email: str, password: str, country: str, language: str) -> None:
        """Sign in with the details typed on the Settings page.

        Raises UnknownRegion, or CookidooAuthException when Cookidoo rejects
        the email and password.
        """
        api = await self._new_api(country, language, email, password)
        async with self._lock:
            await api.login()  # the token callback saves the new sign-in
            self._api = api
            self._account = Account(country=country, language=language, email=email)
            self._save_account()
        _LOGGER.info("Signed in to Cookidoo as %s", email)

    async def sign_out(self) -> None:
        async with self._lock:
            self._settings.token_file.unlink(missing_ok=True)
            self._account = Account(
                country=self._account.country, language=self._account.language, signed_out=True
            )
            self._save_account()
            self._api = await self._new_api(self._account.country, self._account.language)
        _LOGGER.info("Signed out of Cookidoo")

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

        async def fetch(api: Cookidoo) -> Mapping[str, Any]:
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

    async def _with_login(self, call: Callable[[Cookidoo], Awaitable[_T]]) -> _T:
        if not self.signed_in:
            await self._sign_in_automatically()
        try:
            return await call(self._require_api())
        except CookidooAuthException:
            # The saved sign-in was revoked or has expired.
            if self._env_credentials() is None:
                await self._forget_sign_in()
                raise NotSignedIn("Cookidoo signed you out") from None
            _LOGGER.info("Cookidoo rejected the saved sign-in; signing in again")
            await self._sign_in_automatically(force=True)
            return await call(self._require_api())

    async def _sign_in_automatically(self, force: bool = False) -> None:
        credentials = self._env_credentials()
        if credentials is None:
            raise NotSignedIn("Not signed in to Cookidoo")
        email, password = credentials
        async with self._lock:
            if force or not self.signed_in:
                _LOGGER.info("Signing in to Cookidoo as %s (from the container settings)", email)
                api = await self._new_api(
                    self._account.country, self._account.language, email, password
                )
                await api.login()
                self._api = api
                self._account.email = email

    async def _forget_sign_in(self) -> None:
        async with self._lock:
            self._settings.token_file.unlink(missing_ok=True)
            self._api = await self._new_api(self._account.country, self._account.language)

    def _env_credentials(self) -> tuple[str, str] | None:
        email, password = self._settings.cookidoo_email, self._settings.cookidoo_password
        if not email or password is None or self._account.signed_out:
            return None
        if self._account.email not in (None, email):
            return None  # someone signed in with another account on the page
        return email, password.get_secret_value()

    async def _new_api(
        self, country: str, language: str, email: str = "", password: str = ""
    ) -> Cookidoo:
        options = await get_localization_options(country=country.lower(), language=language)
        if not options:
            raise UnknownRegion(f"No Cookidoo site for {country}/{language}")
        return Cookidoo(
            self._require_session(),
            CookidooConfig(localization=options[0], email=email, password=password),
            on_auth_data_update=self._save_tokens,
        )

    def _save_tokens(self, auth_data: CookidooAuthData) -> None:
        _write_private_json(self._settings.token_file, asdict(auth_data))

    def _load_account(self) -> Account:
        path = self._settings.account_file
        if path.exists():
            try:
                return Account(**json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                _LOGGER.warning("Ignoring unreadable account file %s", path)
        return Account(
            country=self._settings.cookidoo_country,
            language=self._settings.cookidoo_language,
            email=self._settings.cookidoo_email,
        )

    def _save_account(self) -> None:
        _write_private_json(self._settings.account_file, asdict(self._account))

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("CookidooClient.start() has not been called")
        return self._session

    def _require_api(self) -> Cookidoo:
        if self._api is None:
            raise RuntimeError("CookidooClient.start() has not been called")
        return self._api


async def regions() -> list[CookidooLocalizationConfig]:
    """Every Cookidoo site, one entry per country and language."""
    seen: set[tuple[str, str]] = set()
    unique = []
    for option in await get_localization_options():
        key = (option.country_code, option.language)
        if key not in seen:
            seen.add(key)
            unique.append(option)
    return unique


def _write_private_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    tmp.replace(path)
