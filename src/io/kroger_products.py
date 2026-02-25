"""Kroger product search and cart stub.

V1 scope: product search (client credentials, no user auth required).
V1.5 scope: cart push (requires user OAuth Authorization Code flow — stub only).

API documentation: https://developer.kroger.com/documentation/
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from src.io.kroger_auth import get_token

logger = logging.getLogger(__name__)

KROGER_PRODUCTS_URL = "https://api.kroger.com/v1/products"
KROGER_CART_URL = "https://api.kroger.com/v1/cart/add"


def search_product(name: str, location_id: str = None) -> list:
    """Search Kroger product catalog by keyword at a given store location.

    Args:
        name:        Search term (ingredient name, e.g. "salmon fillet").
        location_id: Kroger location ID. Defaults to KROGER_LOCATION_ID env var,
                     then falls back to 02400688 (Fred Meyer Seattle).

    Returns:
        List of up to 5 product dicts:
          {product_id, description, price, upc, aisle_location}
        Returns empty list if token unavailable or search fails.
    """
    if location_id is None:
        location_id = os.environ.get("KROGER_LOCATION_ID", "02400688").strip()

    token = get_token()
    if not token:
        return []

    params = urllib.parse.urlencode({
        "filter.term": name,
        "filter.locationId": location_id,
        "filter.limit": 5,
    })
    url = f"{KROGER_PRODUCTS_URL}?{params}"

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("Kroger product search failed (%s): %s", e.code, e.read().decode()[:200])
        return []
    except Exception as exc:
        logger.warning("Kroger product search error: %s", exc)
        return []

    results = []
    for p in body.get("data", []):
        items = p.get("items", [])
        price = None
        upc = None
        aisle = None
        for item in items:
            price_obj = item.get("price", {})
            promo = price_obj.get("promo")
            regular = price_obj.get("regular")
            if promo and promo > 0:
                price = float(promo)
            elif regular and regular > 0 and price is None:
                price = float(regular)
            if not upc:
                upc = item.get("itemId", "")

        fulfillment = p.get("fulfillment", {})
        aisle = fulfillment.get("aisleDescriptions", [{}])[0].get("description") \
                if fulfillment.get("aisleDescriptions") else None

        results.append({
            "product_id": p.get("productId", ""),
            "description": p.get("description", ""),
            "price": price,
            "upc": upc or p.get("productId", ""),
            "aisle_location": aisle,
        })

    return results


def add_to_cart(items: list[dict]) -> bool:
    """Stub: log cart payload and return True.

    Production cart push requires an Authorization Code (user-scoped) token.
    The client_credentials token used for product search does not grant cart access.

    To implement production cart push:
      1. Implement OAuth Authorization Code flow (user visits Kroger login page)
      2. Exchange code for user access token
      3. Use user token as Bearer in the PUT /cart/add request

    Args:
        items: List of {"upc": str, "quantity": int} dicts.

    Returns:
        True (stub always succeeds).
    """
    payload = {"items": items}
    logger.info(
        "Kroger cart stub — would PUT to %s with payload: %s",
        KROGER_CART_URL,
        json.dumps(payload),
    )
    print(f"  [Kroger cart stub] {len(items)} items would be added to cart.")
    print(f"  Payload: {json.dumps(payload, indent=2)}")
    print("  Note: Production cart push requires user OAuth Authorization Code token.")
    return True
