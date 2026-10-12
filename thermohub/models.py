"""API response models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Quantity(BaseModel):
    amount: float = Field(description="Amount, or the lower bound of a range.")
    amount_max: float | None = Field(
        default=None, description="Upper bound when Cookidoo gives a range (e.g. 1-2)."
    )
    unit: str = Field(description='Unit as written by Cookidoo; "" for a plain count.')


class ItemSource(BaseModel):
    """One Cookidoo shopping list entry that contributes to a ShoppingItem."""

    item_id: str = Field(description="Cookidoo's id for this shopping list entry.")
    type: Literal["recipe", "additional"]
    recipe_id: str | None = None
    recipe_name: str | None = None
    quantity: Quantity | None = None
    optional: bool = False
    owned: bool = Field(description="Ticked off in Cookidoo (already have it).")


class ShoppingItem(BaseModel):
    """A thing to buy, merged across every recipe and extra item that needs it."""

    name: str
    ingredient_ref: str | None = Field(
        description="Cookidoo's ingredient id; null for extra items and "
        "ingredients of user-created recipes (free text)."
    )
    category_ref: str | None = Field(description="Cookidoo's shopping category id.")
    quantities: list[Quantity] = Field(
        description="Totals per unit. Entries without a quantity are not counted."
    )
    display: str = Field(description='Human-readable total, e.g. "300 g + 2".')
    owned: bool = Field(description="True when every source is ticked off.")
    optional: bool = Field(description="True when every recipe lists it as optional.")
    sources: list[ItemSource]


class RecipeSummary(BaseModel):
    id: str
    name: str
    url: str
    thumbnail: str | None = None
    custom: bool = Field(description="A recipe the user created in Cookidoo.")


class ShoppingList(BaseModel):
    fetched_at: datetime
    items: list[ShoppingItem]
    recipes: list[RecipeSummary]


StoreId = Literal["woolworths", "coles"]


class UnitPrice(BaseModel):
    price: float = Field(description="Price per 1 kg, 1 L or 1 each.")
    per: Literal["kg", "L", "each"]
    text: str = Field(description='As the store shows it, e.g. "$13.57 per 1kg".')


class StoreProduct(BaseModel):
    store: StoreId
    id: str = Field(description="The store's product id (Woolworths stockcode, Coles id).")
    name: str
    size: str | None = None
    price: float = Field(description="Shelf price for one.")
    was_price: float | None = None
    on_special: bool = False
    unit_price: UnitPrice | None = None
    image: str | None = None
    url: str


class StoreMatch(BaseModel):
    search_term: str
    candidates: list[StoreProduct] = Field(
        description="Available products in the store's relevance order; the first is the pick."
    )
    error: str | None = Field(default=None, description="Why the search failed, if it did.")


class CompareItem(BaseModel):
    name: str
    matches: dict[StoreId, StoreMatch]
    cheaper: StoreId | None = Field(
        description="Store whose first candidate is cheaper; null on a tie or if either is missing."
    )


class StoreTotal(BaseModel):
    store: StoreId
    total: float = Field(description="Sum of picks for the items found at every store.")
    found: int = Field(description="How many of the items this store has.")


class Comparison(BaseModel):
    items: list[CompareItem]
    totals: list[StoreTotal]
    split_total: float = Field(description="Buying each item wherever it is cheaper.")
    compared: int = Field(description="Items found at every store, which the totals cover.")
    cheapest: StoreId | None


class CompareRequest(BaseModel):
    items: list[str] = Field(min_length=1, max_length=60)


class Check(BaseModel):
    ok: bool
    detail: str


class StoreStatus(BaseModel):
    id: StoreId
    name: str
    prices: Check
    account: Check


class Status(BaseModel):
    cookidoo: Check
    stores: list[StoreStatus]
