"""Merge Cookidoo's per-recipe shopping list into one list of things to buy."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from typing import Any

from cookidoo_api import CookidooLocalizationConfig
from cookidoo_api.helpers import cookidoo_recipe_from_json

from thermohub.models import ItemSource, Quantity, RecipeSummary, ShoppingItem, ShoppingList

_LOGGER = logging.getLogger(__name__)


class UnreadableShoppingList(ValueError):
    """Cookidoo's shopping list isn't in a form Thermohub can read: it may have changed."""


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
    recipes = [
        recipe
        for recipe in [*(pantry.get("recipes") or []), *(pantry.get("customerRecipes") or [])]
        if isinstance(recipe, Mapping)
    ]
    groups: dict[str, _Group] = {}
    unreadable = 0

    def add(key: str, group: _Group, source: ItemSource) -> None:
        if include_owned or not source.owned:
            groups.setdefault(key, group).sources.append(source)

    for recipe in recipes:
        for entry in recipe.get("recipeIngredientGroups") or []:
            name, item_id = _text(entry, "ingredientNotation"), _text(entry, "id")
            if name is None or item_id is None:
                unreadable += 1
                continue
            ref = entry.get("ingredient_ref") or None
            group = _Group(name, ref, entry.get("shoppingCategory_ref") or None)
            source = ItemSource(
                item_id=item_id,
                type="recipe",
                recipe_id=_text(recipe, "id"),
                recipe_name=_text(recipe, "title"),
                quantity=_parse_quantity(entry),
                optional=bool(entry.get("optional", False)),
                owned=bool(entry.get("isOwned", False)),
            )
            add(f"ref:{ref}" if ref else _name_key(name), group, source)

    for entry in pantry.get("additionalItems") or []:
        name, item_id = _text(entry, "name"), _text(entry, "id")
        if name is None or item_id is None:
            unreadable += 1
            continue
        source = ItemSource(
            item_id=item_id, type="additional", owned=bool(entry.get("isOwned", False))
        )
        add(_name_key(name), _Group(name), source)

    if unreadable:
        _LOGGER.warning("Skipped %d shopping list entries Thermohub can't read", unreadable)
        if not groups:
            # Not an empty list: that would say there is nothing to buy.
            raise UnreadableShoppingList(f"{unreadable} entries, none readable")

    items = [group.merge() for group in groups.values()]
    items.sort(key=lambda item: item.name.casefold())
    return ShoppingList(
        fetched_at=datetime.now(UTC),
        items=items,
        recipes=_recipe_summaries(recipes, localization),
    )


def _text(entry: object, key: str) -> str | None:
    """A non-blank string field of an entry, or None."""
    value = entry.get(key) if isinstance(entry, Mapping) else None
    return value if isinstance(value, str) and value.strip() else None


def _name_key(name: str) -> str:
    return "name:" + " ".join(name.split()).casefold()


def _parse_quantity(entry: Mapping[str, Any]) -> Quantity | None:
    quantity = entry.get("quantity") or {}
    if not isinstance(quantity, Mapping):
        return None
    unit = str(entry.get("unitNotation") or "").strip()
    try:
        if quantity.get("value"):
            return Quantity(amount=quantity["value"], unit=unit)
        if quantity.get("from") and quantity.get("to"):
            return Quantity(amount=quantity["from"], amount_max=quantity["to"], unit=unit)
    except ValueError:  # not numbers
        pass
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


def _recipe_summaries(
    recipes: list[Mapping[str, Any]], localization: CookidooLocalizationConfig
) -> list[RecipeSummary]:
    summaries = []
    for recipe in recipes:
        try:
            summaries.append(_recipe_summary(recipe, localization))
        except (AttributeError, KeyError, TypeError, ValueError):
            _LOGGER.warning("Left a recipe Thermohub can't read out of the summary", exc_info=True)
    return summaries


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
