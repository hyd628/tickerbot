"""CoinGecko price lookups for Solana and Sui tokens.

Tokens can be referenced either by a CoinGecko coin id (e.g. "solana",
"sui", "bonk") or by their on-chain contract / coin-type address, looked
up via CoinGecko's asset-platform token_price endpoint.
"""

import re

import requests

from . import config

# CoinGecko's asset platform ids for the chains we support.
ASSET_PLATFORM = {
    "solana": "solana",
    "sui": "sui",
}

_SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def guess_ref_type(chain: str, token_ref: str) -> str:
    """Guess whether a user-supplied token reference is a contract address
    or a CoinGecko coin id, based on its shape."""
    if chain == "sui":
        if token_ref.startswith("0x") or "::" in token_ref:
            return "contract"
        return "id"
    if chain == "solana":
        if _SOLANA_ADDRESS_RE.match(token_ref):
            return "contract"
        return "id"
    raise ValueError(f"Unsupported chain: {chain}")


def _headers() -> dict:
    if config.COINGECKO_API_KEY:
        return {"x-cg-demo-api-key": config.COINGECKO_API_KEY}
    return {}


def get_price(chain: str, ref_type: str, token_ref: str) -> float:
    """Fetch a single token's current USD price."""
    prices = get_prices(chain, ref_type, [token_ref])
    if token_ref.lower() not in prices:
        raise LookupError(f"No price found for {chain}:{token_ref}")
    return prices[token_ref.lower()]


def get_prices(chain: str, ref_type: str, token_refs: list[str]) -> dict[str, float]:
    """Fetch USD prices for multiple tokens of the same chain/ref_type in
    one request. Returns a dict keyed by lowercased token_ref."""
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
