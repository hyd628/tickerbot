import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from . import config, db, prices
from .monitor import check_prices

logger = logging.getLogger(__name__)

PRICE_JOB_NAME = "check_prices"

# HTML parse mode, not Markdown: Telegram's legacy Markdown treats every "_"
# as an italics toggle (breaks on things like "address_or_id"), and real
# token addresses/labels can contain "_", "<", ">" etc. that would otherwise
# need careful escaping. HTML only cares about <, >, & (handled below).
CHAIN_ERROR_MSG = "chain must be one of: solana, sui, ethereum, bitcoin (eth/btc/sol are also accepted)."

HELP_TEXT = (
    "<b>Solana / Sui / Ethereum / Bitcoin price alert bot</b>\n\n"
    "<code>/watch [chain] [address_or_id] [threshold_pct] [label]</code> — start watching a token. "
    "<code>chain</code> is <code>solana</code>, <code>sui</code>, <code>ethereum</code> or <code>bitcoin</code> "
    "(<code>eth</code>/<code>btc</code>/<code>sol</code> also work). <code>address_or_id</code> can be "
    "a contract/coin-type address (solana/sui/ethereum only) or a CoinGecko coin id (e.g. <code>solana</code>, "
    "<code>bitcoin</code>, <code>bonk</code>). Bitcoin has no token contracts, so it's id-only.\n"
    "<code>/list</code> — show your active watches\n"
    "<code>/unwatch [id]</code> — stop watching (id from /list)\n"
    "<code>/price [chain] [address_or_id]</code> — check a price right now\n"
    "<code>/setinterval [minutes]</code> — change the default polling interval for every watch "
    "(no args to check the current value)\n"
    "<code>/setinterval [id] [minutes|default]</code> — give one watch (see /list for ids) its own "
    "polling interval, or 'default' to go back to the global one\n"
    "<code>/help</code> — show this message\n\n"
    "Example:\n"
    "<code>/watch solana So11111111111111111111111111111111111111112 5 SOL</code>\n"
    "<code>/watch sui 0x2::sui::SUI 5 SUI</code>\n"
    "<code>/watch ethereum 0xdAC17F958D2ee523a2206206994597C13D831ec7 5 USDT</code>\n"
    "<code>/watch bitcoin bitcoin 3 BTC</code>"
)


def _effective_tick_minutes() -> float:
    """How often the scheduler actually needs to run: the smallest interval
    across the global default and any per-watch overrides. check_prices()
    still only fetches prices for watches whose own interval has elapsed, so
    a small per-watch override doesn't make every other watch poll faster."""
    global_default = float(db.get_setting(config.INTERVAL_SETTING_KEY, str(config.POLL_INTERVAL_MINUTES)))
    per_watch_min = db.min_watch_interval()
    if per_watch_min is not None:
        return min(global_default, per_watch_min)
    return global_default


def _reschedule_price_job(application: Application, minutes: float) -> None:
    for job in application.job_queue.get_jobs_by_name(PRICE_JOB_NAME):
        job.schedule_removal()
    application.job_queue.run_repeating(
        check_prices, interval=minutes * 60, first=10, name=PRICE_JOB_NAME
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode="HTML")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode="HTML")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error while processing update %s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Something went wrong handling that command. Check the bot's logs for details."
        )


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) < 3:
        await update.effective_message.reply_text(
            "Usage: /watch <chain> <address_or_id> <threshold_pct> [label]"
        )
        return

    chain = prices.normalize_chain(args[0])
    token_ref = args[1]
    label = " ".join(args[3:]) or None

    if chain is None:
        await update.effective_message.reply_text(CHAIN_ERROR_MSG)
        return

    try:
        threshold_pct = float(args[2])
    except ValueError:
        await update.effective_message.reply_text("threshold_pct must be a number, e.g. 5 for 5%.")
        return

    if threshold_pct <= 0:
        await update.effective_message.reply_text("threshold_pct must be greater than 0.")
        return

    ref_type = prices.guess_ref_type(chain, token_ref)

    try:
        baseline_price = prices.get_price(chain, ref_type, token_ref)
    except Exception as exc:
        logger.exception("Price lookup failed for %s:%s", chain, token_ref)
        await update.effective_message.reply_text(f"Couldn't fetch a price for that token: {exc}")
        return

    watch_id = db.add_watch(
        chat_id=update.effective_chat.id,
        chain=chain,
        ref_type=ref_type,
        token_ref=token_ref,
        threshold_pct=threshold_pct,
        baseline_price=baseline_price,
        label=label,
    )

    name = label or token_ref
    await update.effective_message.reply_text(
        f"Watching {name} on {chain} (id {watch_id}). "
        f"Current price: ${baseline_price:.6g}. "
        f"You'll be alerted on moves of {threshold_pct}% or more."
    )


async def unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /unwatch <id> (see /list for ids)")
        return
    try:
        watch_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("id must be a number, see /list.")
        return

    removed = db.remove_watch(update.effective_chat.id, watch_id)
    if removed:
        _reschedule_price_job(context.application, _effective_tick_minutes())
        await update.effective_message.reply_text(f"Stopped watching id {watch_id}.")
    else:
        await update.effective_message.reply_text(f"No watch with id {watch_id} for this chat.")


async def list_watches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.list_watches(update.effective_chat.id)
    if not rows:
        await update.effective_message.reply_text("No active watches. Use /watch to add one.")
        return

    lines = ["Your watches:"]
    for row in rows:
        name = row["label"] or row["token_ref"]
        line = f"#{row['id']} {name} ({row['chain']}) — threshold {row['threshold_pct']}%"
        if row["interval_minutes"] is not None:
            line += f", every {row['interval_minutes']:g} min"
        if row["last_price"] is not None:
            line += f", last price ${row['last_price']:.6g}"
        lines.append(line)
    await update.effective_message.reply_text("\n".join(lines))


async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) < 2:
        await update.effective_message.reply_text("Usage: /price <chain> <address_or_id>")
        return

    chain = prices.normalize_chain(args[0])
    token_ref = args[1]
    if chain is None:
        await update.effective_message.reply_text(CHAIN_ERROR_MSG)
        return

    ref_type = prices.guess_ref_type(chain, token_ref)
    try:
        current = prices.get_price(chain, ref_type, token_ref)
    except Exception as exc:
        await update.effective_message.reply_text(f"Couldn't fetch a price for that token: {exc}")
        return

    await update.effective_message.reply_text(f"{token_ref} ({chain}): ${current:.6g}")


async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args

    if not args:
        default = db.get_setting(config.INTERVAL_SETTING_KEY, str(config.POLL_INTERVAL_MINUTES))
        tick = _effective_tick_minutes()
        await update.effective_message.reply_text(
            f"Default polling interval: {default} minute(s) (used by watches with no override).\n"
            f"Actual check frequency right now: every {tick:g} minute(s), the smallest interval in use.\n"
            f"Usage: /setinterval [minutes] to change the default, or "
            f"/setinterval [id] [minutes|default] to override one watch (see /list for ids)."
        )
        return

    if len(args) == 1:
        try:
            minutes = float(args[0])
        except ValueError:
            await update.effective_message.reply_text("minutes must be a number, e.g. 5 or 0.5.")
            return
        if minutes <= 0:
            await update.effective_message.reply_text("minutes must be greater than 0.")
            return

        db.set_setting(config.INTERVAL_SETTING_KEY, minutes)
        _reschedule_price_job(context.application, _effective_tick_minutes())

        reply = f"Default polling interval set to {minutes} minute(s) for watches without their own override."
        if minutes < 1:
            reply += (
                " Note: polling more than once a minute across multiple chains/tokens can hit "
                "CoinGecko's free-tier rate limit."
            )
        await update.effective_message.reply_text(reply)
        return

    try:
        watch_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "Usage: /setinterval [minutes] (global default) or "
            "/setinterval [id] [minutes|default] (one watch, id from /list)."
        )
        return

    if args[1].lower() == "default":
        minutes = None
    else:
        try:
            minutes = float(args[1])
        except ValueError:
            await update.effective_message.reply_text(
                "minutes must be a number, or 'default' to clear a watch's override."
            )
            return
        if minutes <= 0:
            await update.effective_message.reply_text("minutes must be greater than 0.")
            return

    updated = db.set_watch_interval(update.effective_chat.id, watch_id, minutes)
    if not updated:
        await update.effective_message.reply_text(f"No watch with id {watch_id} for this chat.")
        return

    _reschedule_price_job(context.application, _effective_tick_minutes())
    if minutes is None:
        await update.effective_message.reply_text(f"Watch {watch_id} now uses the default polling interval.")
    else:
        await update.effective_message.reply_text(f"Watch {watch_id} will be checked every {minutes:g} minute(s).")


def build_application() -> Application:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    db.init_db()

    application = Application.builder().token(config.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("watch", watch))
    application.add_handler(CommandHandler("unwatch", unwatch))
    application.add_handler(CommandHandler("list", list_watches))
    application.add_handler(CommandHandler("price", price_cmd))
    application.add_handler(CommandHandler("setinterval", set_interval))
    application.add_error_handler(on_error)

    _reschedule_price_job(application, _effective_tick_minutes())

    return application
