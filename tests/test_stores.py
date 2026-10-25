from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from tests.store_fixtures import (
    coles_product,
    coles_search_page,
    woolworths_product,
    woolworths_search,
)
from thermohub.stores import Coles, StoreBlocked, StoreError, Woolworths
from thermohub.stores.base import unit_price
from thermohub.stores.coles import _NEXT_DATA_RE, parse_results
from thermohub.stores.woolworths import parse_search


@pytest.mark.parametrize(
    ("price", "measure", "expected"),
    [
        (1.20, "100g", (12.0, "kg")),
        (13.57, "1KG", (13.57, "kg")),
        (3.10, "1L", (3.10, "L")),
        (0.45, "100ML", (4.5, "L")),
        (0.80, "1EA", (0.80, "each")),
        (2.00, "each", (2.00, "each")),
    ],
)
def test_unit_price_is_normalised(price, measure, expected):
    result = unit_price(price, measure, None)

    assert (result.price, result.per) == expected


@pytest.mark.parametrize(("price", "measure"), [(None, "1KG"), (0, "1KG"), (1.0, ""), (1.0, None)])
def test_unreadable_unit_price(price, measure):
    assert unit_price(price, measure, None) is None


def test_parse_woolworths():
    products = parse_search(
        woolworths_search(
            woolworths_product(144329, "Brown Onions", 4.50, size="1kg"),
            woolworths_product(1, "Unavailable Onions", 3.00, available=False),
            woolworths_product(
                208002, "Woolworths Garlic", 1.00, was=1.50, size="100g", cup=(10.0, "1KG")
            ),
        )
    )

    assert [p.id for p in products] == ["144329", "208002"]
    onions, garlic = products
    assert onions.name == "Brown Onions 1kg"
    assert onions.price == 4.50
    assert onions.was_price is None
    assert onions.url == "https://www.woolworths.com.au/shop/productdetails/144329/brown-onions"
    assert onions.image.endswith("/medium/144329.jpg")
    assert garlic.on_special is True
    assert garlic.was_price == 1.50
    assert (garlic.unit_price.price, garlic.unit_price.per) == (10.0, "kg")
    assert garlic.unit_price.text == "$10.00 / 1KG"


def test_parse_woolworths_keeps_every_size():
    def tissue(stockcode, rolls, price):
        return woolworths_product(
            stockcode,
            "Quilton Toilet Tissue 3 Ply",
            price,
            size=f"{rolls} pack",
            cup=(round(price / rolls, 2), "1EA"),
        )

    products = parse_search(
        woolworths_search(
            [tissue(1, 12, 9.50), tissue(2, 24, 14.00), tissue(3, 36, 20.00)],
            tissue(2, 24, 14.00),  # the same product again in another tile
        )
    )

    assert [(p.id, p.size) for p in products] == [
        ("1", "12 pack"),
        ("2", "24 pack"),
        ("3", "36 pack"),
    ]


def test_parse_coles():
    products = parse_results(
        [
            coles_product(9999, "Sponsored", "Onion Rings", 3.00, ad="ad-123"),
            coles_product(5134236, "Coles", "Brown Onions", 4.00, size="1kg"),
            {"_type": "SINGLE_TILE", "id": "banner"},
            coles_product(1111, "Coles", "Sold Out Onions", 2.00, available=False),
            coles_product(
                2222, "Coles", "Garlic", 1.00, was=1.40, size="100g", unit=(1.00, 100, "g")
            ),
            coles_product(9999, "Sponsored", "Onion Rings", 3.00),  # listed again, unsponsored
        ]
    )

    # Sponsored products are real products and stay, once each.
    assert [p.id for p in products] == ["9999", "5134236", "2222"]
    products = products[1:]
    onions, garlic = products
    assert onions.name == "Coles Brown Onions"
    assert onions.size == "1kg"
    assert onions.url == "https://www.coles.com.au/product/coles-brown-onions-1kg-5134236"
    assert onions.image == "https://productimages.coles.com.au/productimages/5/5134236.jpg"
    assert onions.on_special is False
    assert garlic.on_special is True
    assert garlic.was_price == 1.40
    assert (garlic.unit_price.price, garlic.unit_price.per) == (10.0, "kg")
    assert garlic.unit_price.text == "$1.00 per 100g"


def test_parse_coles_bananas_and_deals():
    loose, mini, organic, pasta = parse_results(
        [
            coles_product(
                409499, "Coles", "Bananas", 0.68, size="approx 170g", unit=(4.00, 1, "kg")
            ),
            coles_product(5555, "Coles", "Mini Bananas Prepacked", 3.50, size="750g"),
            coles_product(6666, "Coles", "Organic Bananas", 6.00, size="1kg"),
            coles_product(7777, "Latina", "Fresh Agnolotti", 8.00, size="375g", multibuy=(2, 13)),
        ]
    )

    assert (loose.name, loose.size, loose.price) == ("Coles Bananas", "approx 170g", 0.68)
    assert (mini.deal, organic.deal) == (None, None)
    assert (pasta.deal.quantity, pasta.deal.price, pasta.deal.text) == (
        2,
        13.0,
        "Pick any 2 for $13",
    )


@pytest.mark.parametrize(
    ("tag", "price", "deal"),
    [
        ('<div class="multibuy">2 for $10</div>', 6.00, (2, 10.0, "2 for $10")),
        ("Any 3 for $12.50", 5.00, (3, 12.5, "Any 3 for $12.50")),
        ("2 for $12", 6.00, None),  # no saving: not a deal
        ("Save $1.00", 6.00, None),
        (None, 6.00, None),
    ],
)
def test_parse_woolworths_deals(tag, price, deal):
    [product] = parse_search(woolworths_search(woolworths_product(1, "Pasta", price, tag=tag)))

    found = product.deal and (product.deal.quantity, product.deal.price, product.deal.text)
    assert found == deal


class FakeSupermarket:
    """A local stand-in for a store website: sets a cookie on the home page,
    and answers searches only from sessions that have it."""

    def __init__(self, search_response, block_first_searches=0):
        self.search_response = search_response
        self.block_first_searches = block_first_searches
        self.home_visits = 0
        self.searches = []
        app = web.Application()
        app.router.add_get("/", self.home)
        app.router.add_post("/apis/ui/Search/products", self.search)
        app.router.add_get("/search/products", self.search)
        self.server = TestServer(app)

    async def home(self, request):
        self.home_visits += 1
        response = web.Response(text="<html>home</html>", content_type="text/html")
        response.set_cookie("bot-check", f"visit-{self.home_visits}")
        return response

    async def search(self, request):
        body = await request.json() if request.method == "POST" else dict(request.query)
        self.searches.append((request.cookies.get("bot-check"), body))
        if request.cookies.get("bot-check") is None:
            return web.Response(status=403)
        if self.block_first_searches:
            self.block_first_searches -= 1
            return web.Response(status=403)
        return self.search_response()

    @property
    def url(self):
        return str(self.server.make_url("/"))


@pytest.fixture
async def supermarket():
    servers = []

    async def start(search_response, **kwargs):
        fake = FakeSupermarket(search_response, **kwargs)
        await fake.server.start_server()
        servers.append(fake)
        return fake

    yield start
    for fake in servers:
        await fake.server.close()


def woolworths_json():
    return web.json_response(woolworths_search(woolworths_product(144329, "Brown Onions", 4.50)))


def coles_html():
    return web.Response(
        text=coles_search_page(coles_product(5134236, "Coles", "Brown Onions", 4.00)),
        content_type="text/html",
    )


@pytest.mark.anyio
async def test_woolworths_search_request(supermarket):
    fake = await supermarket(woolworths_json)
    store = Woolworths(base_url=fake.url)
    try:
        products = await store.search("brown onion")
    finally:
        await store.close()

    assert [p.id for p in products] == ["144329"]
    assert fake.home_visits == 1
    [(cookie, body)] = fake.searches
    assert cookie == "visit-1"
    assert body["searchTerm"] == "brown onion"
    assert body["formatObject"] == '{"name": "brown onion"}'


@pytest.mark.anyio
async def test_coles_search_request(supermarket):
    fake = await supermarket(coles_html)
    store = Coles(base_url=fake.url)
    try:
        products = await store.search("brown onion")
    finally:
        await store.close()

    assert [p.name for p in products] == ["Coles Brown Onions"]
    assert fake.searches == [("visit-1", {"q": "brown onion"})]


@pytest.mark.anyio
async def test_blocked_search_retries_with_a_fresh_session(supermarket):
    fake = await supermarket(woolworths_json, block_first_searches=1)
    store = Woolworths(base_url=fake.url)
    try:
        products = await store.search("onion")
    finally:
        await store.close()

    assert len(products) == 1
    assert fake.home_visits == 2


@pytest.mark.anyio
async def test_still_blocked_after_retry_raises(supermarket):
    fake = await supermarket(woolworths_json, block_first_searches=5)
    store = Woolworths(base_url=fake.url)
    try:
        with pytest.raises(StoreBlocked, match="HTTP 403"):
            await store.search("onion")
    finally:
        await store.close()


@pytest.mark.anyio
async def test_results_are_cached(supermarket):
    fake = await supermarket(coles_html)
    store = Coles(base_url=fake.url)
    try:
        await store.search("Onion")
        await store.search("onion")
    finally:
        await store.close()

    assert len(fake.searches) == 1


@pytest.mark.anyio
async def test_coles_challenge_page_counts_as_blocked(supermarket):
    def challenge():
        return web.Response(
            text='<html><script src="/_Incapsula_Resource?x=1"></script></html>',
            content_type="text/html",
        )

    fake = await supermarket(challenge)
    store = Coles(base_url=fake.url)
    try:
        with pytest.raises(StoreBlocked, match="challenge"):
            await store.search("onion")
    finally:
        await store.close()
    assert fake.home_visits == 2


def test_coles_page_data_found_with_other_attributes():
    html = coles_search_page(coles_product(1, "Coles", "Milk", 3.0)).replace(
        '<script id="__NEXT_DATA__" type="application/json">',
        '<script nonce="abc" type="application/json" id="__NEXT_DATA__" crossorigin="">',
    )

    assert _NEXT_DATA_RE.search(html) is not None


@pytest.mark.anyio
async def test_unreachable_store():
    store = Woolworths(base_url="http://127.0.0.1:9")
    try:
        with pytest.raises(StoreError, match="Couldn't reach Woolworths"):
            await store.search("onion")
    finally:
        await store.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"
