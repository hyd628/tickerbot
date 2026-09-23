# Ticker-o-Bot

A Telegram bot that alerts you when a crypto token's price moves by more
than a threshold percentage — Solana, Sui, Ethereum, Bitcoin, Hyperliquid
and Robinhood (tokenized stocks) natively, plus (via CoinGecko/PreStocks
coin id) practically any other asset those sources track.

## Usage

- `/watch <chain> <address_or_id> <threshold_pct> [label]` — start watching
  a token. `chain` is `solana`, `sui`, `ethereum`, `bitcoin`, `hyperliquid`
  or `robinhood` (`sol`, `eth`, `btc`, `hl`, `hood` are also accepted).
  - `/watch solana solana 5 SOL` (using a CoinGecko coin id)
  - `/watch sui 0x2::sui::SUI 5 SUI` (using a contract/coin-type address)
  - `/watch ethereum 0xdAC17F958D2ee523a2206206994597C13D831ec7 5 USDT`
  - `/watch bitcoin bitcoin 3 BTC` (bitcoin is id-only)
  - `/watch solana bonk 10 BONK` (using a CoinGecko coin id)
  - `/watch hyperliquid hyperliquid 5 HYPE` (using a CoinGecko coin id)
  - `/watch robinhood apple-robinhood-tokenized-stock 5 AAPL` (Robinhood's own tokenized stock, by CoinGecko id)
  - `/watch solana spacex 8 SpaceX` (a PreStocks tokenized pre-IPO stock, by ticker)
- `/list` — show your active watches and their last known (cached) price.
- `/prices` — fetch and show the current live price of every asset you're
  watching, once per asset even if several of your watches point at the
  same one (e.g. two thresholds on the same token).
- `/unwatch <id>` — stop watching (id comes from `/list`).
- `/price <chain> <address_or_id>` — check the live price of any token,
  whether you're watching it or not.
- `/setinterval [minutes]` — change *your* default polling interval, used by
  your watches that don't have their own override (no args to see your
  current default). This is per-chat: it never affects other chats using
  the same bot. `POLL_INTERVAL_MINUTES` in `.env` is only the fallback
  before any chat has run `/setinterval`.
- `/setinterval [id] [minutes|default]` — give a single watch of yours (id
  from `/list`) its own polling interval, independent of your default — e.g.
  `/setinterval 3 1` checks watch 3 every minute regardless of your default.
  `/setinterval 3 default` removes the override.
- `/help` — show usage.

Each watch is checked on its own interval: its own override if it has one,
otherwise its chat's default, otherwise the `.env` fallback. Internally the
scheduler wakes up at whatever the smallest interval in use is *anywhere*
(across every chat and watch), but only actually fetches a price for a
watch once that specific watch's own interval has elapsed — so one chat (or
one watch) using a fast interval never speeds up anyone else's watches.
When a token's price moves by at least its threshold percentage since the
last check (or since it started being watched), the bot sends an alert and
resets the baseline to the new price, so alerts track the size of each
subsequent move rather than firing repeatedly for the same move.

## Price sources

Prices can come from more than one source. Each source (currently
[Pyth Network](https://pyth.network), [PreStocks](https://prestocks.com),
and the [CoinGecko API](https://www.coingecko.com/en/api)) is tried in
priority order — `tickerbot/prices.py`'s `SOURCE_PRIORITY` — and the first
one that actually has a price for that specific asset wins; anything a
source doesn't cover just falls through to the next one. Every price shown
by the bot (`/watch`, `/price`, `/prices`, and alert messages) says which
source it came from, e.g. `[Pyth]`, `[PreStocks]`, or `[CoinGecko]`.

- **Pyth** covers a small, explicit set of assets (native SOL, JitoSOL, the
  PYTH token, native BTC, native ETH, native HYPE — see `FEED_IDS` in
  `tickerbot/pyth.py`) when `PYTH_API_KEY` is configured. Pyth doesn't offer
  a general way to resolve an arbitrary contract address to a feed, so this
  list is manually maintained rather than automatic, and is restricted to
  whatever's on your key's plan — the free trial's whitelist notably does
  NOT include a Sui feed, so Sui watches never use Pyth regardless of
  whether a key is configured.
- **PreStocks** covers its own tokenized pre-IPO equities (OpenAI, SpaceX,
  Anthropic, etc. — Solana SPL tokens 1:1-backed by SPV exposure). No API
  key needed; watch them by Solana mint address or by ticker symbol (e.g.
  `/watch solana spacex 5 SpaceX`). The full token list is cached for 60
  seconds (`CACHE_TTL_SECONDS` in `tickerbot/prestocks.py`) since the API
  has an undocumented but real rate limit — confirmed to 429 after just a
  couple of calls in quick succession.
- **CoinGecko** is the universal fallback: it covers almost anything, by
  CoinGecko coin id (e.g. `solana`, `sui`, `ethereum`, `bitcoin`, `bonk`)
  or, for Solana/Sui/Ethereum/Hyperliquid/Robinhood, by on-chain
  contract/coin-type address. Bitcoin has no general token-contract
  standard, so it's id-only. Robinhood here means Robinhood's own
  tokenized-stock platform (Apple, Nvidia, Meta, etc., each its own
  CoinGecko id/address) — not to be confused with `robinhood-xstock`, a
  *different* tokenized version of Robinhood's own public stock (ticker
  HOOD) issued by Backed Finance, which is already watchable today via the
  `solana` chain like any other xStock.

Adding a new source is just a new module with a `NAME` and a
`get_prices(chain, ref_type, token_refs)` function, registered in
`prices._SOURCES` and added to `SOURCE_PRIORITY`.

## Data storage

Watches and their state are stored in a local SQLite database (`tickerbot.db`
by default, path configurable via `DB_PATH`). No data leaves your machine
except price lookups to CoinGecko/Pyth/PreStocks and messages sent through
Telegram.

The watch ids shown in `/list`, alerts, `/unwatch` and `/setinterval` are
scoped per chat (each chat has its own 1, 2, 3... sequence, stored
separately from the internal database row id) rather than a single counter
shared across every chat using the bot — so they never reveal how many
watches (or users) exist elsewhere.

## Notes

- The public CoinGecko API has rate limits (roughly 10-30 requests/minute).
  Watches are batched into one request per (chain, reference type) per poll,
  but if you watch many tokens across both id- and contract-based
  references, consider raising `POLL_INTERVAL_MINUTES` or setting
  `COINGECKO_API_KEY` if you have a CoinGecko Demo/Pro plan.
