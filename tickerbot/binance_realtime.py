"""Real-time BTC/ETH/SOL prices via Binance's public WebSocket ticker stream.

Binance pushes a fresh ticker roughly once a second per symbol, with no API
key and no rate limit that matters here -- the closest thing to instant price
awareness available for these assets without a paid feed. This module is used
two ways:

1. As an ordinary price source (get_prices()): it just serves whatever price
   last arrived over the socket, with no network call at lookup time.
2. As the actual point of this file: every time a fresh price arrives, it's
   immediately checked against every active limit alert (target_price watch)
   on that asset via the same alerts.check_target_watch() the slow poll loop
   uses -- so a limit alert on BTC/ETH/SOL can fire within about a second of
   the price crossing, instead of waiting up to a full poll interval.

Only native BTC/ETH/SOL (ref_type == "id") are covered here; Binance has no
contract-address lookup, and most assets this bot watches (Sui coins,
tokenized stocks, etc.) aren't traded on Binance at all.
"""

import asyncio
import json
import logging
import time

import websockets
from telegram import Bot

from . import alerts, db

logger = logging.getLogger(__name__)

NAME = "Binance"

# (chain, coingecko id) -> Binance ticker symbol (lowercase, as used in the
# stream name). Extend this if more native assets need instant coverage.
_SYMBOL_MAP = {
    ("bitcoin", "bitcoin"): "btcusdt",
    ("ethereum", "ethereum"): "ethusdt",
    ("solana", "solana"): "solusdt",
}
_SYMBOLS = sorted(set(_SYMBOL_MAP.values()))

_STREAM_URL = "wss://stream.binance.com:9443/stream?streams=" + "/".join(
    f"{symbol}@ticker" for symbol in _SYMBOLS
)

# If the socket hasn't produced a price for this long, treat the cache as too
# stale to serve -- better to fall through to the next source than quietly
# hand back a frozen price from a dead connection.
_STALE_SECONDS = 30
_RECONNECT_DELAY_SECONDS = 5

# symbol -> (price, time.monotonic() timestamp of when it arrived)
_latest: dict[str, tuple[float, float]] = {}


def get_prices(chain: str, ref_type: str, token_refs: list[str]) -> dict[str, float]:
    """Serve whatever price last arrived over the socket for whichever of
    token_refs this covers, as long as the socket has been heard from
    recently; no network call."""
    if ref_type != "id" or not token_refs:
        return {}

    now = time.monotonic()
    result: dict[str, float] = {}
    for ref in token_refs:
        symbol = _SYMBOL_MAP.get((chain, ref.lower()))
        if symbol is None:
            continue
        cached = _latest.get(symbol)
        if cached is None:
            continue
        price, seen_at = cached
        if now - seen_at > _STALE_SECONDS:
            continue
        result[ref.lower()] = price
    return result


async def _check_limit_alerts(bot: Bot, chain: str, coin_id: str, price: float) -> None:
    """Fire any active limit alerts on this asset right away, instead of
    waiting for the next poll cycle to notice. Safe to race against
    monitor.py's poll loop: check_target_watch() is one-shot (deletes the
    watch when it fires), so whichever path gets there first wins."""
    for watch in db.all_watches():
        if (
            watch["target_price"] is not None
            and watch["chain"] == chain
            and watch["ref_type"] == "id"
            and watch["token_ref"].lower() == coin_id
        ):
            try:
                await alerts.check_target_watch(bot, watch, price, NAME)
            except Exception:
                logger.exception("Failed to process real-time limit alert for watch %s", watch["id"])


async def _handle_message(bot: Bot, raw: str) -> None:
    try:
        envelope = json.loads(raw)
        payload = envelope["data"]
        symbol = payload["s"].lower()
        price = float(payload["c"])
    except (KeyError, ValueError, TypeError):
        logger.warning("Unexpected Binance ticker payload: %s", raw[:200])
        return

    _latest[symbol] = (price, time.monotonic())

    for (chain, coin_id), mapped_symbol in _SYMBOL_MAP.items():
        if mapped_symbol == symbol:
            await _check_limit_alerts(bot, chain, coin_id, price)


async def run_forever(bot: Bot) -> None:
    """Hold a persistent connection to Binance's combined ticker stream,
    reconnecting on any drop. Launched once as a background task from
    bot.py's post_init hook, for the life of the process."""
    while True:
        try:
            async with websockets.connect(_STREAM_URL, ping_interval=20) as ws:
                logger.info("Connected to Binance WebSocket (%s)", ", ".join(_SYMBOLS))
                async for raw in ws:
                    await _handle_message(bot, raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Binance WebSocket connection dropped, reconnecting in %ss", _RECONNECT_DELAY_SECONDS
            )
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)
