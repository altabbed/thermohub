"""FastAPI application: `uvicorn thermohub.main:app`."""

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

from thermohub.config import Settings, get_settings
from thermohub.cookidoo_client import CookidooClient
from thermohub.models import ShoppingList
from thermohub.shopping_list import build_shopping_list

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = CookidooClient(get_settings())
    await client.start()
    app.state.cookidoo = client
    try:
        yield
    finally:
        await client.close()


def get_cookidoo(request: Request) -> CookidooClient:
    return request.app.state.cookidoo


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
    except CookidooAuthException as e:
        raise HTTPException(
            status_code=502,
            detail="Cookidoo login failed; check COOKIDOO_EMAIL and COOKIDOO_PASSWORD",
        ) from e
    except CookidooException as e:
        raise HTTPException(status_code=502, detail=f"Cookidoo request failed: {e}") from e
    return build_shopping_list(pantry, cookidoo.localization, include_owned=include_owned)


app = FastAPI(title="Thermohub", lifespan=lifespan)
app.include_router(api)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    # The page is static; it calls /api itself, sending the API key if one is set.
    return FileResponse(Path(__file__).parent / "static" / "index.html")
