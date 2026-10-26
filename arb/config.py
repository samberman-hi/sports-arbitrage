"""Defaults. Everything here can be overridden from the command line."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
LOG_PATH = DATA_DIR / "opportunities.csv"
LATEST_PATH = DATA_DIR / "latest.json"   # the last live scan, read by the Desk app's Arbitrage page
SAMPLE_PATH = DATA_DIR / "sample" / "odds_sample.json"

API_BASE = "https://api.the-odds-api.com/v4"

# Exchanges (The Odds API bookmaker keys) and their commission on net winnings.
# Betfair's standard rate is 5%; Matchbook and Smarkets charge about 2%.
# Check your own account: Betfair discounts and premium charges change the real rate.
EXCHANGE_COMMISSION = {
    "betfair_ex_uk": 0.05,
    "betfair_ex_eu": 0.05,
    "betfair_ex_au": 0.05,
    "matchbook": 0.02,
    "smarkets": 0.02,
}

# Leagues scanned by default. Only the ones in season are requested (the sports list is
# free to fetch), so out-of-season leagues never cost credits.
DEFAULT_SPORTS = [
    "soccer_epl",
    "soccer_efl_champ",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "basketball_nba",
]

DEFAULT_REGIONS = "uk,eu"   # UK and EU books
DEFAULT_MARKETS = "h2h"     # match odds; exchange lay prices (h2h_lay) come back with it for free

# Books in those regions that a customer in Ireland can't use: country-licensed brands
# and US-facing offshore books. Ignored when looking for arbs unless --all-books is given.
DEFAULT_EXCLUDE = {
    "betclic_fr", "pmu_fr", "winamax_fr", "winamax_de", "unibet_fr", "unibet_it", "unibet_nl",
    "unibet_se", "codere_it", "tipico_de", "leovegas_se",
    "betonlineag", "mybookieag", "betanysports", "gtbets", "everygame",
    "onexbet",  # 1xBet: offshore, not licensed in Ireland or the UK
}

# Set this to the books you hold accounts with to only see arbs you can place, e.g.
# MY_BOOKS = {"paddypower", "boylesports", "williamhill", "betfair_ex_uk"}
MY_BOOKS: set[str] = set()

MIN_ROI = 0.0             # report anything at or above this guaranteed return
SUSPICIOUS_ROI = 0.08     # above this, the price is more likely an error that gets voided
MAX_QUOTE_AGE_GAP = 600   # seconds between the oldest and newest leg before flagging as stale
STAKE_INCREMENT = 1.0     # round stakes to whole euros


def load_api_key() -> str | None:
    """ODDS_API_KEY from the environment, or from a .env file in the project root."""
    key = os.environ.get("ODDS_API_KEY")
    if key:
        return key.strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "ODDS_API_KEY" and value.strip():
                return value.strip().strip("'\"")
    return None
