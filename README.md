# alertbot

A Telegram bot that alerts you when the price of a Solana, Sui, Ethereum or
Bitcoin token moves by more than a threshold percentage. Prices come from
the [CoinGecko API](https://www.coingecko.com/en/api), looked up either by
CoinGecko coin id (e.g. `solana`, `sui`, `ethereum`, `bitcoin`, `bonk`) or,
for Solana/Sui/Ethereum, by on-chain contract / coin-type address. Bitcoin
has no general token-contract standard, so it's id-only (native BTC, or
other coins CoinGecko tracks by id).

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
  a token. `chain` is `solana`, `sui`, `ethereum` or `bitcoin` (`sol`,
  `eth`, `btc` are also accepted).
  - `/watch solana So11111111111111111111111111111111111111112 5 SOL`
  - `/watch sui 0x2::sui::SUI 5 SUI`
  - `/watch ethereum 0xdAC17F958D2ee523a2206206994597C13D831ec7 5 USDT`
  - `/watch bitcoin bitcoin 3 BTC` (bitcoin is id-only)
  - `/watch solana bonk 10 BONK` (using a CoinGecko coin id)
- `/list` — show your active watches and their last known price.
- `/unwatch <id>` — stop watching (id comes from `/list`).
- `/price <chain> <address_or_id>` — check a price on demand.
- `/setinterval [minutes]` — change the *default* polling interval, used by
  any watch that doesn't have its own override (no args to see the current
  default and the actual check frequency). Persists in the database, so
  `POLL_INTERVAL_MINUTES` in `.env` is only the starting point before anyone
  runs `/setinterval`.
- `/setinterval [id] [minutes|default]` — give a single watch (id from
  `/list`) its own polling interval, independent of the default — e.g.
  `/setinterval 3 1` checks watch 3 every minute regardless of the global
  setting. `/setinterval 3 default` removes the override.
- `/help` — show usage.

Each watch is checked on its own interval (its override if it has one,
otherwise the global default) — the scheduler internally ticks at whatever
the smallest interval in use is, but only actually fetches a price for a
watch once its own interval has elapsed, so giving one watch a fast
interval doesn't make every other watch poll that fast too. When a token's
price moves by at least its threshold percentage since the last check (or
since it started being watched), the bot sends an alert and resets the
baseline to the new price, so alerts track the size of each subsequent move
rather than firing repeatedly for the same move.

## Deploying to Fly.io

The bot runs as a single always-on background process (Telegram long-polling,
not a web server), with its SQLite database on a small persistent volume so
data survives restarts and redeploys.

1. [Install `flyctl`](https://fly.io/docs/flyctl/install/) and sign up/log in:

   ```bash
   fly auth login
   ```

2. Edit `fly.toml`: change `app` to a globally-unique name, and
   `primary_region` to whichever region is closest to you (see
   `fly platform regions`).

3. Create the app and a 1GB volume for the database (must be in the same
   region as `primary_region` in `fly.toml`):

   ```bash
   fly apps create <your-app-name>
   fly volumes create alertbot_data --size 1 --region <your-region>
   ```

4. Set your bot token as a secret (never put it in `fly.toml` or commit it):

   ```bash
   fly secrets set BOT_TOKEN=<token from BotFather>
   ```

5. Deploy:

   ```bash
   fly deploy
   ```

6. Check it's running and watch logs:

   ```bash
   fly status
   fly logs
   ```

To change the poll interval later, either use the `/setinterval` command in
Telegram (persists in the DB, no redeploy needed) or edit
`POLL_INTERVAL_MINUTES` in `fly.toml` and redeploy.

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
