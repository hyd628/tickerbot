import logging
from collections import defaultdict

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
CHAIN_ERROR_MSG = (
    "chain must be one of: solana, sui, ethereum, bitcoin, hyperliquid, robinhood "
    "(eth/btc/sol/hl/hood are also accepted)."
)

# (chain, token_ref, label) set up by /default.
DEFAULT_WATCHES = [
    ("bitcoin", "bitcoin", "BTC"),
    ("ethereum", "ethereum", "ETH"),
    ("solana", "solana", "SOL"),
]
DEFAULT_THRESHOLD_PCT = 1.0

HELP_TEXT = (
    "<b>Ticker-o-Bot</b> — crypto price alerts, natively on Solana / Sui / Ethereum / Bitcoin "
    "and beyond via CoinGecko/PreStocks coin id\n\n"
    "<code>/watch [chain] [address_or_id] [threshold_pct] [label]</code> — start watching a token. "
    "<code>chain</code> is <code>solana</code>, <code>sui</code>, <code>ethereum</code>, <code>bitcoin</code>, "
    "<code>hyperliquid</code> or <code>robinhood</code> (<code>eth</code>/<code>btc</code>/<code>sol</code>/"
    "<code>hl</code>/<code>hood</code> also work). <code>address_or_id</code> can be a contract/coin-type "
    "address (solana/sui/ethereum/hyperliquid/robinhood only) or a CoinGecko coin id (e.g. <code>solana</code>, "
    "<code>bitcoin</code>, <code>bonk</code>). Bitcoin has no token contracts, so it's id-only.\n"
    "<code>/default</code> — watch BTC, ETH and SOL at a 1% threshold in one shot (skips any you're already watching)\n"
    "<code>/list</code> — show your active watches\n"
    "<code>/prices</code> — show the current price of every asset you're watching (once each, even if "
    "you have more than one watch on the same asset)\n"
    "<code>/unwatch [id]</code> — stop watching (id from /list)\n"
    "<code>/price [chain] [address_or_id]</code> — check the price of any token, watched or not\n"
    "<code>/setinterval [minutes]</code> — change your default polling interval, for your watches only "
    "(no args to check the current value)\n"
    "<code>/setinterval [id] [minutes|default]</code> — give one of your watches (see /list for ids) its own "
    "polling interval, or 'default' to go back to your default\n"
    "<code>/help</code> — show this message\n\n"
    "Example:\n"
    "<code>/watch solana solana 5 SOL</code>\n"
    "<code>/watch sui 0x2::sui::SUI 5 SUI</code>\n"
    "<code>/watch ethereum 0xdAC17F958D2ee523a2206206994597C13D831ec7 5 USDT</code>\n"
    "<code>/watch bitcoin bitcoin 3 BTC</code>\n"
    "<code>/watch solana spacex 8 SpaceX</code> (a PreStocks tokenized pre-IPO stock, by ticker)\n"
    "<code>/watch hyperliquid hyperliquid 5 HYPE</code>\n"
    "<code>/watch robinhood apple-robinhood-tokenized-stock 5 AAPL</code> (Robinhood's own tokenized stock, by CoinGecko id)"
)


def _effective_tick_minutes() -> float:
    """How often the scheduler process actually needs to wake up: the
    smallest interval in use anywhere (the config-file fallback, any chat's
    own default, or any watch's own override). This is just the scheduler's
    wake-up cadence, not what any individual watch is checked against —
    check_prices() resolves each watch against its own chat's setting only,
    so one chat setting a fast interval never speeds up another chat."""
    candidates = [config.POLL_INTERVAL_MINUTES]
    chat_min = db.min_chat_setting(config.INTERVAL_SETTING_KEY)
    if chat_min is not None:
        candidates.append(chat_min)
    watch_min = db.min_watch_interval()
    if watch_min is not None:
        candidates.append(watch_min)
    return min(candidates)


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

    if threshold_pct < 0:
        await update.effective_message.reply_text("threshold_pct must not be negative.")
        return

    ref_type = prices.guess_ref_type(chain, token_ref)

    try:
        baseline = prices.get_price(chain, ref_type, token_ref)
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
        baseline_price=baseline.price,
        label=label,
    )

    name = label or token_ref
    reply = (
        f"Watching {name} on {chain} (id {watch_id}). "
        f"Current price: ${baseline.price:.6g} (via {baseline.source}). "
    )
    if threshold_pct == 0:
        reply += "Threshold is 0%, so you'll get an alert on every single check, even with no price change."
    else:
        reply += f"You'll be alerted on moves of {threshold_pct}% or more."
    await update.effective_message.reply_text(reply)


async def default_watches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    existing_keys = {
        (row["chain"], row["token_ref"].lower()) for row in db.list_watches(chat_id)
    }

    added_lines = []
    skipped_labels = []
    failed_lines = []

    for chain, token_ref, label in DEFAULT_WATCHES:
        if (chain, token_ref.lower()) in existing_keys:
            skipped_labels.append(label)
            continue

        ref_type = prices.guess_ref_type(chain, token_ref)
        try:
            baseline = prices.get_price(chain, ref_type, token_ref)
        except Exception as exc:
            logger.exception("Price lookup failed for %s:%s", chain, token_ref)
            failed_lines.append(f"{label}: couldn't fetch a price ({exc})")
            continue

        watch_id = db.add_watch(
            chat_id=chat_id,
            chain=chain,
            ref_type=ref_type,
            token_ref=token_ref,
            threshold_pct=DEFAULT_THRESHOLD_PCT,
            baseline_price=baseline.price,
            label=label,
        )
        added_lines.append(f"#{watch_id} {label}: ${baseline.price:.6g} (via {baseline.source})")

    lines = []
    if added_lines:
        lines.append(f"Added default watches (threshold {DEFAULT_THRESHOLD_PCT:g}%):")
        lines.extend(added_lines)
    if skipped_labels:
        lines.append(f"Already watching: {', '.join(skipped_labels)}")
    if failed_lines:
        lines.append("Failed:")
        lines.extend(failed_lines)
    if not lines:
        lines.append("Nothing to do.")

    await update.effective_message.reply_text("\n".join(lines))


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

    chat_default = float(
        db.get_chat_setting(update.effective_chat.id, config.INTERVAL_SETTING_KEY, str(config.POLL_INTERVAL_MINUTES))
    )

    lines = ["Your watches:"]
    for row in rows:
        name = row["label"] or row["token_ref"]
        line = f"#{row['chat_seq']} {name} ({row['chain']}) — threshold {row['threshold_pct']}%"
        if row["interval_minutes"] is not None:
            line += f", every {row['interval_minutes']:g} min"
        else:
            line += f", every {chat_default:g} min (default)"
        if row["last_price"] is not None:
            line += f", last price ${row['last_price']:.6g}"
        lines.append(line)
    await update.effective_message.reply_text("\n".join(lines))


async def prices_watched(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.list_watches(update.effective_chat.id)
    if not rows:
        await update.effective_message.reply_text("No active watches. Use /watch to add one.")
        return

    # Multiple watches can point at the exact same asset (e.g. two different
    # thresholds on the same token) — dedupe by (chain, ref_type, token_ref)
    # so it's only fetched, and shown, once.
    seen = {}
    order = []
    for row in rows:
        key = (row["chain"], row["ref_type"], row["token_ref"].lower())
        if key not in seen:
            seen[key] = row["label"] or row["token_ref"]
            order.append(key)

    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for chain, ref_type, token_ref_lower in order:
        groups[(chain, ref_type)].append(token_ref_lower)

    fetched: dict[tuple[str, str], dict] = {}
    for (chain, ref_type), refs in groups.items():
        try:
            fetched[(chain, ref_type)] = prices.get_prices(chain, ref_type, refs)
        except Exception:
            logger.exception("Failed to fetch prices for %s/%s: %s", chain, ref_type, refs)

    lines = ["Current prices:"]
    for chain, ref_type, token_ref_lower in order:
        name = seen[(chain, ref_type, token_ref_lower)]
        result = fetched.get((chain, ref_type), {}).get(token_ref_lower)
        if result is None:
            lines.append(f"{name} ({chain}): unavailable right now")
        else:
            lines.append(f"{name} ({chain}): ${result.price:.6g} [{result.source}]")

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

    await update.effective_message.reply_text(f"{token_ref} ({chain}): ${current.price:.6g} [{current.source}]")


async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args

    chat_id = update.effective_chat.id

    if not args:
        default = db.get_chat_setting(chat_id, config.INTERVAL_SETTING_KEY, str(config.POLL_INTERVAL_MINUTES))
        await update.effective_message.reply_text(
            f"Your default polling interval: {default} minute(s) (used by your watches with no override).\n"
            f"This only affects your own watches, not other chats using this bot.\n"
            f"Usage: /setinterval [minutes] to change your default, or "
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

        db.set_chat_setting(chat_id, config.INTERVAL_SETTING_KEY, minutes)
        _reschedule_price_job(context.application, _effective_tick_minutes())

        reply = (
            f"Your default polling interval is now {minutes} minute(s), for your watches without "
            f"their own override. This doesn't affect other chats using this bot."
        )
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
            "Usage: /setinterval [minutes] (your default) or "
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

    updated = db.set_watch_interval(chat_id, watch_id, minutes)
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
    application.add_handler(CommandHandler("default", default_watches))
    application.add_handler(CommandHandler("unwatch", unwatch))
    application.add_handler(CommandHandler("list", list_watches))
    application.add_handler(CommandHandler("prices", prices_watched))
    application.add_handler(CommandHandler("price", price_cmd))
    application.add_handler(CommandHandler("setinterval", set_interval))
    application.add_error_handler(on_error)

    _reschedule_price_job(application, _effective_tick_minutes())

    return application
