"""Supermarket connectors."""

from thermohub.stores.base import Store, StoreBlocked, StoreError
from thermohub.stores.coles import Coles
from thermohub.stores.woolworths import Woolworths

__all__ = ["Coles", "Store", "StoreBlocked", "StoreError", "Woolworths"]
