import pytest

from thermohub.models import Quantity, StoreProduct, UnitPrice
from thermohub.smarts import (
    Preferences,
    choose,
    common_amount,
    good_match,
    holds,
    item_key,
    need,
    packs_needed,
    value_basis,
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
        ("toilet paper", "Quilton Toilet Tissue 3 Ply", True),
        ("toilet paper", "Sorbent Toilet Paper", True),
        ("toilet paper", "Viva Paper Towel", False),
        ("toilet paper", "Harpic Toilet Cleaner", False),
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
    products = [product("a", "Finish Quantum Ultimate", 20.0), product("b", "Fairy Platinum", 18.0)]

    assert choose("dishwasher tablets", {}, products)[0].id == "a"


def toilet_paper(pid, rolls, price, brand="Quilton Toilet Tissue 3 Ply"):
    return product(pid, f"{brand} {rolls} Pack", price, f"{rolls} pack")


def test_choose_best_value_when_the_recipes_dont_say_how_much():
    products = [
        toilet_paper("12", 12, 9.50),
        toilet_paper("24", 24, 14.00),
        toilet_paper("36", 36, 20.00),
    ]

    ranked = choose("toilet paper", {}, products)

    assert [c.id for c in ranked] == ["36", "24", "12"]
    assert ranked[0].tags == ["best_value"]
    assert ranked[0].value.text == "$0.56 each"
    assert ranked[0].holds == {"count": 36}
    assert (ranked[0].packs, ranked[0].cost) == (1, 20.00)


def test_choose_for_an_amount_compares_the_cost_of_that_amount():
    products = [
        toilet_paper("12", 12, 9.50),
        toilet_paper("24", 24, 14.00),
        toilet_paper("36", 36, 20.00),
    ]

    ranked = choose("toilet paper", {"count": 24}, products)

    assert [(c.id, c.packs, c.cost) for c in ranked] == [
        ("24", 1, 14.00),
        ("12", 2, 19.00),
        ("36", 1, 20.00),
    ]


def test_choose_remembers_how_many_for_unmeasured_items():
    products = [toilet_paper("12", 12, 9.50), toilet_paper("24", 24, 14.00)]

    pick = choose("toilet paper", {}, products, usual="12", usual_packs=2)[0]
    assert (pick.id, pick.packs, pick.cost, pick.tags) == ("12", 2, 19.00, ["usual"])
    # A recipe amount wins over last time's count.
    pick = choose("toilet paper", {"count": 12}, products, usual="12", usual_packs=2)[0]
    assert (pick.id, pick.packs) == ("12", 1)


@pytest.mark.parametrize(
    ("needs", "sizes", "basis"),
    [
        ({}, ["12 pack", "24 pack", "1kg"], "count"),
        ({}, ["1kg", "each", "2kg"], "mass"),
        ({"mass": 800}, ["12 pack", "24 pack"], "mass"),
        ({"count": 3}, ["1kg", "2kg"], "count"),
    ],
)
def test_value_basis(needs, sizes, basis):
    assert value_basis(needs, [product(str(i), "x", 1.0, s) for i, s in enumerate(sizes)]) == basis


def test_common_amount_is_the_best_value_pick():
    woolworths = choose("toilet paper", {}, [toilet_paper("w36", 36, 20.00)])[0]
    coles = choose("toilet paper", {}, [toilet_paper("c24", 24, 13.00)])[0]

    assert common_amount([woolworths, coles]) == {"count": 24}  # Coles' 54c a roll beats 56c


def test_common_amount_follows_the_usual_pick():
    usual = choose("toilet paper", {}, [toilet_paper("12", 12, 9.5)], usual="12", usual_packs=2)[0]
    other = choose("toilet paper", {}, [toilet_paper("c36", 36, 15.00)])[0]

    assert common_amount([other, usual]) == {"count": 24}


def test_common_amount_unknown():
    assert common_amount(choose("milk", {}, [product("m", "Milk", 3.0)])) == {}


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
    Preferences(path).remember("coles", "name:toilet paper", "tp", "Quilton", packs=2)

    assert Preferences(path).usual("coles", "ref:x") == "123"
    assert Preferences(path).usual_packs("coles", "ref:x") is None
    assert Preferences(path).usual("woolworths", "ref:x") is None
    assert Preferences(path).usual_packs("coles", "name:toilet paper") == 2
