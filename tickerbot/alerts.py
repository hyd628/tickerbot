"""Shared alert message formatting + firing logic.

Used by both the periodic poll loop (monitor.py) and the real-time push
path (binance_realtime.py), so there's one source of truth for what an
alert looks like and when a limit alert actually fires -- and so a watch
can never be double-fired by both paths racing each other: whichever one
calls check_target_watch() first deletes the row (one-shot), so the other
path's next lookup simply won't find it anymore.
"""

import html
import logging

from telegram import Bot

from . import db

logger = logging.getLogger(__name__)


async def send_threshold_alert(
    bot: Bot, watch, current: float, pct_change: float, source: str
) -> None:
    direction = "\U0001F4C8" if pct_change >= 0 else "\U0001F4C9"
    # Labels/addresses are user-supplied (and real Sui coin types can contain
    # "<", ">", "&" from generic type params), so escape before embedding in
    # an HTML-parsed message.
    name = html.escape(watch["label"] or watch["token_ref"])
    chain = html.escape(watch["chain"])
    text = (
        f"{direction} <b>{name}</b> ({chain})\n"
        f"Price: <code>${current:.6g}</code> ({pct_change:+.2f}% since last alert)\n"
        f"Source: {html.escape(source)}\n"
        f"Threshold: {watch['threshold_pct']}%  |  watch id: {watch['chat_seq']}"
    )
    await bot.send_message(chat_id=watch["chat_id"], text=text, parse_mode="HTML")


async def send_target_alert(bot: Bot, watch, current: float, source: str) -> None:
    name = html.escape(watch["label"] or watch["token_ref"])
    chain = html.escape(watch["chain"])
    verb = "risen to" if watch["target_direction"] == "above" else "fallen to"
    text = (
        f"\U0001F3AF <b>{name}</b> ({chain})\n"
        f"Price has {verb} <code>${current:.6g}</code>, past your target of "
        f"<code>${watch['target_price']:.6g}</code>\n"
        f"Source: {html.escape(source)}\n"
        f"This was a one-shot alert and has been removed."
    )
    await bot.send_message(chat_id=watch["chat_id"], text=text, parse_mode="HTML")


def target_triggered(watch, current: float) -> bool:
    return (
        current >= watch["target_price"]
        if watch["target_direction"] == "above"
        else current <= watch["target_price"]
    )


async def check_target_watch(bot: Bot, watch, current: float, source: str) -> bool:
    """Evaluate one limit-alert watch against a fresh price; fire + remove it
    if triggered, else just record the check. Returns True if it fired."""
    triggered = target_triggered(watch, current)
    logger.info(
        "price alert %s (%s:%s): current=%.6g target=%.6g (%s) triggered=%s source=%s",
        watch["id"], watch["chain"], watch["token_ref"], current, watch["target_price"],
        watch["target_direction"], triggered, source,
    )
    if triggered:
        await send_target_alert(bot, watch, current, source)
        # One-shot: remove rather than reset, unlike percentage watches.
        db.remove_watch(watch["chat_id"], watch["chat_seq"])
    else:
        db.update_after_check(watch["id"], current)
    return triggered
