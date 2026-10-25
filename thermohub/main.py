"""FastAPI application: `uvicorn thermohub.main:app`."""

import asyncio
import base64
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
from pathlib import Path
import secrets
import time
from typing import Annotated, Any
from urllib.parse import urlparse
import uuid

from cookidoo_api import CookidooAuthException, CookidooException
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.requests import HTTPConnection
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from thermohub.browser import (
    SITES,
    Browsers,
    CartJob,
    CartLine,
    CartOrder,
    LiveSignIn,
    StoreSite,
    fill_carts,
)
from thermohub.compare import compare
from thermohub.config import Settings, get_settings
from thermohub.cookidoo_client import CookidooClient, NotSignedIn, UnknownRegion, regions
from thermohub.diagnose import Check, check_shop
from thermohub.models import (
    CartJobView,
    CartLineView,
    CartOrderView,
    CartRequest,
    ChoiceRequest,
    CompareRequest,
    Comparison,
    ComparisonJob,
    Connection,
    ConnectionState,
    CookidooAccount,
    Region,
    ShopCheck,
    ShopCheckJob,
    ShoppingList,
    SignInRequest,
    Status,
    StoreAccount,
    StoreId,
    StoreProduct,
)
from thermohub.shopping_list import build_shopping_list
from thermohub.smarts import Preferences
from thermohub.stores import Coles, Store, StoreBlocked, StoreError, Woolworths

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC = Path(__file__).parent / "static"

# Cart jobs run after the request that started them; keep them from being garbage collected.
_running: set[asyncio.Task[None]] = set()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = CookidooClient(get_settings())
    await client.start()
    app.state.cookidoo = client
    browsers = Browsers(get_settings().data_dir, get_settings().chromium)
    app.state.browsers = browsers
    # The stores search in the browser when their bot protection blocks plain requests.
    app.state.stores = [Woolworths(browsers=browsers), Coles(browsers=browsers)]
    app.state.preferences = Preferences(get_settings().preferences_file)
    app.state.sites = SITES
    app.state.cart_jobs = {}
    app.state.comparisons = {}
    app.state.shop_checks = {}
    try:
        yield
    finally:
        await client.close()
        for store in app.state.stores:
            await store.close()
        await app.state.browsers.close()


def get_cookidoo(request: Request) -> CookidooClient:
    return request.app.state.cookidoo


def get_stores(request: Request) -> list[Store]:
    return request.app.state.stores


def get_preferences(request: Request) -> Preferences:
    return request.app.state.preferences


def get_browsers(connection: HTTPConnection) -> Browsers:
    return connection.app.state.browsers


def get_sites(connection: HTTPConnection) -> dict[str, StoreSite]:
    return connection.app.state.sites


def get_cart_jobs(request: Request) -> dict[str, CartJob]:
    return request.app.state.cart_jobs


@dataclass
class ComparisonRun:
    id: str
    total: int
    checked: dict[str, int]
    started: float
    done: bool = False
    result: Comparison | None = None
    error: str | None = None


def get_comparisons(request: Request) -> dict[str, ComparisonRun]:
    return request.app.state.comparisons


def get_shop_checks(request: Request) -> dict[str, ShopCheckJob]:
    return request.app.state.shop_checks


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
    browsers: Annotated[Browsers, Depends(get_browsers)],
) -> Status:
    """Whether Cookidoo and each store are working right now, in plain language."""
    checks: list[Awaitable[Connection]] = [_check_cookidoo(cookidoo)]
    checks += [_check_store(store, browsers.signed_in(store.id)) for store in stores]
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
    request: CompareRequest,
    stores: Annotated[list[Store], Depends(get_stores)],
    preferences: Annotated[Preferences, Depends(get_preferences)],
) -> Comparison:
    """Search every store for each item and total up where it's cheaper.

    Each store's candidates are ranked best pick first: the product chosen
    last time for that ingredient, else the cheapest real match for the
    amount needed. Totals cover items every store has.
    """
    return await compare(request.items, stores, preferences)


@api.get("/stores", response_model=list[StoreAccount])
async def get_store_accounts(
    browsers: Annotated[Browsers, Depends(get_browsers)],
    sites: Annotated[dict[str, StoreSite], Depends(get_sites)],
) -> list[StoreAccount]:
    """Which stores Thermohub is signed in to. Sign in with the WebSocket below."""
    return [
        StoreAccount(
            id=site.id,  # type: ignore[arg-type]
            name=site.name,
            signed_in=browsers.signed_in(site.id),
            cart_url=site.cart_url,
        )
        for site in sites.values()
    ]


@api.get("/stores/{store_id}/search", response_model=list[StoreProduct])
async def search_store(
    store_id: str,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    stores: Annotated[list[Store], Depends(get_stores)],
) -> list[StoreProduct]:
    """What Thermohub reads from a store's search for these words, in the store's order.

    For checking a comparison against the store's own website.
    """
    store = next((s for s in stores if s.id == store_id), None)
    if store is None:
        raise HTTPException(status_code=404, detail="No such store.")
    try:
        return await store.search(q)
    except StoreError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@api.post("/comparisons", response_model=ComparisonJob, status_code=202)
async def start_comparison(
    request: CompareRequest,
    stores: Annotated[list[Store], Depends(get_stores)],
    preferences: Annotated[Preferences, Depends(get_preferences)],
    runs: Annotated[dict[str, ComparisonRun], Depends(get_comparisons)],
) -> ComparisonJob:
    """The same comparison as POST /api/compare, run in the background.

    A long list can take a minute or two. The page follows it with GET
    /api/comparisons/{id} instead of holding one request open that long (which
    phones give up on), and can pick it up again after a reload.
    """
    run = ComparisonRun(
        id=uuid.uuid4().hex,
        total=len(request.items),
        checked={store.id: 0 for store in stores},
        started=time.time(),
    )
    for old in sorted(runs.values(), key=lambda r: r.started)[:-9]:
        runs.pop(old.id, None)  # keep the last 10
    runs[run.id] = run

    def searched(store_id: StoreId) -> None:
        run.checked[store_id] = min(run.checked.get(store_id, 0) + 1, run.total)

    async def work() -> None:
        try:
            run.result = await compare(request.items, stores, preferences, searched)
        except Exception:
            logging.getLogger(__name__).exception("Comparison failed")
            run.error = "Something went wrong while comparing. Please try again."
        finally:
            run.done = True

    task = asyncio.create_task(work())
    _running.add(task)
    task.add_done_callback(_running.discard)
    return _comparison_view(run)


@api.get("/comparisons/{run_id}", response_model=ComparisonJob)
async def get_comparison(
    run_id: str, runs: Annotated[dict[str, ComparisonRun], Depends(get_comparisons)]
) -> ComparisonJob:
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="That comparison has expired. Compare again.")
    return _comparison_view(run)


def _comparison_view(run: ComparisonRun) -> ComparisonJob:
    return ComparisonJob(
        id=run.id,
        done=run.done,
        total=run.total,
        checked=run.checked,  # type: ignore[arg-type]
        result=run.result,
        error=run.error,
    )


@api.post("/stores/{store_id}/checks", response_model=ShopCheckJob, status_code=202)
async def start_shop_check(
    store_id: str,
    stores: Annotated[list[Store], Depends(get_stores)],
    browsers: Annotated[Browsers, Depends(get_browsers)],
    sites: Annotated[dict[str, StoreSite], Depends(get_sites)],
    checks: Annotated[dict[str, ShopCheckJob], Depends(get_shop_checks)],
) -> ShopCheckJob:
    """Check a shop step by step (search, search in the browser, sign-in page,
    signed in?) and report what each step saw; follow with GET .../checks/{id}."""
    store = next((s for s in stores if s.id == store_id), None)
    if store is None or store_id not in sites:
        raise HTTPException(status_code=404, detail="No such store.")
    job = ShopCheckJob(id=uuid.uuid4().hex, store=store_id, done=False, checks=[])  # type: ignore[arg-type]
    for old in list(checks)[:-4]:
        checks.pop(old, None)  # keep the last 5
    checks[job.id] = job

    async def report(check: Check) -> None:
        job.checks.append(
            ShopCheck(
                name=check.name,
                ok=check.ok,
                summary=check.summary,
                facts=check.facts,
                screenshot="data:image/jpeg;base64," + base64.b64encode(check.screenshot).decode()
                if check.screenshot
                else None,
            )
        )

    async def work() -> None:
        try:
            # A fresh store, so the plain search isn't answered from the cache.
            fresh = {"woolworths": Woolworths, "coles": Coles}[store_id]()
            await check_shop(fresh, sites[store_id], browsers, report=report)
        except Exception as e:
            logging.getLogger(__name__).exception("Checking %s failed", store_id)
            await report(Check("Run the checks", False, str(e).splitlines()[0][:300]))
        finally:
            job.done = True

    task = asyncio.create_task(work())
    _running.add(task)
    task.add_done_callback(_running.discard)
    return job


@api.get("/stores/{store_id}/checks/{check_id}", response_model=ShopCheckJob)
async def get_shop_check(
    store_id: str,
    check_id: str,
    checks: Annotated[dict[str, ShopCheckJob], Depends(get_shop_checks)],
) -> ShopCheckJob:
    job = checks.get(check_id)
    if job is None or job.store != store_id:
        raise HTTPException(status_code=404, detail="That check has been cleared. Run it again.")
    return job


@api.post("/stores/{store_id}/sign-out", status_code=204)
async def sign_out_of_store(
    store_id: str,
    browsers: Annotated[Browsers, Depends(get_browsers)],
    sites: Annotated[dict[str, StoreSite], Depends(get_sites)],
) -> None:
    """Forget the store sign-in made inside Thermohub."""
    if store_id not in sites:
        raise HTTPException(status_code=404, detail="No such store.")
    await browsers.sign_out(store_id)


@api.post("/carts", response_model=CartJobView, status_code=202)
async def send_to_carts(
    request: CartRequest,
    browsers: Annotated[Browsers, Depends(get_browsers)],
    sites: Annotated[dict[str, StoreSite], Depends(get_sites)],
    jobs: Annotated[dict[str, CartJob], Depends(get_cart_jobs)],
    preferences: Annotated[Preferences, Depends(get_preferences)],
) -> CartJobView:
    """Start adding products to the stores' carts; follow along with GET /api/carts/{id}.

    Each product is added on the store's own website in the browser that was
    signed in from Settings. Products that make it into the cart become the
    usual pick for their ingredient.
    """
    orders = []
    for order in request.orders:
        site = urlparse(sites[order.store].account_url)
        for line in order.lines:
            # Only the shop's own pages: the browser is signed in to it.
            url = urlparse(line.url)
            if (url.scheme, url.hostname) != (site.scheme, site.hostname):
                raise HTTPException(
                    status_code=400, detail=f"{line.name} isn't a {site.hostname} page."
                )
        orders.append(
            CartOrder(
                store=order.store,
                lines=[
                    CartLine(line.product_id, line.name, line.url, line.quantity, line.key)
                    for line in order.lines
                ],
            )
        )
    job = CartJob(id=uuid.uuid4().hex, orders=orders)
    for old in sorted(jobs.values(), key=lambda j: j.started)[:-19]:
        jobs.pop(old.id, None)  # keep the last 20
    jobs[job.id] = job

    def remember(order: CartOrder, line: CartLine) -> None:
        if line.key:
            preferences.remember(order.store, line.key, line.product_id, line.name, line.quantity)

    task = asyncio.create_task(fill_carts(job, browsers, sites, remember))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return _job_view(job, sites)


@api.get("/carts/{job_id}", response_model=CartJobView)
async def get_cart_job(
    job_id: str,
    jobs: Annotated[dict[str, CartJob], Depends(get_cart_jobs)],
    sites: Annotated[dict[str, StoreSite], Depends(get_sites)],
) -> CartJobView:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="That cart job has finished and been cleared.")
    return _job_view(job, sites)


@api.post("/choices", status_code=204)
async def remember_choice(
    request: ChoiceRequest, preferences: Annotated[Preferences, Depends(get_preferences)]
) -> None:
    """Remember the product to pick for an ingredient at a store from now on.

    `quantity` is remembered too, for items the recipes don't measure.
    """
    preferences.remember(
        request.store, request.key, request.product_id, request.name, request.quantity
    )


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


async def _check_store(store: Store, signed_in: bool) -> Connection:
    def connection(state: ConnectionState, summary: str, detail: str | None = None) -> Connection:
        return Connection(
            id=store.id,
            name=store.name,
            state=state,
            summary=summary,
            detail=detail,
            note=None if signed_in else f"Sign in to send items to your {store.name} cart.",
            signed_in=signed_in,
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


def _job_view(job: CartJob, sites: dict[str, StoreSite]) -> CartJobView:
    return CartJobView(
        id=job.id,
        finished=job.finished,
        orders=[
            CartOrderView(
                store=order.store,  # type: ignore[arg-type]
                name=sites[order.store].name,
                state=order.state,  # type: ignore[arg-type]
                cart_url=sites[order.store].cart_url,
                message=order.message,
                lines=[
                    CartLineView(
                        product_id=line.product_id,
                        name=line.name,
                        url=line.url,
                        quantity=line.quantity,
                        key=line.key,
                        status=line.status,  # type: ignore[arg-type]
                        message=line.message,
                        screenshot="data:image/jpeg;base64,"
                        + base64.b64encode(line.screenshot).decode()
                        if line.screenshot
                        else None,
                    )
                    for line in order.lines
                ],
            )
            for order in job.orders
        ],
    )


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


@app.websocket("/api/stores/{store_id}/sign-in")
async def store_sign_in(
    websocket: WebSocket,
    store_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    browsers: Annotated[Browsers, Depends(get_browsers)],
    sites: Annotated[dict[str, StoreSite], Depends(get_sites)],
) -> None:
    """Sign in to a store in Thermohub's browser, live.

    The first message is {"type": "hello", "width", "height", "scale", "key"}.
    The server then sends the page as binary JPEG frames, plus JSON
    messages: {"type": "editable", "value"} after a tap (show a keyboard),
    {"type": "address", "url"}, {"type": "result", "signed_in"} and
    {"type": "error", "message"}. The page sends {"type": "tap", "x", "y"},
    "text", "key", "scroll", "back", "restart", "done" or "cancel".
    """
    # Browsers let any website open a WebSocket to any address, so refuse
    # pages that aren't Thermohub's own: they'd see the signed-in shop.
    origin = websocket.headers.get("origin")
    if origin and urlparse(origin).netloc != websocket.headers.get("host"):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    outgoing: asyncio.Queue[bytes | dict[str, Any] | None] = asyncio.Queue(maxsize=16)

    async def send(message: bytes | dict[str, Any]) -> None:
        if isinstance(message, bytes) and outgoing.full():
            return  # drop a video frame rather than fall behind
        await outgoing.put(message)

    async def sender() -> None:
        while (message := await outgoing.get()) is not None:
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_json(message)

    sending = asyncio.create_task(sender())
    try:
        hello = await websocket.receive_json()
        expected = settings.api_key.get_secret_value() if settings.api_key else ""
        key = str(hello.get("key") or "")
        site = sites.get(store_id)
        if expected and not secrets.compare_digest(key, expected):
            await send({"type": "error", "message": "Enter Thermohub's access key first."})
        elif site is None:
            await send({"type": "error", "message": "Thermohub doesn't know that store."})
        elif browsers.lock(store_id).locked():
            await send(
                {
                    "type": "error",
                    "message": f"Thermohub is busy with your {site.name} cart. "
                    "Try again in a minute.",
                }
            )
        else:
            async with browsers.lock(store_id):
                await _run_sign_in(websocket, LiveSignIn(browsers, site, send), hello, send)
    except WebSocketDisconnect:
        pass
    finally:
        await outgoing.put(None)
        try:
            await asyncio.wait_for(sending, timeout=5)
        except (TimeoutError, WebSocketDisconnect, RuntimeError):
            sending.cancel()
        try:
            await websocket.close()
        except RuntimeError:
            pass  # already closed by the browser


async def _run_sign_in(
    websocket: WebSocket,
    session: LiveSignIn,
    hello: dict[str, Any],
    send: Callable[[bytes | dict[str, Any]], Awaitable[None]],
) -> None:
    try:
        await session.start(
            int(hello.get("width") or 390), int(hello.get("height") or 700), hello.get("scale") or 1
        )
        while True:
            message = await websocket.receive_json()
            kind = message.get("type")
            try:
                if kind == "tap":
                    await session.tap(float(message["x"]), float(message["y"]))
                elif kind == "text":
                    await session.type_text(str(message.get("text", "")))
                elif kind == "key":
                    await session.press(str(message.get("key", "")))
                elif kind == "scroll":
                    await session.scroll(float(message.get("dy", 0)))
                elif kind == "back":
                    await session.back()
                elif kind == "restart":
                    await session.restart()
                elif kind == "done":
                    await send({"type": "result", "signed_in": await session.finish()})
                    return
                elif kind == "cancel":
                    return
            except (KeyError, TypeError, ValueError):
                continue  # a malformed message; ignore it
            except Exception as e:  # a page action failed; the person can carry on
                logging.getLogger(__name__).info("Sign-in action %s failed: %s", kind, e)
    except Exception as e:
        if isinstance(e, WebSocketDisconnect):
            raise
        logging.getLogger(__name__).warning("Store sign-in failed: %s", e)
        await send({"type": "error", "message": "The sign-in page stopped working. Try again."})
    finally:
        await session.close()


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
