"""Multi-source price lookups for Solana, Sui, Ethereum, Bitcoin,
Hyperliquid and Robinhood (tokenized stocks).

Each price source (pyth.py, coingecko.py, ...) implements the same
interface — get_prices(chain, ref_type, token_refs) -> dict[token_ref
lowercased -> price] — and only returns the subset of token_refs it can
actually price; anything it can't is simply absent from its result. This
module tries each source in SOURCE_PRIORITY order, keeping the first price
found for each token and asking only about the still-unresolved ones as it
moves down the list, so most tokens end up fetched from exactly one source
even though several might be able to price it.

To add a source: give it get_prices() and a NAME, register it in _SOURCES,
and add its key to SOURCE_PRIORITY wherever it should rank.

Tokens can be referenced either by a CoinGecko coin id (e.g. "solana",
"sui", "ethereum", "bitcoin", "bonk") or, for Solana/Sui/Ethereum, by their
on-chain contract / coin-type address. Bitcoin has no general
token-contract standard, so it's id-only.
"""

import re
from dataclasses import dataclass
from typing import Optional

from . import coingecko, prestocks, pyth

# Sources are tried in this order for every lookup; the first one that can
# price a given token wins. CoinGecko is last because it's the only source
# with near-universal coverage — earlier sources only need to cover the
# subset of assets they specifically know about.
_SOURCES = {
    "pyth": pyth,
    "prestocks": prestocks,
    "coingecko": coingecko,
}
SOURCE_PRIORITY = ["pyth", "prestocks", "coingecko"]

# Chain names/aliases a user can type, normalized to a canonical chain id.
CHAIN_ALIASES = {
    "solana": "solana",
    "sol": "solana",
    "sui": "sui",
    "ethereum": "ethereum",
    "eth": "ethereum",
    "bitcoin": "bitcoin",
    "btc": "bitcoin",
    "hyperliquid": "hyperliquid",
    "hl": "hyperliquid",
    "robinhood": "robinhood",
    "hood": "robinhood",
}

_SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
# HyperCore (Hyperliquid's native spot/perp layer) asset ids are shorter than
# a standard EVM address — e.g. HYPE's own id is 0x0d01dc56dcaaca66ad901c959b4011ec.
_HYPERLIQUID_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{32}$")


@dataclass(frozen=True)
class PriceResult:
    price: float
    source: str  # human-readable label, e.g. "Pyth", "CoinGecko"


def normalize_chain(raw: str) -> Optional[str]:
    """Map user input (e.g. "eth", "BTC") to a canonical chain id, or None
    if it isn't recognized."""
    return CHAIN_ALIASES.get(raw.strip().lower())


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
    if chain == "ethereum":
        if _ETH_ADDRESS_RE.match(token_ref):
            return "contract"
        return "id"
    if chain == "bitcoin":
        # No token-contract standard to look up on Bitcoin; always a coin id.
        return "id"
    if chain == "hyperliquid":
        if _HYPERLIQUID_ADDRESS_RE.match(token_ref):
            return "contract"
        return "id"
    if chain == "robinhood":
        # Robinhood's own tokenized-stock platform uses standard EVM-style
        # addresses (distinct from Backed Finance's xStocks, which are
        # ordinary Solana/Ethereum SPL/ERC-20 tokens watchable via those
        # chains already).
        if _ETH_ADDRESS_RE.match(token_ref):
            return "contract"
        return "id"
    raise ValueError(f"Unsupported chain: {chain}")


def get_price(chain: str, ref_type: str, token_ref: str) -> PriceResult:
    """Fetch a single token's current USD price, tagged with which source
    it came from."""
    results = get_prices(chain, ref_type, [token_ref])
    if token_ref.lower() not in results:
        raise LookupError(f"No price found for {chain}:{token_ref}")
    return results[token_ref.lower()]


def get_prices(chain: str, ref_type: str, token_refs: list[str]) -> dict[str, PriceResult]:
    """Fetch USD prices for multiple tokens of the same chain/ref_type,
    trying each source in SOURCE_PRIORITY order. Returns a dict keyed by
    lowercased token_ref; a token with no price from any source is simply
    absent from the result."""
    if not token_refs:
        return {}

    result: dict[str, PriceResult] = {}
    remaining = list(token_refs)
    for source_name in SOURCE_PRIORITY:
        if not remaining:
            break
        source = _SOURCES[source_name]
        fetched = source.get_prices(chain, ref_type, remaining)
        for ref_lower, price in fetched.items():
            result[ref_lower] = PriceResult(price=price, source=source.NAME)
        remaining = [ref for ref in remaining if ref.lower() not in result]
    return result
