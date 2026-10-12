import pytest

from thermohub.compare import compare, search_term
from thermohub.models import StoreProduct
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


def product(store, pid, price):
    return StoreProduct(store=store, id=pid, name=pid, price=price, url=f"https://{store}/{pid}")


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

    result = await compare(["onion", "garlic", "saffron"], [woolworths, coles])

    onion, garlic, saffron = result.items
    assert onion.cheaper == "coles"
    assert garlic.cheaper == "woolworths"
    assert len(garlic.matches["woolworths"].candidates) == 5
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

    await compare(["350 g self-raising flour"], [woolworths, coles])

    assert woolworths.terms == coles.terms == ["self-raising flour"]


@pytest.mark.anyio
async def test_compare_reports_a_failed_store():
    woolworths = FakeStore("woolworths", {"onion": [product("woolworths", "w-onion", 4.50)]})
    coles = FakeStore("coles", {}, fail="Coles blocked the request (HTTP 403)")

    result = await compare(["onion"], [woolworths, coles])

    [onion] = result.items
    assert onion.matches["coles"].error == "Coles blocked the request (HTTP 403)"
    assert onion.cheaper is None
    assert result.compared == 0
    assert result.cheapest is None


@pytest.mark.anyio
async def test_equal_totals_have_no_cheapest():
    woolworths = FakeStore("woolworths", {"onion": [product("woolworths", "w", 4.00)]})
    coles = FakeStore("coles", {"onion": [product("coles", "c", 4.00)]})

    result = await compare(["onion"], [woolworths, coles])

    assert result.items[0].cheaper is None
    assert result.cheapest is None


@pytest.fixture
def anyio_backend():
    return "asyncio"
