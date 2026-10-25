"""Choosing what to buy at a store: how many packs, which products really match, the pick."""

from collections.abc import Sequence
import json
import logging
import math
from pathlib import Path
import re
import time

from thermohub.models import Candidate, Quantity, StoreProduct

_LOGGER = logging.getLogger(__name__)

# How many of a store's search results are considered, and how many are shown.
POOL = 12
SHOWN = 6

# ---- How much the recipes need -------------------------------------------------

# Amounts are worked out in three kinds. Millilitres count as grams: close
# enough for deciding how many packs cover a recipe.
MASS, COUNT, PACK = "mass", "count", "pack"

_UNITS: dict[str, tuple[str, float]] = {
    **dict.fromkeys(["g", "gram", "grams"], (MASS, 1)),
    **dict.fromkeys(["kg", "kilogram", "kilograms"], (MASS, 1000)),
    **dict.fromkeys(["ml", "millilitre", "millilitres"], (MASS, 1)),
    **dict.fromkeys(["l", "litre", "litres", "liter", "liters"], (MASS, 1000)),
    **dict.fromkeys(["tsp", "teaspoon", "teaspoons"], (MASS, 5)),
    **dict.fromkeys(["tbsp", "tablespoon", "tablespoons"], (MASS, 20)),  # Australian tablespoon
    **dict.fromkeys(["cup", "cups"], (MASS, 250)),
    **dict.fromkeys(
        ["", "each", "piece", "pieces", "whole", "large", "medium", "small"], (COUNT, 1)
    ),
    **dict.fromkeys(
        [
            "can",
            "cans",
            "tin",
            "tins",
            "jar",
            "jars",
            "packet",
            "packets",
            "pack",
            "packs",
            "bunch",
            "bunches",
            "bottle",
            "bottles",
        ],
        (PACK, 1),
    ),
}


def need(quantities: Sequence[Quantity]) -> dict[str, float]:
    """What the recipes need, by kind. Units such as cloves or pinches are left out."""
    total: dict[str, float] = {}
    for q in quantities:
        unit = _UNITS.get(q.unit.strip().lower().rstrip("."))
        if unit is None:
            continue
        kind, factor = unit
        total[kind] = total.get(kind, 0) + (q.amount_max or q.amount) * factor
    return total


# ---- How much a product holds ------------------------------------------------------

_MULTI = re.compile(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b", re.IGNORECASE)
_SINGLE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b", re.IGNORECASE)
_COUNT = re.compile(r"(\d+)\s*(?:pack|pk|pieces|piece|ea|each|count|ct)\b", re.IGNORECASE)
_SCALE = {"kg": 1000, "g": 1, "l": 1000, "ml": 1}


def holds(product: StoreProduct) -> dict[str, float]:
    """What one of this product holds, by kind, read from its size."""
    size = product.size or ""
    amount: dict[str, float] = {}
    multi = _MULTI.search(size) or _MULTI.search(product.name)
    single = _SINGLE.search(size) or _SINGLE.search(product.name)
    count = _COUNT.search(size) or _COUNT.search(product.name)
    if multi:
        n, q, unit = int(multi.group(1)), float(multi.group(2)), multi.group(3).lower()
        amount[MASS] = n * q * _SCALE[unit]
        amount[COUNT] = n
    elif single:
        amount[MASS] = float(single.group(1)) * _SCALE[single.group(2).lower()]
    if count:
        amount[COUNT] = int(count.group(1))
    elif "each" in size.lower() and COUNT not in amount:
        amount[COUNT] = 1
    return amount


def packs_needed(needs: dict[str, float], product: StoreProduct) -> int:
    """How many of the product cover what's needed (at least one, at most 24)."""
    has = holds(product)
    packs = 1
    for kind, wanted in needs.items():
        if kind == PACK:
            per_pack = has.get(COUNT, 1) if MASS in has and COUNT in has else 1
        else:
            per_pack = has.get(kind) or 0
        if per_pack > 0 and wanted > 0:
            packs = max(packs, math.ceil(wanted / per_pack - 1e-9))
    return min(packs, 24)


# ---- Is it the right product? -----------------------------------------------------------

_WORD = re.compile(r"[a-z]+")
_STOP = {"of", "and", "the", "a", "an", "with", "fresh", "to", "for", "in", "or"}
# Words that turn an ingredient into a different product: garlic -> garlic bread.
_OTHER_PRODUCT = {
    "bread",
    "powder",
    "salt",
    "sauce",
    "paste",
    "chip",
    "crisp",
    "dip",
    "soup",
    "ring",
    "flavoured",
    "flavour",
    "seasoning",
    "stock",
    "spread",
    "mix",
    "juice",
    "granule",
    "flake",
    "crumb",
    "biscuit",
    "cake",
    "cookie",
    "bar",
    "drink",
    "pie",
    "pizza",
    "dumpling",
    "noodle",
    "chutney",
    "relish",
    "jam",
    "cordial",
    "lolly",
}


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("oes"):
        return word[:-2]
    if len(word) > 4 and word.endswith("es") and word[:-2].endswith(("ch", "sh", "ss", "x")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> set[str]:
    return {_singular(w) for w in _WORD.findall(text.lower()) if w not in _STOP}


def good_match(ingredient: str, product_name: str) -> bool:
    """Has every word of the ingredient and doesn't turn it into something else."""
    want, has = _words(ingredient), _words(product_name)
    return want <= has and not ((has & _OTHER_PRODUCT) - want)


# ---- The pick --------------------------------------------------------------------


def choose(
    ingredient: str,
    needs: dict[str, float],
    products: Sequence[StoreProduct],
    usual: str | None = None,
) -> list[Candidate]:
    """Rank a store's search results for one ingredient, best pick first.

    The pick is the product chosen last time when it turns up again;
    otherwise the cheapest product that really matches, for the amount the
    recipes need; otherwise the store's own top result.
    """
    candidates = []
    for rank, product in enumerate(products[:POOL]):
        packs = packs_needed(needs, product)
        candidates.append(
            (
                rank,
                Candidate(
                    **product.model_dump(),
                    packs=packs,
                    cost=round(product.price * packs, 2),
                    good_match=good_match(ingredient, product.name),
                ),
            )
        )
    if not candidates:
        return []

    good = [c for _, c in candidates if c.good_match]
    usual_pick = next((c for _, c in candidates if usual and c.id == usual), None)
    if usual_pick:
        pick = usual_pick
    elif good:
        pick = min(good, key=lambda c: c.cost)
    else:
        pick = candidates[0][1]

    for _, c in candidates:
        if c is usual_pick:
            c.tags.append("usual")
        if c.on_special:
            c.tags.append("special")
    priced = [c for c in good if c.unit_price]
    if len(priced) > 1:
        per = priced[0].unit_price.per  # type: ignore[union-attr]
        same = [c for c in priced if c.unit_price and c.unit_price.per == per]
        if len(same) > 1:
            min(same, key=lambda c: c.unit_price.price).tags.append("best_value")  # type: ignore[union-attr]

    rest = sorted(
        (rc for rc in candidates if rc[1] is not pick),
        key=lambda rc: (not rc[1].good_match, rc[1].cost if rc[1].good_match else rc[0]),
    )
    return [pick, *(c for _, c in rest)][:SHOWN]


# ---- Remembered choices ------------------------------------------------------------


def item_key(name: str, ingredient_ref: str | None = None) -> str:
    """Stable key for an ingredient: Cookidoo's id when there is one, else the name."""
    if ingredient_ref:
        return f"ref:{ingredient_ref}"
    return "name:" + " ".join(name.split()).casefold()


class Preferences:
    """The product picked for each ingredient at each store, kept in the data volume."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, dict[str, dict[str, object]]] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _LOGGER.warning("Ignoring unreadable preferences file %s", path)

    def usual(self, store: str, key: str) -> str | None:
        chosen = self._data.get(store, {}).get(key)
        return str(chosen["id"]) if chosen else None

    def remember(self, store: str, key: str, product_id: str, name: str) -> None:
        self._data.setdefault(store, {})[key] = {"id": product_id, "name": name, "at": time.time()}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
        tmp.replace(self._path)
