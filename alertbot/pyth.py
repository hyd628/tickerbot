"""Pyth Network (Hermes) price feeds.

Pyth identifies assets by opaque feed IDs (e.g. "Crypto.SOL/USD"), not by
chain + contract address like CoinGecko, so there's no general way to
resolve an arbitrary token reference to a feed automatically. FEED_IDS is a
small, manually-verified map from (chain, token_ref) — accepting both a
CoinGecko-style coin id and the token's known contract/coin-type address —
to the feed's id on Hermes. Anything not in this map isn't looked up here;
callers should fall back to another source (see prices.get_prices).

As of the August 2026 Pyth Core upgrade, Hermes requires an API key
(config.PYTH_API_KEY) for the price-update endpoint even though feed
discovery/search remains open. A free trial key is available by signing up
at Pyth Terminal (https://terminal.pyth.network); sustained production use
may require a paid Pyth Pro plan. If no key is configured, or Hermes is
unreachable/unauthorized, get_prices() simply returns no results so callers
fall back cleanly rather than breaking.

Note: the free trial key's asset whitelist is a fixed list of major
crypto/equity/FX feeds (confirmed 2026-09-22) rather than full Hermes
coverage — notably it does NOT include Crypto.SUI/USD, so every Sui watch
always falls back to CoinGecko regardless of key. FEED_IDS only lists
assets confirmed to be on that whitelist.
"""

import logging
from typing import Optional

import requests

from . import config

logger = logging.getLogger(__name__)

NAME = "Pyth"

HERMES_BASE = "https://hermes.pyth.network"

# (chain, token_ref.lower()) -> Hermes feed id (hex, no "0x" prefix).
# Verified against https://hermes.pyth.network/v2/price_feeds on 2026-09-22.
# Restricted to assets confirmed present on the free trial key's whitelist.
FEED_IDS: dict[tuple[str, str], str] = {
    # Native SOL: CoinGecko id "solana", or the wrapped-SOL mint address.
    ("solana", "solana"): "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    ("solana", "so11111111111111111111111111111111111111112"):
        "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    # JitoSOL: CoinGecko id "jito-staked-sol", or its mint address.
    ("solana", "jito-staked-sol"): "67be9f519b95cf24338801051f9a808eff0a578ccb388db73b7f6fe1de019ffb",
    ("solana", "j1toso1uck3rlmjorhttrvwy9hj7x8v9yyac6y7kgcpn"):
        "67be9f519b95cf24338801051f9a808eff0a578ccb388db73b7f6fe1de019ffb",
    # PYTH (Pyth Network's own token): CoinGecko id "pyth-network", or its mint address.
    ("solana", "pyth-network"): "0bbf28e9a841a1cc788f6a361b17ca072d0ea3098a1e5df1c3922d06719579ff",
    ("solana", "hz1jovnivvgrgniiyveozevgz58xau3rkwx8eacqbct3"):
        "0bbf28e9a841a1cc788f6a361b17ca072d0ea3098a1e5df1c3922d06719579ff",
    # Native BTC: CoinGecko id "bitcoin" (bitcoin chain is always id-type, no contracts).
    ("bitcoin", "bitcoin"): "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    # Native ETH: CoinGecko id "ethereum" (the native asset has no contract address).
    ("ethereum", "ethereum"): "ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
}


def has_feed(chain: str, token_ref: str) -> bool:
    return (chain, token_ref.lower()) in FEED_IDS


def _headers() -> dict:
    if config.PYTH_API_KEY:
        return {"Authorization": f"Bearer {config.PYTH_API_KEY}"}
    return {}


def get_prices(chain: str, ref_type: str, token_refs: list[str]) -> dict[str, float]:
    """Fetch USD prices for whichever of token_refs have a known Pyth feed
    for this chain. Returns a dict keyed by lowercased token_ref; refs with
    no known feed, or if the request fails for any reason (no API key,
    unauthorized, network error), are simply absent from the result.

    ref_type is accepted only to match the common price-source interface
    (see prices.py) — Pyth's feed map doesn't distinguish id vs contract."""
    if not config.PYTH_API_KEY:
        return {}

    feed_id_by_ref: dict[str, str] = {}
    for ref in token_refs:
        ref_lower = ref.lower()
        feed_id = FEED_IDS.get((chain, ref_lower))
        if feed_id:
            feed_id_by_ref[ref_lower] = feed_id
    if not feed_id_by_ref:
        return {}

    unique_feed_ids = sorted(set(feed_id_by_ref.values()))
    try:
        resp = requests.get(
            f"{HERMES_BASE}/v2/updates/price/latest",
            params={"ids[]": unique_feed_ids},
            headers=_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("Pyth Hermes price fetch failed for feed ids %s", unique_feed_ids)
        return {}

    price_by_feed_id: dict[str, float] = {}
    for item in data.get("parsed", []):
        feed_id = item.get("id", "").lower().removeprefix("0x")
        price_data = item.get("price") or {}
        try:
            price_by_feed_id[feed_id] = int(price_data["price"]) * (10 ** int(price_data["expo"]))
        except (KeyError, ValueError, TypeError):
            logger.warning("Unexpected Pyth price payload for feed %s: %s", feed_id, price_data)

    result: dict[str, float] = {}
    for ref_lower, feed_id in feed_id_by_ref.items():
        price = price_by_feed_id.get(feed_id.lower().removeprefix("0x"))
        if price is not None:
            result[ref_lower] = price
    return result


def get_price(chain: str, ref_type: str, token_ref: str) -> Optional[float]:
    return get_prices(chain, ref_type, [token_ref]).get(token_ref.lower())
