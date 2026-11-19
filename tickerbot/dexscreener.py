"""DexScreener price lookups for Solana and Sui tokens by contract address.

DexScreener aggregates live on-chain DEX pair data, so prices here reflect
actual current swap rates rather than an aggregator's cached value — useful
for tokens (like many Sui SPL-equivalents) CoinGecko only refreshes
occasionally. No API key needed.

Only handles ref_type == "contract" on chain in (solana, sui): DexScreener
has no coin-id lookup, only contract/coin-type addresses. id-type refs and
other chains fall through to the next source untouched.
"""

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

NAME = "DexScreener"

API_BASE = "https://api.dexscreener.com"
_SUPPORTED_CHAINS = {"solana", "sui"}


def get_prices(chain: str, ref_type: str, token_refs: list[str]) -> dict[str, float]:
    """Fetch USD prices for whichever of token_refs are contract/coin-type
    addresses on a supported chain. Returns a dict keyed by lowercased
    token_ref. A token listed on multiple DEX pairs uses whichever pair has
    the most USD liquidity, as the more reliable price."""
    if chain not in _SUPPORTED_CHAINS or ref_type != "contract" or not token_refs:
        return {}

    addrs = ",".join(token_refs)
    try:
        resp = requests.get(f"{API_BASE}/tokens/v1/{chain}/{addrs}", timeout=15)
        resp.raise_for_status()
        pairs = resp.json()
    except Exception:
        logger.exception("DexScreener price fetch failed for %s: %s", chain, token_refs)
        return {}

    if not isinstance(pairs, list):
        return {}

    wanted_lower = {ref.lower() for ref in token_refs}
    best_liquidity: dict[str, float] = {}
    result: dict[str, float] = {}
    for pair in pairs:
        base = pair.get("baseToken") or {}
        addr_lower = (base.get("address") or "").lower()
        if addr_lower not in wanted_lower:
            continue
        price_usd = pair.get("priceUsd")
        if price_usd is None:
            continue
        try:
            price = float(price_usd)
        except (TypeError, ValueError):
            continue
        liquidity = (pair.get("liquidity") or {}).get("usd") or 0
        if addr_lower not in best_liquidity or liquidity > best_liquidity[addr_lower]:
            best_liquidity[addr_lower] = liquidity
            result[addr_lower] = price

    return result


def get_price(chain: str, ref_type: str, token_ref: str) -> Optional[float]:
    return get_prices(chain, ref_type, [token_ref]).get(token_ref.lower())
