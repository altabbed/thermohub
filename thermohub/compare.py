"""Price a set of shopping list items at every store and say where it's cheaper."""

import asyncio
from collections.abc import Sequence
import re
from typing import NamedTuple

from thermohub.models import (
    Amount,
    CompareItem,
    CompareRequestItem,
    Comparison,
    StoreId,
    StoreMatch,
    StoreProduct,
    StoreTotal,
)
from thermohub.smarts import (
    COUNT,
    MASS,
    PACK,
    POOL,
    Preferences,
    choose,
    common_amount,
    each_grams,
    good_match,
    item_key,
    need,
    value_basis,
)
from thermohub.stores import Store, StoreError

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


async def compare(
    items: Sequence[CompareRequestItem],
    stores: Sequence[Store],
    preferences: Preferences | None = None,
) -> Comparison:
    async def search(store: Store, item: CompareRequestItem) -> _Found:
        term = search_term(item.name)
        try:
            return _Found(term, await store.search(term))
        except StoreError as e:
            return _Found(term, [], str(e))

    found = await asyncio.gather(*(search(store, item) for item in items for store in stores))
    compared = []
    for i, item in enumerate(items):
        key = item.key or item_key(item.name)
        results = {store.id: found[i * len(stores) + j] for j, store in enumerate(stores)}

        needs = need(item.quantities)
        by_store = _pick(results, needs, key, preferences)
        if not needs:
            # Nothing measured (toilet paper): price the same amount at every store.
            needs = common_amount([m.candidates[0] for m in by_store.values() if m.candidates])
            if needs:
                by_store = _pick(results, needs, key, preferences)
        compared.append(
            CompareItem(
                name=item.name,
                key=key,
                amount=_amount(needs),
                matches=by_store,
                cheaper=_cheaper(by_store),
            )
        )
    return summarise(compared, [store.id for store in stores])


class _Found(NamedTuple):
    term: str
    products: list[StoreProduct]
    error: str | None = None


def _pick(
    results: dict[StoreId, _Found],
    needs: dict[str, float],
    key: str,
    preferences: Preferences | None,
) -> dict[StoreId, StoreMatch]:
    # Value is judged on one basis (per item or per kg) at every store, and
    # bags are counted with the same typical item weight.
    term = next(iter(results.values())).term
    pooled = [p for r in results.values() for p in r.products[:POOL]]
    good = [p for p in pooled if good_match(term, p.name)]
    typical = each_grams(good)
    basis = value_basis(needs, good or pooled, typical)
    return {
        store_id: StoreMatch(
            search_term=r.term,
            candidates=choose(
                r.term,
                needs,
                r.products,
                preferences.usual(store_id, key) if preferences else None,
                preferences.usual_packs(store_id, key) if preferences else None,
                basis,
                typical,
            ),
            error=r.error,
        )
        for store_id, r in results.items()
    }


def _amount(needs: dict[str, float]) -> Amount | None:
    for kind in (MASS, COUNT, PACK):
        if kind in needs:
            return Amount(kind=kind, value=round(needs[kind], 3))  # type: ignore[arg-type]
    return None


def summarise(items: list[CompareItem], store_ids: list[StoreId]) -> Comparison:
    """Totals over the items every store has, so the stores are compared like for like."""
    totals = dict.fromkeys(store_ids, 0.0)
    found = dict.fromkeys(store_ids, 0)
    split_total = 0.0
    compared = 0
    for item in items:
        picks = {s: m.candidates[0].cost for s, m in item.matches.items() if m.candidates}
        for store_id in picks:
            found[store_id] += 1
        if len(picks) == len(store_ids):
            compared += 1
            split_total += min(picks.values())
            for store_id, cost in picks.items():
                totals[store_id] += cost

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
    picks = {s: m.candidates[0].cost for s, m in matches.items() if m.candidates}
    if len(picks) < len(matches) or len(set(picks.values())) < len(picks):
        return None
    return min(picks, key=picks.__getitem__)
