from cookidoo_api import CookidooLocalizationConfig

from tests.pantry_fixture import PANTRY
from thermohub.models import Quantity
from thermohub.shopping_list import build_shopping_list

AU = CookidooLocalizationConfig(
    country_code="au", language="en-AU", url="https://cookidoo.com.au/foundation/en-AU"
)


def items_by_name(include_owned: bool = False):
    shopping_list = build_shopping_list(PANTRY, AU, include_owned=include_owned)
    return {item.name: item for item in shopping_list.items}


def test_merges_the_same_ingredient_across_recipes():
    onion = items_by_name()["brown onion"]

    assert onion.ingredient_ref == "com.vorwerk.ingredients.Ingredient-onion"
    assert onion.category_ref == "ShoppingCategory-veg"
    assert onion.quantities == [Quantity(amount=3, unit="")]
    assert onion.display == "3"
    assert [(s.item_id, s.recipe_name) for s in onion.sources] == [
        ("item-onion-1", "Spaghetti bolognese"),
        ("item-onion-2", "French onion soup"),
    ]


def test_sums_ranges():
    garlic = items_by_name()["garlic"]

    assert garlic.quantities == [Quantity(amount=3, amount_max=4, unit="cloves")]
    assert garlic.display == "3-4 cloves"


def test_keeps_different_units_apart():
    oil = items_by_name()["olive oil"]

    assert oil.quantities == [Quantity(amount=20, unit="g"), Quantity(amount=1, unit="tbsp")]
    assert oil.display == "20 g + 1 tbsp"


def test_leaves_out_ticked_off_entries_by_default():
    items = items_by_name()

    assert "Dishwasher tablets" not in items
    # One of the two salt entries is ticked off; only the other still needs buying.
    assert [s.item_id for s in items["salt"].sources] == ["item-salt-2"]
    assert items["salt"].display == "1 pinch"
    assert items["salt"].owned is False


def test_include_owned_returns_everything():
    items = items_by_name(include_owned=True)

    assert items["Dishwasher tablets"].owned is True
    assert items["salt"].display == "2 pinch"
    assert items["salt"].owned is False


def test_free_text_and_extra_items_merge_by_name():
    items = items_by_name()

    toilet_paper = items["Toilet paper"]
    assert [s.item_id for s in toilet_paper.sources] == ["extra-1", "extra-2"]
    assert {s.type for s in toilet_paper.sources} == {"additional"}
    assert toilet_paper.ingredient_ref is None
    assert toilet_paper.quantities == []

    flour = items["350 g self-raising flour"]
    assert flour.ingredient_ref is None
    assert flour.sources[0].recipe_name == "Nan's scones"


def test_optional_flag():
    items = items_by_name()

    assert items["parmesan"].optional is True
    assert items["brown onion"].optional is False


def test_recipes_summary():
    shopping_list = build_shopping_list(PANTRY, AU)

    assert [(r.id, r.name, r.custom) for r in shopping_list.recipes] == [
        ("r100001", "Spaghetti bolognese", False),
        ("r100002", "French onion soup", False),
        ("01K2CVHK2FCUSTOM", "Nan's scones", True),
    ]
    assert shopping_list.recipes[0].url.startswith("https://cookidoo.com.au/")


def test_items_are_sorted_by_name():
    names = [item.name for item in build_shopping_list(PANTRY, AU).items]

    assert names == sorted(names, key=str.casefold)


def test_empty_list():
    shopping_list = build_shopping_list(
        {"recipes": [], "customerRecipes": [], "additionalItems": []}, AU
    )

    assert shopping_list.items == []
    assert shopping_list.recipes == []
