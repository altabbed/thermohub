"""Store search responses, shaped like real woolworths.com.au / coles.com.au data."""

import json


def woolworths_product(stockcode, name, price, *, was=None, size="1kg", cup=None, available=True):
    cup_price, cup_measure = cup or (price, "1KG")
    return {
        "Stockcode": stockcode,
        "Name": name,
        "DisplayName": f"{name} {size}",
        "UrlFriendlyName": name.lower().replace(" ", "-"),
        "Price": price,
        "WasPrice": was if was is not None else price,
        "IsOnSpecial": was is not None,
        "IsAvailable": available,
        "PackageSize": size,
        "CupPrice": cup_price,
        "CupMeasure": cup_measure,
        "CupString": f"${cup_price:.2f} / {cup_measure}",
        "MediumImageFile": f"https://cdn0.woolworths.media/content/wowproductimages/medium/{stockcode}.jpg",
    }


def woolworths_search(*products):
    # Each result tile is a group whose own Products list starts with the shown product.
    return {
        "SearchResultsCount": len(products),
        "Products": [{"Name": p["Name"], "Products": [p]} for p in products],
    }


def coles_product(
    pid, brand, name, price, *, was=0, size="1kg", unit=None, available=True, ad=None
):
    unit_price, measure_qty, measure_unit = unit or (price, 1, "kg")
    return {
        "_type": "PRODUCT",
        "id": pid,
        "adId": ad,
        "name": name,
        "brand": brand,
        "size": size,
        "availability": available,
        "imageUris": [{"altText": "", "type": "default", "uri": f"/{str(pid)[0]}/{pid}.jpg"}],
        "pricing": {
            "now": price,
            "was": was,
            "unit": {
                "quantity": 1,
                "ofMeasureQuantity": measure_qty,
                "ofMeasureUnits": measure_unit,
                "price": unit_price,
                "isWeighted": False,
            },
            "comparable": f"${unit_price:.2f} per {measure_qty}{measure_unit}",
            "promotionType": "EVERYDAY",
            "onlineSpecial": False,
        },
    }


def coles_search_page(*results):
    data = {
        "buildId": "20261001.01_v5.0.0",
        "props": {
            "pageProps": {"searchResults": {"noOfResults": len(results), "results": list(results)}}
        },
    }
    return (
        "<!DOCTYPE html><html><head><title>Search | Coles</title></head><body>"
        '<div id="__next"></div>'
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
        "</body></html>"
    )
