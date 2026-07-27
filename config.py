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
    "User-Agent": "FootballTriviaApp/0.1 (personal project; contact: your-email@example.com)"
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
}

# ---------- Season ----------
# Wikidata's P118 ("league") reflects a club's CURRENT league, not
# season-by-season history, so this single value is used as the season
# marker for club_league_seasons entries, and as the default filter for
# which club memberships count as "active" in a pull.
CURRENT_SEASON_YEAR = 2025  # represents the 2025/26 season

# ---------- Pull sizing ----------
DEFAULT_LIMIT_PER_LEAGUE = 50

# ---------- Pacing between requests ----------
# Keep these conservative — Wikidata's public endpoint is free and
# shared, and has both a rate limit (429) and a tendency to time out
# on heavier qualifier-based queries under load.
ENRICH_SLEEP_SECONDS = 1.5   # delay between a club's detail/trophy requests
LEAGUE_SLEEP_SECONDS = 1     # delay between leagues within one pull

# ---------- Progress tracking ----------
PROGRESS_FILE = "fetch_progress.json"