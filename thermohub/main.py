"""FastAPI application: `uvicorn thermohub.main:app`."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import secrets
from typing import Annotated

from cookidoo_api import CookidooAuthException, CookidooException
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader

from thermohub.compare import compare
from thermohub.config import Settings, get_settings
from thermohub.cookidoo_client import CookidooClient
from thermohub.models import Check, CompareRequest, Comparison, ShoppingList, Status, StoreStatus
from thermohub.shopping_list import build_shopping_list
from thermohub.stores import Coles, Store, StoreError, Woolworths

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


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
    """
    try:
        pantry = await cookidoo.get_pantry()
    except CookidooException as e:
        raise HTTPException(status_code=502, detail=_cookidoo_error(e)) from e
    return build_shopping_list(pantry, cookidoo.localization, include_owned=include_owned)


@api.get("/status", response_model=Status)
async def get_status(
    cookidoo: Annotated[CookidooClient, Depends(get_cookidoo)],
    stores: Annotated[list[Store], Depends(get_stores)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Status:
    """Whether Cookidoo and each store's price lookups are working right now."""

    async def check_cookidoo() -> Check:
        try:
            await cookidoo.get_pantry()
        except CookidooException as e:
            return Check(ok=False, detail=_cookidoo_error(e))
        return Check(ok=True, detail=f"Signed in as {settings.cookidoo_email}")

    async def check_prices(store: Store) -> Check:
        try:
            products = await store.search("milk")
        except StoreError as e:
            return Check(ok=False, detail=str(e))
        if not products:
            return Check(ok=False, detail="Search worked but found no products")
        return Check(ok=True, detail="Price lookups working")

    cookidoo_check, *price_checks = await asyncio.gather(
        check_cookidoo(), *(check_prices(store) for store in stores)
    )
    return Status(
        cookidoo=cookidoo_check,
        stores=[
            StoreStatus(
                id=store.id,
                name=store.name,
                prices=prices,
                account=Check(ok=False, detail="Only needed later, to fill your cart."),
            )
            for store, prices in zip(stores, price_checks, strict=True)
        ],
    )


@api.post("/compare", response_model=Comparison)
async def compare_prices(
    request: CompareRequest, stores: Annotated[list[Store], Depends(get_stores)]
) -> Comparison:
    """Search every store for each item and total up where it's cheaper.

    Each store's first available search result is the pick; the next few
    come back as alternatives. Totals cover items every store has.
    """
    return await compare(request.items, stores)


def _cookidoo_error(e: CookidooException) -> str:
    if isinstance(e, CookidooAuthException):
        return "Cookidoo login failed; check COOKIDOO_EMAIL and COOKIDOO_PASSWORD"
    return f"Cookidoo request failed: {e}"


app = FastAPI(title="Thermohub", lifespan=lifespan)
app.include_router(api)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    # The page is static; it calls /api itself, sending the API key if one is set.
    return FileResponse(Path(__file__).parent / "static" / "index.html")
