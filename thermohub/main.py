"""FastAPI application: `uvicorn thermohub.main:app`."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import secrets
from typing import Annotated

from cookidoo_api import CookidooAuthException, CookidooException
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from thermohub.compare import compare
from thermohub.config import Settings, get_settings
from thermohub.cookidoo_client import CookidooClient, NotSignedIn, UnknownRegion, regions
from thermohub.models import (
    CompareRequest,
    Comparison,
    Connection,
    ConnectionState,
    CookidooAccount,
    Region,
    ShoppingList,
    SignInRequest,
    Status,
)
from thermohub.shopping_list import build_shopping_list
from thermohub.stores import Coles, Store, StoreBlocked, StoreError, Woolworths

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = CookidooClient(get_settings())
    await client.start()
    app.state.cookidoo = client
    app.state.stores = [Woolworths(), Coles()]
    try:
        yield
    finally:
        await client.close()
        for store in app.state.stores:
            await store.close()


def get_cookidoo(request: Request) -> CookidooClient:
    return request.app.state.cookidoo


def get_stores(request: Request) -> list[Store]:
    return request.app.state.stores


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    key: Annotated[str | None, Security(APIKeyHeader(name="X-API-Key", auto_error=False))],
) -> None:
    expected = settings.api_key.get_secret_value() if settings.api_key else ""
    if expected and not (key and secrets.compare_digest(key, expected)):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")


api = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


@api.get("/shopping-list", response_model=ShoppingList)
async def get_shopping_list(
    cookidoo: Annotated[CookidooClient, Depends(get_cookidoo)],
    include_owned: bool = False,
) -> ShoppingList:
    """Everything on the Cookidoo shopping list, merged across recipes.

    Items ticked off in Cookidoo are left out unless `include_owned=true`.
    Answers 409 when no Cookidoo account is connected.
    """
    try:
        pantry = await cookidoo.get_pantry()
    except NotSignedIn as e:
        raise HTTPException(
            status_code=409, detail="Connect your Cookidoo account to see your shopping list."
        ) from e
    except CookidooAuthException as e:
        raise HTTPException(
            status_code=409, detail="Cookidoo didn't accept the saved email and password."
        ) from e
    except CookidooException as e:
        raise HTTPException(
            status_code=502, detail="Cookidoo isn't answering right now. Try again in a minute."
        ) from e
    return build_shopping_list(pantry, cookidoo.localization, include_owned=include_owned)


@api.get("/status", response_model=Status)
async def get_status(
    cookidoo: Annotated[CookidooClient, Depends(get_cookidoo)],
    stores: Annotated[list[Store], Depends(get_stores)],
) -> Status:
    """Whether Cookidoo and each store are working right now, in plain language."""
    checks: list[Awaitable[Connection]] = [_check_cookidoo(cookidoo)]
    checks += [_check_store(store) for store in stores]
    return Status(connections=list(await asyncio.gather(*checks)))


@api.get("/cookidoo/account", response_model=CookidooAccount)
async def get_cookidoo_account(
    cookidoo: Annotated[CookidooClient, Depends(get_cookidoo)],
) -> CookidooAccount:
    return _account_view(cookidoo)


@api.get("/cookidoo/regions", response_model=list[Region])
async def get_cookidoo_regions() -> list[Region]:
    return [
        Region(country=r.country_code, language=r.language, site=r.url.split("/foundation")[0])
        for r in await regions()
    ]


@api.post("/cookidoo/sign-in", response_model=CookidooAccount)
async def sign_in_to_cookidoo(
    request: SignInRequest, cookidoo: Annotated[CookidooClient, Depends(get_cookidoo)]
) -> CookidooAccount:
    """Connect a Cookidoo account. The password is used to sign in and not stored."""
    try:
        await cookidoo.sign_in(
            request.email.strip(), request.password, request.country, request.language
        )
    except UnknownRegion as e:
        raise HTTPException(status_code=400, detail="Pick your country from the list.") from e
    except CookidooAuthException as e:
        raise HTTPException(
            status_code=400,
            detail="Cookidoo didn't accept that email and password. Check them and try again.",
        ) from e
    except CookidooException as e:
        raise HTTPException(
            status_code=502,
            detail="Thermohub couldn't reach Cookidoo. Check the internet connection and "
            "try again.",
        ) from e
    return _account_view(cookidoo)


@api.post("/cookidoo/sign-out", response_model=CookidooAccount)
async def sign_out_of_cookidoo(
    cookidoo: Annotated[CookidooClient, Depends(get_cookidoo)],
) -> CookidooAccount:
    await cookidoo.sign_out()
    return _account_view(cookidoo)


@api.post("/compare", response_model=Comparison)
async def compare_prices(
    request: CompareRequest, stores: Annotated[list[Store], Depends(get_stores)]
) -> Comparison:
    """Search every store for each item and total up where it's cheaper.

    Each store's first available search result is the pick; the next few
    come back as alternatives. Totals cover items every store has.
    """
    return await compare(request.items, stores)


async def _check_cookidoo(cookidoo: CookidooClient) -> Connection:
    def connection(state: ConnectionState, summary: str, detail: str | None = None) -> Connection:
        return Connection(
            id="cookidoo", name="Cookidoo", state=state, summary=summary, detail=detail
        )

    email = cookidoo.account.email
    if not cookidoo.signed_in and not cookidoo.can_sign_in_automatically:
        if email:
            return connection(
                "needs_attention", f"Cookidoo signed you out. Sign in again as {email}."
            )
        return connection("not_set_up", "Connect your Cookidoo account to see your shopping list.")
    try:
        await cookidoo.get_pantry()
    except NotSignedIn:
        return connection("needs_attention", "Cookidoo signed you out. Please sign in again.")
    except CookidooAuthException as e:
        return connection(
            "needs_attention", "Cookidoo didn't accept the saved email and password.", str(e)
        )
    except CookidooException as e:
        return connection("unavailable", "Cookidoo isn't answering right now.", str(e))
    email = cookidoo.account.email
    return connection("connected", f"Connected as {email}" if email else "Connected")


async def _check_store(store: Store) -> Connection:
    def connection(state: ConnectionState, summary: str, detail: str | None = None) -> Connection:
        return Connection(
            id=store.id,
            name=store.name,
            state=state,
            summary=summary,
            detail=detail,
            note=f"You don't need to sign in to {store.name} to compare prices.",
        )

    try:
        products = await store.search("milk")
    except StoreBlocked as e:
        return connection(
            "unavailable", f"{store.name} isn't letting Thermohub check prices right now.", str(e)
        )
    except StoreError as e:
        return connection("unavailable", f"Thermohub can't reach {store.name} right now.", str(e))
    if not products:
        return connection(
            "unavailable", f"{store.name} didn't return any prices.", "A search for milk was empty"
        )
    return connection("connected", "Prices available")


def _account_view(cookidoo: CookidooClient) -> CookidooAccount:
    account = cookidoo.account
    return CookidooAccount(
        signed_in=cookidoo.signed_in,
        email=account.email,
        country=account.country,
        language=account.language,
        automatic=cookidoo.can_sign_in_automatically,
    )


app = FastAPI(title="Thermohub", lifespan=lifespan)
app.include_router(api)


@app.middleware("http")
async def revalidate_page_files(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # Make browsers check for a newer page after the container is updated.
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    # The page is static; it calls /api itself, sending the API key if one is set.
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
