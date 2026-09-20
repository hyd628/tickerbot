import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
POLL_INTERVAL_MINUTES = float(os.environ.get("POLL_INTERVAL_MINUTES", "5"))
DB_PATH = os.environ.get("DB_PATH", "alertbot.db")
COINGECKO_API_BASE = os.environ.get("COINGECKO_API_BASE", "https://api.coingecko.com/api/v3")
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")

# Key under which the global default poll interval is stored in the
# `settings` table. Shared between bot.py and monitor.py (kept here instead
# of in bot.py to avoid a circular import, since monitor.check_prices is
# imported by bot.py).
INTERVAL_SETTING_KEY = "poll_interval_minutes"
