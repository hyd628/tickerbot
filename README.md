# alertbot

A Telegram bot that alerts you when the price of Solana or Sui tokens moves
by more than a threshold percentage. Prices come from the [CoinGecko
API](https://www.coingecko.com/en/api), looked up either by CoinGecko coin
id (e.g. `solana`, `sui`, `bonk`) or by on-chain contract / coin-type
address on the `solana` and `sui` asset platforms.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) on Telegram and
   copy the token it gives you.
2. Create a virtualenv and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and fill in `BOT_TOKEN`:

   ```bash
   cp .env.example .env
   ```

4. Run the bot:

   ```bash
   python main.py
   ```

## Usage

- `/watch <chain> <address_or_id> <threshold_pct> [label]` — start watching
  a token. `chain` is `solana` or `sui`.
  - `/watch solana So11111111111111111111111111111111111111112 5 SOL`
  - `/watch sui 0x2::sui::SUI 5 SUI`
  - `/watch solana bonk 10 BONK` (using a CoinGecko coin id)
- `/list` — show your active watches and their last known price.
- `/unwatch <id>` — stop watching (id comes from `/list`).
- `/price <chain> <address_or_id>` — check a price on demand.
- `/help` — show usage.

The bot polls prices every `POLL_INTERVAL_MINUTES` (default 5, configurable
in `.env`). When a token's price moves by at least its threshold percentage
since the last check (or since it started being watched), the bot sends an
alert and resets the baseline to the new price, so alerts track the size of
each subsequent move rather than firing repeatedly for the same move.

## Data storage

Watches and their state are stored in a local SQLite database (`alertbot.db`
by default, path configurable via `DB_PATH`). No data leaves your machine
except price lookups to CoinGecko and messages sent through Telegram.

## Notes

- The public CoinGecko API has rate limits (roughly 10-30 requests/minute).
  Watches are batched into one request per (chain, reference type) per poll,
  but if you watch many tokens across both id- and contract-based
  references, consider raising `POLL_INTERVAL_MINUTES` or setting
  `COINGECKO_API_KEY` if you have a CoinGecko Demo/Pro plan.
