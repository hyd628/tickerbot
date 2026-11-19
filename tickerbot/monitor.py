import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from telegram.ext import ContextTypes

from . import alerts, config, db, prices

logger = logging.getLogger(__name__)


def _watch_interval(watch, chat_defaults: dict) -> float:
    """A watch's own override if it has one, else its own chat's default
    (never another chat's), else the config-file fallback."""
    if watch["interval_minutes"] is not None:
        return watch["interval_minutes"]
    chat_default = chat_defaults.get(watch["chat_id"])
    return float(chat_default) if chat_default is not None else config.POLL_INTERVAL_MINUTES


def _is_due(watch, chat_defaults: dict, now: datetime) -> bool:
    if watch["last_checked_at"] is None:
        return True
    interval = _watch_interval(watch, chat_defaults)
    last_checked = datetime.fromisoformat(watch["last_checked_at"])
    return now - last_checked >= timedelta(minutes=interval)


async def check_prices(context: ContextTypes.DEFAULT_TYPE) -> None:
    watches = db.all_watches()
    if not watches:
        return

    # The scheduler tick runs at least as often as the smallest interval in
    # use anywhere (see bot._effective_tick_minutes), but each watch is only
    # actually checked against its own chat's default (or its own override),
    # never another chat's setting.
    chat_defaults = db.all_chat_settings(config.INTERVAL_SETTING_KEY)
    now = datetime.now(timezone.utc)
    due_watches = [w for w in watches if _is_due(w, chat_defaults, now)]
    if not due_watches:
        return

    # Batch by (chain, ref_type) so each group is a single CoinGecko call.
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for watch in due_watches:
        groups[(watch["chain"], watch["ref_type"])].append(watch)

    for (chain, ref_type), group in groups.items():
        refs = list({w["token_ref"] for w in group})
        try:
            current_prices = prices.get_prices(chain, ref_type, refs)
        except Exception:
            logger.exception("Failed to fetch prices for %s/%s: %s", chain, ref_type, refs)
            continue

        for watch in group:
            result = current_prices.get(watch["token_ref"].lower())
            if result is None:
                logger.warning("No price returned for %s:%s", chain, watch["token_ref"])
                continue
            current = result.price

            if watch["target_price"] is not None:
                # Shared with the real-time push path (binance_realtime.py)
                # so a watch can't be double-fired by both.
                await alerts.check_target_watch(context.bot, watch, current, result.source)
                continue

            baseline = watch["baseline_price"]
            pct_change = (current - baseline) / baseline * 100 if baseline else 0.0
            logger.info(
                "watch %s (%s:%s): baseline=%.6g current=%.6g change=%.3f%% threshold=%s%% source=%s",
                watch["id"], chain, watch["token_ref"], baseline, current, pct_change,
                watch["threshold_pct"], result.source,
            )

            if abs(pct_change) >= watch["threshold_pct"]:
                await alerts.send_threshold_alert(context.bot, watch, current, pct_change, result.source)
                db.update_after_check(watch["id"], current, new_baseline=current)
            else:
                db.update_after_check(watch["id"], current)
