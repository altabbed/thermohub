"""Choosing what to buy at a store: how many packs, which products really match, the pick."""

from collections.abc import Sequence
import json
import logging
import math
from pathlib import Path
import re
import time

from thermohub.models import Candidate, Quantity, StoreProduct, UnitPrice

_LOGGER = logging.getLogger(__name__)

# How many of a store's search results are considered, and how many are
# returned: every size and brand that matches, then a few other results.
POOL = 40
SHOWN = 24

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
# Loose fruit and veg are sold one at a time with a rough weight:
# "Coles Bananas | approx 170g" is one banana.
_APPROX = re.compile(r"\bapprox(?:\.|imately)?(?=\W|$)", re.IGNORECASE)


def holds(product: StoreProduct) -> dict[str, float]:
    """What one of this product holds, by kind, read from its size."""
    size = product.size or ""
    if _APPROX.search(size) or _APPROX.search(product.name):
        single = _SINGLE.search(size) or _SINGLE.search(product.name)
        if single:
            return {COUNT: 1, MASS: float(single.group(1)) * _SCALE[single.group(2).lower()]}
        return {COUNT: 1}
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


def each_grams(products: Sequence[StoreProduct]) -> float | None:
    """The typical weight of one item, from those sold singly with a weight
    ("approx 170g"), so a bag's count can be estimated. None if none are."""
    weights = sorted(h[MASS] for h in map(holds, products) if h.get(COUNT) == 1 and h.get(MASS))
    return weights[len(weights) // 2] if weights else None


def estimate_count(has: dict[str, float], typical: float | None) -> dict[str, float]:
    """Add a count to something sold by weight: a 1 kg bag of 170 g bananas holds about 6."""
    if typical and MASS in has and COUNT not in has:
        return {**has, COUNT: max(1, round(has[MASS] / typical))}
    return has


def cost_of(product: StoreProduct, packs: int) -> float:
    """What that many cost, with the multi-buy deal for each full set of them."""
    deal = product.deal
    if deal and packs >= deal.quantity:
        sets, rest = divmod(packs, deal.quantity)
        return round(sets * deal.price + rest * product.price, 2)
    return round(product.price * packs, 2)


def packs_needed(
    needs: dict[str, float], product: StoreProduct, has: dict[str, float] | None = None
) -> int:
    """How many of the product cover what's needed (at least one, at most 24)."""
    has = holds(product) if has is None else has
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


# Words a product may use instead of the ingredient's own: toilet paper is
# sold as toilet tissue.
_SYNONYMS = {
    "paper": {"tissue"},
    "chilli": {"chili"},
    "yoghurt": {"yogurt"},
    "mince": {"minced"},
    "coriander": {"cilantro"},
}


def good_match(ingredient: str, product_name: str) -> bool:
    """Has every word of the ingredient and doesn't turn it into something else."""
    want, has = _words(ingredient), _words(product_name)
    covered = all(w in has or has & _SYNONYMS.get(w, set()) for w in want)
    return covered and not ((has & _OTHER_PRODUCT) - want)


# ---- Value for money ----------------------------------------------------------------


def value_basis(
    needs: dict[str, float], products: Sequence[StoreProduct], typical: float | None = None
) -> str:
    """Compare value per item or per kg/L.

    By what the recipes count in when they say; otherwise by how the products
    are sold: per item when most come in packs of several (toilet paper,
    eggs), per kg/L otherwise (onions by the kilo or each).
    """
    if MASS in needs:
        return MASS
    if COUNT in needs:
        return COUNT
    held = [estimate_count(holds(p), typical) for p in products]
    counted = sum(1 for h in held if h.get(COUNT, 0) > 1)
    weighed = sum(1 for h in held if MASS in h)
    return COUNT if counted and counted >= weighed else MASS


def unit_value(
    product: StoreProduct, has: dict[str, float], basis: str, price: float | None = None
) -> UnitPrice | None:
    """The product's price per item or per kg/L, or None when its size can't be read.

    ``price`` is what one actually costs when it differs from the shelf price
    (bought with a multi-buy deal).
    """
    price = product.price if price is None else price
    if basis == COUNT:
        count = has.get(COUNT)
        if not count:
            return None
        each = price / count
        return UnitPrice(price=round(each, 4), per="each", text=f"${each:.2f} each")
    if product.unit_price and product.unit_price.per in ("kg", "L") and price == product.price:
        return product.unit_price
    grams = has.get(MASS)
    if not grams:
        return None
    per_kg = price / grams * 1000
    return UnitPrice(price=round(per_kg, 4), per="kg", text=f"${per_kg:.2f} per kg")


def _value(candidate: Candidate) -> float:
    return candidate.value.price if candidate.value else math.inf


# ---- The pick --------------------------------------------------------------------


def choose(
    ingredient: str,
    needs: dict[str, float],
    products: Sequence[StoreProduct],
    usual: str | None = None,
    usual_packs: int | None = None,
    basis: str | None = None,
    typical: float | None = None,
) -> list[Candidate]:
    """Rank a store's search results for one ingredient, best pick first.

    The pick is the product chosen last time when it turns up again. Otherwise,
    when there's an amount to buy, the cheapest real match that covers it;
    when there isn't, the real match that's the best value per item or kg.
    Failing all that, the store's own top result. ``typical`` is the weight
    of one item, for estimating how many are in a bag.
    """
    pool = list(products[:POOL])
    if not pool:
        return []
    matches = [good_match(ingredient, p.name) for p in pool]
    good_pool = [p for p, ok in zip(pool, matches, strict=True) if ok]
    typical = typical or each_grams(good_pool)
    basis = basis or value_basis(needs, good_pool or pool, typical)
    candidates = []
    for product, ok in zip(pool, matches, strict=True):
        read = holds(product)
        has = estimate_count(read, typical) if ok else read
        packs = packs_needed(needs, product, has)
        cost = cost_of(product, packs)
        candidates.append(
            Candidate(
                **product.model_dump(),
                packs=packs,
                cost=cost,
                good_match=ok,
                holds=has,
                estimated=has is not read,
                value=unit_value(product, has, basis, round(cost / packs, 4)),
            )
        )

    good = [c for c in candidates if c.good_match]
    usual_pick = next((c for c in candidates if usual and c.id == usual), None)
    if usual_pick:
        pick = usual_pick
        if not needs and usual_packs:  # bought by the pack, not for a recipe: same as last time
            pick.packs = usual_packs
            pick.cost = cost_of(pick, usual_packs)
    elif good and needs:
        pick = min(good, key=lambda c: (c.cost, _value(c)))
    elif good:
        pick = min(good, key=lambda c: (_value(c), c.cost))
    else:
        pick = candidates[0]

    for c in candidates:
        if c is usual_pick:
            c.tags.append("usual")
        if c.on_special:
            c.tags.append("special")
        if c.deal:
            c.tags.append("deal")
    valued = [c for c in good if c.value]
    if len(valued) > 1:
        min(valued, key=_value).tags.append("best_value")

    def order(c: Candidate) -> tuple[bool, float, float]:
        if not c.good_match:
            return (True, 0, 0)  # keeps the store's order
        return (False, c.cost, _value(c)) if needs else (False, _value(c), c.cost)

    rest = sorted((c for c in candidates if c is not pick), key=order)
    return [pick, *rest][:SHOWN]


def common_amount(picks: Sequence[Candidate]) -> dict[str, float]:
    """The amount to price at every store when the recipes don't say how much.

    What the product chosen last time holds (times how many), else what the
    best-value pick holds, so the stores are compared for the same amount:
    24 rolls at both, not 24 at one and 12 at the other.
    """
    usual = [c for c in picks if "usual" in c.tags]
    for c in usual or sorted((c for c in picks if c.value), key=_value):
        if c.value:
            kind = COUNT if c.value.per == "each" else MASS
        else:
            kind = COUNT if COUNT in c.holds else MASS
        amount = c.holds.get(kind)
        if amount:
            return {kind: amount * c.packs}
    return {}


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

    def usual_packs(self, store: str, key: str) -> int | None:
        """How many were bought last time, for items the recipes don't measure."""
        packs = self._data.get(store, {}).get(key, {}).get("packs")
        return packs if isinstance(packs, int) and 1 <= packs <= 24 else None

    def remember(
        self, store: str, key: str, product_id: str, name: str, packs: int | None = None
    ) -> None:
        chosen: dict[str, object] = {"id": product_id, "name": name, "at": time.time()}
        if packs:
            chosen["packs"] = packs
        self._data.setdefault(store, {})[key] = chosen
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
        tmp.replace(self._path)
