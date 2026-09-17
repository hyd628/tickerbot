import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from . import config, db, prices
from .monitor import check_prices

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "*Solana / Sui price alert bot*\n\n"
    "/watch <chain> <address_or_id> <threshold_pct> [label] — start watching a token. "
    "`chain` is `solana` or `sui`. `address_or_id` can be a contract/coin-type address "
    "or a CoinGecko coin id (e.g. `solana`, `sui`, `bonk`).\n"
    "/list — show your active watches\n"
    "/unwatch <id> — stop watching (id from /list)\n"
    "/price <chain> <address_or_id> — check a price right now\n"
    "/help — show this message\n\n"
    "Example:\n"
    "`/watch solana So11111111111111111111111111111111111111112 5 SOL`\n"
    "`/watch sui 0x2::sui::SUI 5 SUI`"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Usage: /watch <chain> <address_or_id> <threshold_pct> [label]"
        )
        return

    chain = args[0].lower()
    token_ref = args[1]
    label = " ".join(args[3:]) or None

    if chain not in ("solana", "sui"):
        await update.message.reply_text("chain must be `solana` or `sui`.", parse_mode="Markdown")
        return

    try:
        threshold_pct = float(args[2])
    except ValueError:
        await update.message.reply_text("threshold_pct must be a number, e.g. 5 for 5%.")
        return

    if threshold_pct <= 0:
        await update.message.reply_text("threshold_pct must be greater than 0.")
        return

    ref_type = prices.guess_ref_type(chain, token_ref)

    try:
        baseline_price = prices.get_price(chain, ref_type, token_ref)
    except Exception as exc:
        logger.exception("Price lookup failed for %s:%s", chain, token_ref)
        await update.message.reply_text(f"Couldn't fetch a price for that token: {exc}")
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
    await update.message.reply_text(
        f"Watching {name} on {chain} (id {watch_id}). "
        f"Current price: ${baseline_price:.6g}. "
        f"You'll be alerted on moves of {threshold_pct}% or more."
    )


async def unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /unwatch <id> (see /list for ids)")
        return
    try:
        watch_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id must be a number, see /list.")
        return

    removed = db.remove_watch(update.effective_chat.id, watch_id)
    if removed:
        await update.message.reply_text(f"Stopped watching id {watch_id}.")
    else:
        await update.message.reply_text(f"No watch with id {watch_id} for this chat.")


async def list_watches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.list_watches(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("No active watches. Use /watch to add one.")
        return

    lines = ["Your watches:"]
    for row in rows:
        name = row["label"] or row["token_ref"]
        line = f"#{row['id']} {name} ({row['chain']}) — threshold {row['threshold_pct']}%"
        if row["last_price"] is not None:
            line += f", last price ${row['last_price']:.6g}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines))


async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /price <chain> <address_or_id>")
        return

    chain = args[0].lower()
    token_ref = args[1]
    if chain not in ("solana", "sui"):
        await update.message.reply_text("chain must be `solana` or `sui`.", parse_mode="Markdown")
        return

    ref_type = prices.guess_ref_type(chain, token_ref)
    try:
        current = prices.get_price(chain, ref_type, token_ref)
    except Exception as exc:
        await update.message.reply_text(f"Couldn't fetch a price for that token: {exc}")
        return

    await update.message.reply_text(f"{token_ref} ({chain}): ${current:.6g}")


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

    interval_seconds = config.POLL_INTERVAL_MINUTES * 60
    application.job_queue.run_repeating(check_prices, interval=interval_seconds, first=10)

    return application
