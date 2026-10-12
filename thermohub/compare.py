"""Price a set of shopping list items at every store and say where it's cheaper."""

import asyncio
from collections.abc import Sequence
import re

from thermohub.models import CompareItem, Comparison, StoreId, StoreMatch, StoreTotal
from thermohub.stores import Store, StoreError

CANDIDATES = 5

# Ingredients of user-created recipes are free text such as "350 g self-raising
# flour"; the quantity and unit would only confuse a store search.
_LEADING_QUANTITY = re.compile(
    r"^[\d\s.,/¼½¾⅓⅔-]+"
    r"(?:(?:g|kg|ml|l|tsp|tbsp|cups?|pinch(?:es)?|cloves?|pieces?|cans?|tins?|bunch(?:es)?"
    r"|slices?|sprigs?|handfuls?|stalks?|sticks?|sheets?|packets?|pkts?)\b\.?\s*)?"
    r"(?:of\s+)?",
    re.IGNORECASE,
)


def search_term(name: str) -> str:
    term = _LEADING_QUANTITY.sub("", name).split(",")[0]
    return " ".join(term.split()) or name.strip()


async def compare(names: Sequence[str], stores: Sequence[Store]) -> Comparison:
    async def match(store: Store, term: str) -> StoreMatch:
        try:
            products = await store.search(term)
        except StoreError as e:
            return StoreMatch(search_term=term, candidates=[], error=str(e))
        return StoreMatch(search_term=term, candidates=products[:CANDIDATES])

    terms = [search_term(name) for name in names]
    matches = await asyncio.gather(*(match(store, term) for term in terms for store in stores))
    items = []
    for i, name in enumerate(names):
        by_store = {store.id: matches[i * len(stores) + j] for j, store in enumerate(stores)}
        items.append(CompareItem(name=name, matches=by_store, cheaper=_cheaper(by_store)))
    return summarise(items, [store.id for store in stores])


def summarise(items: list[CompareItem], store_ids: list[StoreId]) -> Comparison:
    """Totals over the items every store has, so the stores are compared like for like."""
    totals = dict.fromkeys(store_ids, 0.0)
    found = dict.fromkeys(store_ids, 0)
    split_total = 0.0
    compared = 0
    for item in items:
        picks = {s: m.candidates[0].price for s, m in item.matches.items() if m.candidates}
        for store_id in picks:
            found[store_id] += 1
        if len(picks) == len(store_ids):
            compared += 1
            split_total += min(picks.values())
            for store_id, price in picks.items():
                totals[store_id] += price

    ranked = sorted(store_ids, key=lambda s: totals[s])
    cheapest = None
    if compared and len(ranked) > 1 and round(totals[ranked[0]], 2) < round(totals[ranked[1]], 2):
        cheapest = ranked[0]
    return Comparison(
        items=items,
        totals=[StoreTotal(store=s, total=round(totals[s], 2), found=found[s]) for s in store_ids],
        split_total=round(split_total, 2),
        compared=compared,
        cheapest=cheapest,
    )


def _cheaper(matches: dict[StoreId, StoreMatch]) -> StoreId | None:
    picks = {s: m.candidates[0].price for s, m in matches.items() if m.candidates}
    if len(picks) < len(matches) or len(set(picks.values())) < len(picks):
        return None
    return min(picks, key=picks.__getitem__)
