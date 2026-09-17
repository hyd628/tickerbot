import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
POLL_INTERVAL_MINUTES = float(os.environ.get("POLL_INTERVAL_MINUTES", "5"))
DB_PATH = os.environ.get("DB_PATH", "alertbot.db")
COINGECKO_API_BASE = os.environ.get("COINGECKO_API_BASE", "https://api.coingecko.com/api/v3")
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")
