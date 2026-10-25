"""API response models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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


class Deal(BaseModel):
    """A multi-buy offer: `quantity` of the product for `price` together."""

    quantity: int = Field(ge=2)
    price: float = Field(gt=0, description="What `quantity` of them cost together.")
    text: str = Field(description='As the store words it, e.g. "Pick any 2 for $13".')


class StoreProduct(BaseModel):
    store: StoreId
    id: str = Field(description="The store's product id (Woolworths stockcode, Coles id).")
    name: str
    size: str | None = None
    price: float = Field(description="Shelf price for one.")
    was_price: float | None = None
    on_special: bool = False
    unit_price: UnitPrice | None = None
    deal: Deal | None = None
    image: str | None = None
    url: str


Tag = Literal["usual", "best_value", "special", "deal"]


class Candidate(StoreProduct):
    """A product that could be bought for an ingredient, priced for the amount needed."""

    packs: int = Field(default=1, description="How many to buy to cover the recipes.")
    cost: float = Field(description="Price for that many, with any multi-buy deal they meet.")
    good_match: bool = Field(
        description="Has every word of the ingredient and isn't a different product."
    )
    tags: list[Tag] = Field(
        default_factory=list,
        description="usual: picked last time; best_value: lowest price per kg/L/each among "
        "real matches; special: on special; deal: has a multi-buy deal.",
    )
    holds: dict[Literal["mass", "count"], float] = Field(
        default_factory=dict,
        description="What one holds, read from its size: grams (or ml) and/or a count "
        "(24 for a 24 pack; 1 for a single item of about 170 g).",
    )
    estimated: bool = Field(
        default=False,
        description="The count is a guess from the weight: a 1 kg bag of bananas holds "
        "about 6 when loose ones weigh about 170 g.",
    )
    value: UnitPrice | None = Field(
        default=None,
        description="Price per each or per kg/L, the same basis for every candidate of the "
        "item, so they can be compared for value.",
    )


class StoreMatch(BaseModel):
    search_term: str
    candidates: list[Candidate] = Field(description="The recommended pick first.")
    error: str | None = Field(default=None, description="Why the search failed, if it did.")


class Amount(BaseModel):
    kind: Literal["mass", "count", "pack"] = Field(
        description="mass: grams (ml count as grams); count: items; pack: tins, jars and such."
    )
    value: float


class CompareItem(BaseModel):
    name: str
    key: str = Field(description="Stable key for the ingredient, used to remember choices.")
    amount: Amount | None = Field(
        default=None,
        description="The amount priced at every store: what the recipes need, or when they "
        "don't say, what the usual or best-value product holds. Null when unknown.",
    )
    matches: dict[StoreId, StoreMatch]
    cheaper: StoreId | None = Field(
        description="Store whose pick costs less; null on a tie or if either is missing."
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


class CompareRequestItem(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    key: str | None = Field(default=None, description="Defaults to one made from the name.")
    quantities: list[Quantity] = Field(
        default_factory=list, description="What the recipes need, to work out pack counts."
    )


class CompareRequest(BaseModel):
    items: list[CompareRequestItem] = Field(min_length=1, max_length=60)

    @field_validator("items", mode="before")
    @classmethod
    def _names_are_items(cls, items: object) -> object:
        # A plain list of names is accepted too.
        if isinstance(items, list):
            return [{"name": i} if isinstance(i, str) else i for i in items]
        return items


class ChoiceRequest(BaseModel):
    """Remember a product as the one to buy for an ingredient at a store."""

    store: StoreId
    key: str = Field(min_length=1, max_length=300)
    product_id: str = Field(min_length=1, max_length=64)
    name: str = Field(max_length=300)
    quantity: int | None = Field(
        default=None, ge=1, le=24, description="How many, for items the recipes don't measure."
    )


ConnectionState = Literal["connected", "not_set_up", "needs_attention", "unavailable"]


class Connection(BaseModel):
    """One account or service, described for the person using the page."""

    id: Literal["cookidoo", "woolworths", "coles"]
    name: str
    state: ConnectionState = Field(
        description="connected; not_set_up (nothing entered yet); needs_attention (the user "
        "has to do something, e.g. sign in again); unavailable (not working right now, "
        "nothing for the user to fix)."
    )
    summary: str = Field(description="One plain-language sentence about the state.")
    detail: str | None = Field(default=None, description="Technical reason, when there is one.")
    note: str | None = Field(default=None, description="Anything else worth knowing.")
    signed_in: bool | None = Field(
        default=None, description="Stores: whether Thermohub is signed in to the account."
    )


class Status(BaseModel):
    connections: list[Connection]


class Region(BaseModel):
    country: str = Field(description="Cookidoo country code, e.g. au.")
    language: str = Field(description="Cookidoo language, e.g. en-AU.")
    site: str = Field(description="The Cookidoo website for this region.")


class CookidooAccount(BaseModel):
    signed_in: bool
    email: str | None
    country: str
    language: str
    automatic: bool = Field(
        description="Signed in with details set in the container settings rather than the page."
    )


class SignInRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    country: str = Field(min_length=2, max_length=8)
    language: str = Field(min_length=2, max_length=16)


class CartLineRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=8, max_length=2000, description="The product's page at the store.")
    quantity: int = Field(default=1, ge=1, le=24)
    key: str | None = Field(default=None, description="Ingredient key, to remember the product.")


class CartOrderRequest(BaseModel):
    store: StoreId
    lines: list[CartLineRequest] = Field(min_length=1, max_length=60)


class CartRequest(BaseModel):
    orders: list[CartOrderRequest] = Field(min_length=1, max_length=2)


class StoreAccount(BaseModel):
    """Whether Thermohub's browser is signed in to a store, so it can fill the cart."""

    id: StoreId
    name: str
    signed_in: bool
    cart_url: str


class CartLineView(BaseModel):
    product_id: str
    name: str
    url: str
    quantity: int
    key: str | None = None
    status: Literal["waiting", "adding", "added", "failed", "skipped"]
    message: str | None = None
    screenshot: str | None = Field(
        default=None, description="data: URL of what the page looked like when adding failed."
    )


class CartOrderView(BaseModel):
    store: StoreId
    name: str
    state: Literal["waiting", "running", "done", "needs_sign_in", "failed"]
    cart_url: str
    message: str | None = None
    lines: list[CartLineView]


class CartJobView(BaseModel):
    id: str
    finished: bool
    orders: list[CartOrderView]
