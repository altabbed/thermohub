"""Merge Cookidoo's per-recipe shopping list into one list of things to buy."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cookidoo_api import CookidooLocalizationConfig
from cookidoo_api.helpers import cookidoo_recipe_from_json

from thermohub.models import ItemSource, Quantity, RecipeSummary, ShoppingItem, ShoppingList


def build_shopping_list(
    pantry: Mapping[str, Any],
    localization: CookidooLocalizationConfig,
    *,
    include_owned: bool = False,
) -> ShoppingList:
    """Build the merged list from a raw Cookidoo pantry document.

    Entries are merged on Cookidoo's ingredient id where there is one (the
    same "brown onion" in two recipes) and on the name otherwise (extra items,
    and ingredients of user-created recipes, which are free text). Entries
    ticked off in Cookidoo are dropped unless ``include_owned`` is set.
    """
    recipes = [*pantry.get("recipes", []), *pantry.get("customerRecipes", [])]
    groups: dict[str, _Group] = {}

    def add(key: str, group: _Group, source: ItemSource) -> None:
        if include_owned or not source.owned:
            groups.setdefault(key, group).sources.append(source)

    for recipe in recipes:
        for entry in recipe.get("recipeIngredientGroups", []):
            name = entry["ingredientNotation"]
            ref = entry.get("ingredient_ref") or None
            group = _Group(name, ref, entry.get("shoppingCategory_ref") or None)
            source = ItemSource(
                item_id=entry["id"],
                type="recipe",
                recipe_id=recipe["id"],
                recipe_name=recipe.get("title"),
                quantity=_parse_quantity(entry),
                optional=bool(entry.get("optional", False)),
                owned=bool(entry.get("isOwned", False)),
            )
            add(f"ref:{ref}" if ref else _name_key(name), group, source)

    for entry in pantry.get("additionalItems", []):
        source = ItemSource(
            item_id=entry["id"],
            type="additional",
            owned=bool(entry.get("isOwned", False)),
        )
        add(_name_key(entry["name"]), _Group(entry["name"]), source)

    items = [group.merge() for group in groups.values()]
    items.sort(key=lambda item: item.name.casefold())
    return ShoppingList(
        fetched_at=datetime.now(UTC),
        items=items,
        recipes=[_recipe_summary(recipe, localization) for recipe in recipes],
    )


def _name_key(name: str) -> str:
    return "name:" + " ".join(name.split()).casefold()


def _parse_quantity(entry: Mapping[str, Any]) -> Quantity | None:
    quantity = entry.get("quantity") or {}
    unit = (entry.get("unitNotation") or "").strip()
    if quantity.get("value"):
        return Quantity(amount=quantity["value"], unit=unit)
    if quantity.get("from") and quantity.get("to"):
        return Quantity(amount=quantity["from"], amount_max=quantity["to"], unit=unit)
    return None


@dataclass
class _Group:
    name: str
    ingredient_ref: str | None = None
    category_ref: str | None = None
    sources: list[ItemSource] = field(default_factory=list)

    def merge(self) -> ShoppingItem:
        totals: dict[str, Quantity] = {}
        for q in (source.quantity for source in self.sources):
            if q is None:
                continue
            total = totals.get(q.unit.casefold())
            if total is None:
                totals[q.unit.casefold()] = q.model_copy()
                continue
            if total.amount_max is not None or q.amount_max is not None:
                total.amount_max = (total.amount_max or total.amount) + (q.amount_max or q.amount)
            total.amount += q.amount

        quantities = list(totals.values())
        return ShoppingItem(
            name=self.name.strip(),
            ingredient_ref=self.ingredient_ref,
            category_ref=self.category_ref,
            quantities=quantities,
            display=" + ".join(_format_quantity(q) for q in quantities),
            owned=all(source.owned for source in self.sources),
            optional=all(source.optional for source in self.sources),
            sources=self.sources,
        )


def _format_quantity(q: Quantity) -> str:
    amount = f"{q.amount:g}"
    if q.amount_max is not None:
        amount += f"-{q.amount_max:g}"
    return f"{amount} {q.unit}".strip()


def _recipe_summary(
    recipe: Mapping[str, Any], localization: CookidooLocalizationConfig
) -> RecipeSummary:
    parsed = cookidoo_recipe_from_json(recipe, localization)  # type: ignore[arg-type]
    return RecipeSummary(
        id=parsed.id,
        name=parsed.name,
        url=parsed.url,
        thumbnail=parsed.thumbnail,
        custom=bool(recipe.get("isCustomerRecipe", False)),
    )
