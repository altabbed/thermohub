import pytest

from thermohub.compare import compare, search_term
from thermohub.models import CompareRequestItem, Quantity, StoreProduct
from thermohub.smarts import Preferences
from thermohub.stores import StoreError


@pytest.mark.parametrize(
    ("name", "term"),
    [
        ("brown onion", "brown onion"),
        ("350 g self-raising flour", "self-raising flour"),
        ("1 ¼ - 2 ¼ cloves of garlic", "garlic"),
        ("2 tbsp olive oil, extra virgin", "olive oil"),
        ("Toilet paper", "Toilet paper"),
        ("4 eggs", "eggs"),
    ],
)
def test_search_term(name, term):
    assert search_term(name) == term


def product(store, pid, price, name=None, size=None):
    return StoreProduct(
        store=store, id=pid, name=name or pid, size=size, price=price, url=f"https://{store}/{pid}"
    )


def items(*names):
    return [CompareRequestItem(name=n) for n in names]


class FakeStore:
    def __init__(self, store_id, catalogue, fail=None):
        self.id = store_id
        self.name = store_id.title()
        self.catalogue = catalogue
        self.fail = fail
        self.terms = []

    async def search(self, term):
        self.terms.append(term)
        if self.fail:
            raise StoreError(self.fail)
        return self.catalogue.get(term, [])


@pytest.mark.anyio
async def test_compare_totals_items_found_at_both_stores():
    woolworths = FakeStore(
        "woolworths",
        {
            "onion": [product("woolworths", "w-onion", 4.50)],
            "garlic": [product("woolworths", f"w-garlic-{i}", 1.0 + i) for i in range(8)],
            "saffron": [product("woolworths", "w-saffron", 12.00)],
        },
    )
    coles = FakeStore(
        "coles",
        {
            "onion": [product("coles", "c-onion", 4.00)],
            "garlic": [product("coles", "c-garlic", 1.20)],
        },
    )

    result = await compare(items("onion", "garlic", "saffron"), [woolworths, coles])

    onion, garlic, saffron = result.items
    assert onion.cheaper == "coles"
    assert garlic.cheaper == "woolworths"
    assert len(garlic.matches["woolworths"].candidates) == 6
    assert saffron.cheaper is None
    assert saffron.matches["coles"].candidates == []
    # Saffron is only at Woolworths, so it stays out of the totals.
    assert result.compared == 2
    assert {t.store: (t.total, t.found) for t in result.totals} == {
        "woolworths": (5.50, 3),
        "coles": (5.20, 2),
    }
    assert result.split_total == 5.00
    assert result.cheapest == "coles"


@pytest.mark.anyio
async def test_compare_searches_with_cleaned_terms():
    woolworths = FakeStore("woolworths", {})
    coles = FakeStore("coles", {})

    await compare(items("350 g self-raising flour"), [woolworths, coles])

    assert woolworths.terms == coles.terms == ["self-raising flour"]


@pytest.mark.anyio
async def test_compare_reports_a_failed_store():
    woolworths = FakeStore("woolworths", {"onion": [product("woolworths", "w-onion", 4.50)]})
    coles = FakeStore("coles", {}, fail="Coles blocked the request (HTTP 403)")

    result = await compare(items("onion"), [woolworths, coles])

    [onion] = result.items
    assert onion.matches["coles"].error == "Coles blocked the request (HTTP 403)"
    assert onion.cheaper is None
    assert result.compared == 0
    assert result.cheapest is None


@pytest.mark.anyio
async def test_equal_totals_have_no_cheapest():
    woolworths = FakeStore("woolworths", {"onion": [product("woolworths", "w", 4.00)]})
    coles = FakeStore("coles", {"onion": [product("coles", "c", 4.00)]})

    result = await compare(items("onion"), [woolworths, coles])

    assert result.items[0].cheaper is None
    assert result.cheapest is None


@pytest.mark.anyio
async def test_compare_prices_the_amount_needed():
    woolworths = FakeStore(
        "woolworths",
        {
            "crushed tomatoes": [
                product("woolworths", "w-tin", 0.90, "Crushed Tomatoes", "400g"),
                product("woolworths", "w-big", 2.50, "Crushed Tomatoes Value Pack", "4 x 400g"),
            ]
        },
    )
    coles = FakeStore(
        "coles", {"crushed tomatoes": [product("coles", "c-tin", 1.00, "Crushed Tomatoes", "400g")]}
    )
    request = [
        CompareRequestItem(
            name="crushed tomatoes", key="ref:tomato", quantities=[Quantity(amount=800, unit="g")]
        )
    ]

    result = await compare(request, [woolworths, coles])

    [item] = result.items
    pick = item.matches["woolworths"].candidates[0]
    assert (pick.id, pick.packs, pick.cost) == ("w-tin", 2, 1.80)
    assert item.matches["coles"].candidates[0].cost == 2.00
    assert {t.store: t.total for t in result.totals} == {"woolworths": 1.80, "coles": 2.00}
    assert item.key == "ref:tomato"


@pytest.mark.anyio
async def test_compare_uses_remembered_choices(tmp_path):
    preferences = Preferences(tmp_path / "preferences.json")
    preferences.remember("woolworths", "name:onion", "w-fancy", "Fancy onion")
    woolworths = FakeStore(
        "woolworths",
        {"onion": [product("woolworths", "w-cheap", 1.0), product("woolworths", "w-fancy", 3.0)]},
    )
    coles = FakeStore("coles", {"onion": [product("coles", "c-onion", 2.0)]})

    result = await compare(items("onion"), [woolworths, coles], preferences)

    pick = result.items[0].matches["woolworths"].candidates[0]
    assert pick.id == "w-fancy"
    assert pick.tags == ["usual"]
    assert result.items[0].cheaper == "coles"


@pytest.fixture
def anyio_backend():
    return "asyncio"
