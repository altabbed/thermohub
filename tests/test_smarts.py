import pytest

from thermohub.models import Quantity, StoreProduct, UnitPrice
from thermohub.smarts import (
    Preferences,
    choose,
    good_match,
    holds,
    item_key,
    need,
    packs_needed,
)


def product(pid, name, price, size=None, unit=None, special=False):
    return StoreProduct(
        store="woolworths",
        id=pid,
        name=name,
        size=size,
        price=price,
        on_special=special,
        unit_price=UnitPrice(price=unit[0], per=unit[1], text="") if unit else None,
        url=f"https://example.com/{pid}",
    )


def q(amount, unit="", amount_max=None):
    return Quantity(amount=amount, unit=unit, amount_max=amount_max)


def test_need_adds_up_known_units():
    assert need([q(20, "g"), q(1, "tbsp")]) == {"mass": 40}
    assert need([q(1, "kg"), q(500, "ml")]) == {"mass": 1500}
    assert need([q(2, "", 3)]) == {"count": 3}
    assert need([q(2, "tins")]) == {"pack": 2}
    assert need([q(4, "cloves"), q(1, "pinch")]) == {}


@pytest.mark.parametrize(
    ("size", "name", "expected"),
    [
        ("1kg", "Brown Onions", {"mass": 1000}),
        ("750mL", "Olive Oil", {"mass": 750}),
        ("4 x 400g", "Tomatoes", {"mass": 1600, "count": 4}),
        ("each", "Garlic", {"count": 1}),
        ("12 pack", "Free Range Eggs", {"count": 12}),
        (None, "Coconut Milk 400ml", {"mass": 400}),
    ],
)
def test_holds(size, name, expected):
    assert holds(product("p", name, 1.0, size)) == expected


@pytest.mark.parametrize(
    ("needs", "size", "packs"),
    [
        ({"mass": 800}, "400g", 2),
        ({"mass": 40}, "125g", 1),
        ({"count": 3}, "each", 3),
        ({"count": 3}, "1kg", 1),  # can't tell how many onions are in a bag
        ({"count": 2}, "12 pack", 1),
        ({"pack": 2}, "400g", 2),
        ({"pack": 2}, "4 x 400g", 1),
        ({}, "1kg", 1),
        ({"mass": 100_000}, "100g", 24),
    ],
)
def test_packs_needed(needs, size, packs):
    assert packs_needed(needs, product("p", "x", 1.0, size)) == packs


@pytest.mark.parametrize(
    ("ingredient", "name", "ok"),
    [
        ("brown onion", "Woolworths Brown Onions Prepacked", True),
        ("crushed tomatoes", "Woolworths Crushed Tomato", True),
        ("garlic", "Garlic Bread", False),
        ("garlic", "Garlic Salt", False),
        ("onion", "Onion Rings", False),
        ("green curry paste", "Valcom Green Curry Paste", True),
        ("bananas", "Cavendish Bananas", True),
        ("toilet paper", "Quilton Toilet Tissue", False),
    ],
)
def test_good_match(ingredient, name, ok):
    assert good_match(ingredient, name) is ok


def test_choose_prefers_cheapest_real_match_for_the_amount():
    products = [
        product("bag", "Brown Onions Prepacked", 4.50, "1kg"),
        product("rings", "Onion Rings", 1.00, "100g"),
        product("loose", "Brown Onions Loose", 0.75, "each"),
    ]

    ranked = choose("brown onion", {"count": 2}, products)

    assert [c.id for c in ranked] == ["loose", "bag", "rings"]
    assert (ranked[0].packs, ranked[0].cost) == (2, 1.50)
    assert ranked[2].good_match is False


def test_choose_falls_back_to_the_store_order():
    products = [product("a", "Quilton Toilet Tissue", 9.50), product("b", "Kleenex Tissue", 8.0)]

    assert choose("toilet paper", {}, products)[0].id == "a"


def test_choose_tags():
    products = [
        product("a", "Olive Oil", 13.0, "750mL", unit=(17.33, "L"), special=True),
        product("b", "Olive Oil", 9.0, "1L", unit=(9.0, "L")),
    ]

    ranked = choose("olive oil", {}, products, usual="a")

    assert ranked[0].id == "a"
    assert ranked[0].tags == ["usual", "special"]
    assert ranked[1].tags == ["best_value"]


def test_item_key():
    assert item_key("Brown  Onion", "com.vorwerk.ingredients.Ingredient-1") == (
        "ref:com.vorwerk.ingredients.Ingredient-1"
    )
    assert item_key("  Toilet  Paper ") == "name:toilet paper"


def test_preferences_survive_a_restart(tmp_path):
    path = tmp_path / "data" / "preferences.json"
    Preferences(path).remember("coles", "ref:x", "123", "Coles Milk")

    assert Preferences(path).usual("coles", "ref:x") == "123"
    assert Preferences(path).usual("woolworths", "ref:x") is None
