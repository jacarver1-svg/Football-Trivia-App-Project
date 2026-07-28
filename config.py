"""
Central configuration for the football trivia scripts.
Edit values here rather than hunting through fetch_wikidata.py,
manage_questions.py, or generate_questions.py individually.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------- Database connection ----------
# Actual credentials come from your .env file, not from here.
DB_CONFIG = {
    "dbname": os.environ.get("FOOTBALL_DB_NAME", "football_trivia"),
    "user": os.environ.get("FOOTBALL_DB_USER", "postgres"),
    "password": os.environ.get("FOOTBALL_DB_PASSWORD"),
    "host": os.environ.get("FOOTBALL_DB_HOST", "localhost"),
    "port": int(os.environ.get("FOOTBALL_DB_PORT", 5432)),
}

# ---------- Wikidata request settings ----------
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

HEADERS = {
    "User-Agent": "FootballTriviaApp/0.1 (https://github.com/jacarver1-svg/Football-Trivia-App-Project; contact: jacarver1@gmail.com)"
}

SPARQL_TIMEOUT = 60          # seconds to wait for a single query response before giving up
MAX_RETRIES = 3              # retry attempts on transient errors (429 / 502 / 503 / 504 / timeout)
RETRY_BASE_WAIT = 5          # seconds, scaled by attempt number, for 502/503/504 and timeouts
RATE_LIMIT_FALLBACK_WAIT = 30  # seconds, scaled by attempt number, for 429s with no Retry-After header

# ---------- Leagues to pull from ----------
# Add or remove leagues here. Look up new QIDs at wikidata.org.
LEAGUES = {
    "Premier League": "Q9448",
    "La Liga": "Q324867",
    "Bundesliga": "Q82595",
    "Serie A": "Q15804",
    "Ligue 1": "Q13394",
    "EFL Championship": "Q19510",
    "LaLiga 2": "Q35615",
    "2. Bundesliga": "Q152665",
    "Serie B": "Q194052",
    "Ligue 2": "Q217374",
    "Major League Soccer": "Q18543",
    "Primeira Liga": "Q182994",
    "Eredivisie": "Q167541",
    "J1 League": "Q276445",
    "Campeonato Brasileiro Série A": "Q206813",
    "Argentine Primera División": "Q223170",
    "Danish Superliga": "Q204752",
    "Belgian Pro League": "Q216022",
}

# ---------- Season ----------
# Wikidata's P118 ("league") reflects a club's CURRENT league, not
# season-by-season history, so this single value is used as the season
# marker for club_league_seasons entries, and as the default filter for
# which club memberships count as "active" in a pull.
CURRENT_SEASON_YEAR = 2025  # represents the 2025/26 season

# ---------- Pull sizing ----------
DEFAULT_LIMIT_PER_LEAGUE = 200

# ---------- Pacing between requests ----------
# Keep these conservative — Wikidata's public endpoint is free and
# shared, and has both a rate limit (429) and a tendency to time out
# on heavier qualifier-based queries under load.
ENRICH_SLEEP_SECONDS = 1.5   # delay between a club's detail/trophy requests
LEAGUE_SLEEP_SECONDS = 1     # delay between leagues within one pull

# ---------- Competitions to fetch COMPLETE champion history for ----------
# Unlike per-club discovery (which only surfaces what's backfilled on
# each club's own page), this queries each COMPETITION directly for
# every season and its winner — far more complete, and much cheaper
# (one request per competition instead of one per club). Includes your
# five tracked leagues (their QIDs match the LEAGUES dict above, so
# their trophies get parent_league_id linked automatically) plus any
# cup/continental competitions you want full history for.
TROPHY_COMPETITIONS = {
    "Premier League": "Q9448",
    "La Liga": "Q324867",
    "Bundesliga": "Q82595",
    "Serie A": "Q15804",
    "Ligue 1": "Q13394",
    "UEFA Champions League": "Q18756",
    "UEFA Europa League": "Q18760",
    "FA Cup": "Q11151",
    "Copa del Rey": "Q483794",
    "DFB-Pokal": "Q150880",
    "Coupe de France": "Q212412",
}

# ---------- Trophies ----------
# If set to a year (e.g. 2025), club trophy pulls only fetch competitions
# won in/around that year, instead of a club's entire trophy history.
# Set to None to pull full history (more useful for "how many trophies
# has X won all-time" questions, but noisier and slightly slower).
TROPHY_SEASON_FILTER = None

# ---------- Progress tracking ----------
PROGRESS_FILE = "fetch_progress.json"