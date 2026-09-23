"""CoinGecko price lookups — the universal fallback price source.

Unlike Pyth, CoinGecko has a listing (by coin id or, for Solana/Sui/Ethereum,
by on-chain contract/coin-type address) for almost anything, so it's meant
to sit last in prices.SOURCE_PRIORITY and catch whatever earlier sources
couldn't price.
"""

from typing import Optional

import requests

from . import config

NAME = "CoinGecko"

# CoinGecko's asset platform ids for the chains that support contract-address
# lookups. Bitcoin is intentionally absent: CoinGecko has no asset platform
# for it, so bitcoin watches are always looked up by coin id.
ASSET_PLATFORM = {
    "solana": "solana",
    "sui": "sui",
    "ethereum": "ethereum",
    "hyperliquid": "hyperliquid",
    "robinhood": "robinhood",
}


def _headers() -> dict:
    if config.COINGECKO_API_KEY:
        return {"x-cg-demo-api-key": config.COINGECKO_API_KEY}
    return {}


def get_price(chain: str, ref_type: str, token_ref: str) -> Optional[float]:
    return get_prices(chain, ref_type, [token_ref]).get(token_ref.lower())


def get_prices(chain: str, ref_type: str, token_refs: list[str]) -> dict[str, float]:
    """Fetch USD prices for multiple tokens of the same chain/ref_type in
    one CoinGecko request. Returns a dict keyed by lowercased token_ref."""
    if not token_refs:
        return {}

    if ref_type == "id":
        url = f"{config.COINGECKO_API_BASE}/simple/price"
        params = {"ids": ",".join(token_refs), "vs_currencies": "usd"}
        resp = requests.get(url, params=params, headers=_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return {
            coin_id.lower(): entry["usd"]
            for coin_id, entry in data.items()
            if "usd" in entry
        }

    if ref_type == "contract":
        platform = ASSET_PLATFORM[chain]
        url = f"{config.COINGECKO_API_BASE}/simple/token_price/{platform}"
        params = {"contract_addresses": ",".join(token_refs), "vs_currencies": "usd"}
        resp = requests.get(url, params=params, headers=_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return {
            addr.lower(): entry["usd"]
            for addr, entry in data.items()
            if "usd" in entry
        }

    raise ValueError(f"Unsupported ref_type: {ref_type}")
