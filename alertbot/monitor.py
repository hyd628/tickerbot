import logging
from collections import defaultdict

from telegram.ext import ContextTypes

from . import db, prices

logger = logging.getLogger(__name__)


async def check_prices(context: ContextTypes.DEFAULT_TYPE) -> None:
    watches = db.all_watches()
    if not watches:
        return

    # Batch by (chain, ref_type) so each group is a single CoinGecko call.
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for watch in watches:
        groups[(watch["chain"], watch["ref_type"])].append(watch)

    for (chain, ref_type), group in groups.items():
        refs = list({w["token_ref"] for w in group})
        try:
            current_prices = prices.get_prices(chain, ref_type, refs)
        except Exception:
            logger.exception("Failed to fetch prices for %s/%s: %s", chain, ref_type, refs)
            continue

        for watch in group:
            current = current_prices.get(watch["token_ref"].lower())
            if current is None:
                logger.warning("No price returned for %s:%s", chain, watch["token_ref"])
                continue

            baseline = watch["baseline_price"]
            pct_change = (current - baseline) / baseline * 100 if baseline else 0.0

            if abs(pct_change) >= watch["threshold_pct"]:
                await _send_alert(context, watch, current, pct_change)
                db.update_after_check(watch["id"], current, new_baseline=current)
            else:
                db.update_after_check(watch["id"], current)


async def _send_alert(context: ContextTypes.DEFAULT_TYPE, watch, current: float, pct_change: float) -> None:
    direction = "\U0001F4C8" if pct_change >= 0 else "\U0001F4C9"
    name = watch["label"] or watch["token_ref"]
    text = (
        f"{direction} *{name}* ({watch['chain']})\n"
        f"Price: `${current:.6g}` ({pct_change:+.2f}% since last alert)\n"
        f"Threshold: {watch['threshold_pct']}%  |  watch id: {watch['id']}"
    )
    await context.bot.send_message(
        chat_id=watch["chat_id"], text=text, parse_mode="Markdown"
    )
