"""PreStocks (tokenized pre-IPO equity) price lookups.

PreStocks issues Solana SPL tokens 1:1-backed by SPV exposure to private
companies (OpenAI, SpaceX, Anthropic, etc). Its API is a single unauthenticated
GET that returns every listed token in one response — no per-token query, no
API key, no pagination — so this source just fetches the whole list and
matches it against whatever token_refs were asked for, by Solana mint
address or by ticker symbol (e.g. "openai", "spacex"), rather than keeping a
static id map like pyth.py needs to.

The API documents no rate limit, but in practice returns 429s within a
couple of calls in quick succession (confirmed 2026-09-22) — likely meant
for occasional dashboard use, not per-poll-cycle fetching. Since it always
returns the full token list regardless of what's asked for, the result is
cached for CACHE_TTL_SECONDS so repeated polls within that window reuse the
same response instead of hitting the API again.
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

NAME = "PreStocks"

API_URL = "https://prestocks.com/api/prestocks"
CACHE_TTL_SECONDS = 60

_cache: Optional[list[dict]] = None
_cache_fetched_at = 0.0


def _fetch_all() -> list[dict]:
    global _cache, _cache_fetched_at
    now = time.monotonic()
    if _cache is not None and (now - _cache_fetched_at) < CACHE_TTL_SECONDS:
        return _cache

    resp = requests.get(API_URL, timeout=15)
    resp.raise_for_status()
    tokens = resp.json()
    _cache = tokens
    _cache_fetched_at = now
    return tokens


def get_prices(chain: str, ref_type: str, token_refs: list[str]) -> dict[str, float]:
    """Fetch USD prices for whichever of token_refs match a PreStocks
    token, by Solana mint address or ticker symbol. Only relevant for
    chain == "solana"; returns {} for any other chain, or if the PreStocks
    API is unreachable, so callers fall back cleanly."""
    if chain != "solana" or not token_refs:
        return {}

    wanted = {ref.lower() for ref in token_refs}

    try:
        tokens = _fetch_all()
    except Exception:
        logger.exception("PreStocks price fetch failed")
        return {}

    result: dict[str, float] = {}
    for token in tokens:
        price = token.get("markPrice")
        if price is None:
            continue
        for key in (token.get("contract_address"), token.get("symbol")):
            if key and key.lower() in wanted:
                result[key.lower()] = price
    return result


def get_price(chain: str, ref_type: str, token_ref: str) -> Optional[float]:
    return get_prices(chain, ref_type, [token_ref]).get(token_ref.lower())
