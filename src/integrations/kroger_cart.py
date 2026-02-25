"""Kroger Developer API integration — product search and cart push.

V1 scope: product search (client credentials, no user auth required).
V1.5 scope: cart push (requires user OAuth Authorization Code flow).

API documentation: https://developer.kroger.com/documentation/
Register for credentials: https://developer.kroger.com

Setup:
  1. Register at https://developer.kroger.com
  2. Create an application
  3. Set redirect URI to http://localhost:8080/callback
  4. Copy client_id and client_secret to demo_inputs/kroger_config.json

Authentication:
  - Product search: Client Credentials grant (app-level, no user login)
  - Cart push: Authorization Code grant (user must authorize)
"""

import json
import urllib.request
import urllib.parse
import urllib.error
import base64
import difflib
from pathlib import Path
from typing import Optional


KROGER_TOKEN_URL = "https://api.kroger.com/v1/connect/oauth2/token"
KROGER_PRODUCTS_URL = "https://api.kroger.com/v1/products"
KROGER_CART_URL = "https://api.kroger.com/v1/cart/add"
KROGER_AUTH_URL = "https://api.kroger.com/v1/connect/oauth2/authorize"
KROGER_LOCATIONS_URL = "https://api.kroger.com/v1/locations"


class KrogerAPIError(Exception):
    pass


class KrogerClient:
    """Thin Kroger API client using only stdlib (no requests dependency)."""

    def __init__(self, client_id: str, client_secret: str, location_id: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.location_id = location_id
        self._app_token: Optional[str] = None

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _basic_auth(self) -> str:
        creds = f"{self.client_id}:{self.client_secret}"
        return base64.b64encode(creds.encode()).decode()

    def _get_app_token(self) -> str:
        """Client Credentials grant — app-level token for product search."""
        if self._app_token:
            return self._app_token

        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "scope": "product.compact",
        }).encode()

        req = urllib.request.Request(
            KROGER_TOKEN_URL,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {self._basic_auth()}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read())
                self._app_token = body["access_token"]
                return self._app_token
        except urllib.error.HTTPError as e:
            raise KrogerAPIError(
                f"Token request failed ({e.code}): {e.read().decode()[:200]}"
            ) from e

    def get_auth_url(self, redirect_uri: str, state: str = "meal-planner") -> str:
        """Return OAuth Authorization URL for user to authorize cart access."""
        params = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": "cart.basic:write profile.compact",
            "state": state,
        })
        return f"{KROGER_AUTH_URL}?{params}"

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """Exchange authorization code for user access token."""
        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }).encode()

        req = urllib.request.Request(
            KROGER_TOKEN_URL,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {self._basic_auth()}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise KrogerAPIError(
                f"Token exchange failed ({e.code}): {e.read().decode()[:200]}"
            ) from e

    # ------------------------------------------------------------------
    # Product search
    # ------------------------------------------------------------------

    def search_products(self, term: str, limit: int = 5) -> list:
        """
        Search Kroger products by keyword at the configured location.

        Returns list of product dicts with:
          productId, description, brand, size, price, upc
        """
        token = self._get_app_token()
        params = urllib.parse.urlencode({
            "filter.term": term,
            "filter.locationId": self.location_id,
            "filter.limit": min(limit, 50),
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
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read())
                return body.get("data", [])
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self._app_token = None  # invalidate stale token
                raise KrogerAPIError("Unauthorized — check client_id / client_secret") from e
            raise KrogerAPIError(
                f"Product search failed ({e.code}): {e.read().decode()[:200]}"
            ) from e

    # ------------------------------------------------------------------
    # Cart push (requires user OAuth token)
    # ------------------------------------------------------------------

    def add_to_cart(self, items: list, user_access_token: str) -> dict:
        """
        Add items to the authenticated user's Kroger cart.

        items: list of {"upc": str, "quantity": int}
        Returns API response body.
        """
        payload = json.dumps({"items": items}).encode()
        req = urllib.request.Request(
            KROGER_CART_URL,
            data=payload,
            method="PUT",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {user_access_token}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read()) if resp.read() else {"status": "ok"}
        except urllib.error.HTTPError as e:
            raise KrogerAPIError(
                f"Cart push failed ({e.code}): {e.read().decode()[:200]}"
            ) from e


# ------------------------------------------------------------------
# Matching logic
# ------------------------------------------------------------------

def _extract_price(product: dict) -> Optional[float]:
    """Pull the best available price from a Kroger product dict."""
    items = product.get("items", [])
    for item in items:
        price = item.get("price", {})
        regular = price.get("regular")
        promo = price.get("promo")
        if promo and promo > 0:
            return float(promo)
        if regular and regular > 0:
            return float(regular)
    return None


def _extract_size(product: dict) -> str:
    items = product.get("items", [])
    for item in items:
        size = item.get("size", "")
        if size:
            return size
    return ""


def _extract_upc(product: dict) -> str:
    items = product.get("items", [])
    for item in items:
        upc = item.get("itemId", "")
        if upc:
            return upc
    return product.get("productId", "")


def _match_confidence(query: str, product_description: str) -> float:
    """Compute fuzzy match confidence between query and product name."""
    q = query.lower().strip()
    d = product_description.lower().strip()
    return difflib.SequenceMatcher(None, q, d).ratio()


def resolve_grocery_items(grocery_items: list, client: KrogerClient,
                           verbose: bool = True) -> list:
    """
    Map normalized grocery items to Kroger products.

    For each grocery item:
      - Search Kroger by name_normalized
      - Pick best match by fuzzy similarity
      - Annotate with store_item_name, store_product_id, store_sku, price_usd,
        match_confidence, match_type

    Returns enriched grocery items list (original fields preserved).
    """
    enriched = []
    total = len(grocery_items)

    for idx, item in enumerate(grocery_items):
        name = item.get("name_normalized") or item.get("name_display", "")
        if verbose:
            print(f"  [{idx+1}/{total}] Searching: {name} ...", end=" ", flush=True)

        result = dict(item)  # copy to avoid mutation

        try:
            products = client.search_products(name, limit=5)
        except KrogerAPIError as e:
            if verbose:
                print(f"ERROR ({e})")
            result["match_type"] = "no_match"
            result["match_confidence"] = 0.0
            enriched.append(result)
            continue

        if not products:
            if verbose:
                print("no results")
            result["match_type"] = "no_match"
            result["match_confidence"] = 0.0
            enriched.append(result)
            continue

        # Score each result
        scored = []
        for p in products:
            desc = p.get("description", "")
            confidence = _match_confidence(name, desc)
            scored.append((confidence, p))
        scored.sort(reverse=True, key=lambda x: x[0])

        best_confidence, best_product = scored[0]
        desc = best_product.get("description", "")
        price = _extract_price(best_product)
        size = _extract_size(best_product)
        upc = _extract_upc(best_product)
        product_id = best_product.get("productId", "")

        match_type = (
            "exact" if best_confidence >= 0.85
            else "approximate" if best_confidence >= 0.45
            else "no_match"
        )

        result["store_item_name"] = f"{desc} {size}".strip() if size else desc
        result["store_product_id"] = product_id
        result["store_sku"] = upc
        result["price_usd"] = price
        result["match_confidence"] = round(best_confidence, 3)
        result["match_type"] = match_type

        if verbose:
            price_str = f"${price:.2f}" if price else "no price"
            print(f"{match_type} ({best_confidence:.0%}) → {desc[:40]} {price_str}")

        enriched.append(result)

    return enriched


def build_cart_request(enriched_items: list) -> dict:
    """Build a Kroger cart request payload from enriched grocery items."""
    cart_items = []
    skipped = []

    for item in enriched_items:
        upc = item.get("store_sku") or item.get("store_product_id")
        if not upc or item.get("match_type") == "no_match":
            skipped.append(item.get("name_display", "unknown"))
            continue
        # Kroger cart quantity = number of units (not grams/ml)
        # For demo, always add 1 unit per unique item
        cart_items.append({"upc": upc, "quantity": 1})

    return {
        "items": cart_items,
        "skipped": skipped,
        "total_items": len(cart_items),
        "total_skipped": len(skipped),
    }


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(
            f"Kroger config not found at {config_path}.\n"
            "Copy demo_inputs/kroger_config.json, fill in client_id and client_secret."
        )
    cfg = json.loads(config_path.read_text())
    if cfg.get("client_id") == "YOUR_CLIENT_ID_HERE":
        raise ValueError(
            "Kroger credentials not configured.\n"
            "Register at https://developer.kroger.com and fill in demo_inputs/kroger_config.json."
        )
    return cfg


def run_search(grocery_list_path: Path, config_path: Path,
               out_path: Path, verbose: bool = True) -> Path:
    """
    Load grocery list JSON, search Kroger for each item, write kroger_cart_request.json.
    """
    cfg = load_config(config_path)
    grocery = json.loads(grocery_list_path.read_text())
    items = grocery.get("items", [])

    if verbose:
        print(f"\nKroger product search: {len(items)} items @ {cfg.get('store_chain', 'Kroger')}")
        print(f"Location ID: {cfg['location_id']}")
        print("-" * 50)

    client = KrogerClient(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        location_id=cfg["location_id"],
    )

    enriched = resolve_grocery_items(items, client, verbose=verbose)
    cart_request = build_cart_request(enriched)

    # Estimated total cost
    priced = [i for i in enriched if i.get("price_usd")]
    estimated_total = sum(i["price_usd"] for i in priced)

    result = {
        "week_start": grocery.get("week_start"),
        "store": cfg.get("store_chain", "Fred Meyer"),
        "location_id": cfg["location_id"],
        "estimated_total_usd": round(estimated_total, 2),
        "items_priced": len(priced),
        "items_total": len(items),
        "enriched_items": enriched,
        "cart_payload": cart_request,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))

    if verbose:
        print("-" * 50)
        print(f"  Items resolved: {len(priced)}/{len(items)} with prices")
        print(f"  Estimated total: ${estimated_total:.2f}")
        print(f"  Cart request written → {out_path}")
        if cart_request["skipped"]:
            print(f"  Skipped (no match): {', '.join(cart_request['skipped'])}")

    return out_path


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    run_search(
        grocery_list_path=root / "outputs" / "demo" / "grocery_list.json",
        config_path=root / "demo_inputs" / "kroger_config.json",
        out_path=root / "outputs" / "demo" / "kroger_cart_request.json",
    )
