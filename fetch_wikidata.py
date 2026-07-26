"""
Pull football (soccer) player data from Wikidata into your local Postgres DB.

This is a ONE-TIME (or occasional) pull — not a live API dependency.
Once it's in your `football_trivia` database, it's yours to query forever
with no rate limits and no cost.

Setup:
    pip install requests psycopg2-binary

Usage:
    python fetch_wikidata.py
"""

import os
import json
import time
import requests
import psycopg2
from dotenv import load_dotenv

# Reads key=value pairs from a local .env file and loads them into the
# environment automatically — no need to type/set them each terminal session.
load_dotenv()

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

# Be a good citizen: Wikidata asks for a descriptive User-Agent on all requests.
HEADERS = {
    "User-Agent": "FootballTriviaApp/0.1 (personal project; contact: your-email@example.com)"
}

# Credentials come from .env (which is gitignored, so it never gets committed).
# See .env.example for the expected format.
DB_CONFIG = {
    "dbname": os.environ.get("FOOTBALL_DB_NAME", "football_trivia"),
    "user": os.environ.get("FOOTBALL_DB_USER", "postgres"),
    "password": os.environ.get("FOOTBALL_DB_PASSWORD"),
    "host": os.environ.get("FOOTBALL_DB_HOST", "localhost"),
    "port": int(os.environ.get("FOOTBALL_DB_PORT", 5432)),
}

# Q937857 = "association football player" (occupation)
# Q6581097 = "male" (sex or gender) — filters out women's footballers
QUERY_TEMPLATE = """
SELECT ?player ?playerLabel ?birthDate ?birthPlaceLabel ?clubLabel WHERE {{
  ?player wdt:P106 wd:Q937857.        # occupation: football player
  ?player wdt:P27 wd:{country_qid}.   # country of citizenship
  ?player wdt:P21 wd:Q6581097.        # sex or gender: male
  OPTIONAL {{ ?player wdt:P569 ?birthDate. }}
  OPTIONAL {{ ?player wdt:P19 ?birthPlace. }}
  OPTIONAL {{ ?player wdt:P54 ?club. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT {limit}
"""

# Add/remove leagues here — display name -> Wikidata QID.
# Look up new ones at wikidata.org (search the league, QID is on the item page).
LEAGUES = {
    "Premier League": "Q9448",
    "La Liga": "Q324867",
    "Bundesliga": "Q82595",
    "Serie A": "Q15804",
    "Ligue 1": "Q13394",
}

# Pulls players across ANY of the leagues listed in LEAGUES, in one request,
# instead of looping per country. Captures each player's own nationality
# inline, since we're no longer fixing the country ahead of time.
LEAGUE_QUERY_TEMPLATE = """
SELECT ?player ?playerLabel ?birthDate ?birthPlaceLabel
       ?club ?clubLabel ?clubCountry ?clubCountryLabel
       ?nationality ?nationalityLabel
       ?league ?leagueLabel
       ?start ?end ?transferType ?transferTypeLabel WHERE {{
  VALUES ?league {{ {league_qids} }}

  ?player wdt:P106 wd:Q937857.        # occupation: football player
  ?player wdt:P21 wd:Q6581097.        # sex or gender: male
  ?player wdt:P27 ?nationality.       # country of citizenship

  ?player p:P54 ?membership.          # a specific club-membership fact
  ?membership ps:P54 ?club.           # ...which club
  ?club wdt:P118 ?league.             # club plays in one of the leagues above

  OPTIONAL {{ ?club wdt:P17 ?clubCountry. }}          # club's country
  ?membership pq:P580 ?start.                          # start time (required, not optional — needed for the season filter below)
  OPTIONAL {{ ?membership pq:P582 ?end. }}            # qualifier: end time
  OPTIONAL {{ ?membership pq:P1642 ?transferType. }}  # qualifier: acquisition transaction (loan/transfer/free transfer)
  OPTIONAL {{ ?player wdt:P569 ?birthDate. }}
  OPTIONAL {{ ?player wdt:P19 ?birthPlace. }}

  # Only keep memberships that were active during the target season —
  # started on or before it, and either still ongoing or didn't end
  # before the season began. This is what excludes historical players
  # from clubs' 1800s/1900s-era squads.
  FILTER(YEAR(?start) <= {target_season})
  FILTER(!BOUND(?end) || YEAR(?end) >= {target_season})

  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
ORDER BY ?player
OFFSET {offset}
LIMIT {limit}
"""

# Wikidata's P118 ("league") reflects a club's CURRENT league, not a
# season-by-season history. We don't have a reliable way to know which
# season that corresponds to for each row, so club_league_seasons entries
# from this script are recorded under this single "current season" marker.
# Update this each year, or replace with real historical data later if
# you want proper season-by-season league history.
CURRENT_SEASON_YEAR = 2025  # represents the 2025/26 season

# Tracks how far into each league's result list we've already paginated,
# so re-running the script fetches the NEXT page of players instead of
# re-fetching (or randomly landing on) the same ones. Stored locally,
# not in the database — this is just bookkeeping for the fetch process.
PROGRESS_FILE = "fetch_progress.json"


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_progress(progress):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def get_or_create_country(conn, name, wikidata_id):
    """
    Look up a country by name; insert it if it doesn't exist yet.
    Returns the country's row id in our own `countries` table
    (NOT the Wikidata QID — this is our own auto-incrementing id).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM countries WHERE name = %s;", (name,))
        row = cur.fetchone()
        if row:
            return row[0]

        cur.execute(
            """
            INSERT INTO countries (wikidata_id, name)
            VALUES (%s, %s)
            ON CONFLICT (wikidata_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING id;
            """,
            (wikidata_id, name),
        )
        country_id = cur.fetchone()[0]
    conn.commit()
    return country_id


def get_or_create_league(conn, name, wikidata_id):
    """Same upsert pattern as get_or_create_country, for leagues."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM leagues WHERE name = %s;", (name,))
        row = cur.fetchone()
        if row:
            return row[0]

        cur.execute(
            """
            INSERT INTO leagues (wikidata_id, name)
            VALUES (%s, %s)
            ON CONFLICT (wikidata_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING id;
            """,
            (wikidata_id, name),
        )
        league_id = cur.fetchone()[0]
    conn.commit()
    return league_id


def get_or_create_club(conn, wikidata_id, name, country_id):
    """Same upsert pattern again, for clubs."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO clubs (wikidata_id, name, country_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (wikidata_id) DO UPDATE SET
                name = EXCLUDED.name,
                country_id = EXCLUDED.country_id
            RETURNING id;
            """,
            (wikidata_id, name, country_id),
        )
        club_id = cur.fetchone()[0]
    conn.commit()
    return club_id


def link_club_to_league(conn, club_id, league_id, season_start_year):
    """Insert a club_league_seasons row, ignoring if it already exists."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO club_league_seasons (club_id, league_id, season_start_year)
            VALUES (%s, %s, %s)
            ON CONFLICT (club_id, league_id, season_start_year) DO NOTHING;
            """,
            (club_id, league_id, season_start_year),
        )
    conn.commit()


def link_player_to_club(conn, player_id, club_id, start_year, end_year, transfer_type):
    """
    Insert a player_clubs row (one stint), relying on the unique index
    on (player_id, club_id, start_year) to avoid duplicates on re-runs.
    Updates end_year/transfer_type in case they've changed (e.g. a loan
    became permanent) since the last pull.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO player_clubs (player_id, club_id, start_year, end_year, transfer_type)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (player_id, club_id, start_year) DO UPDATE SET
                end_year = EXCLUDED.end_year,
                transfer_type = EXCLUDED.transfer_type;
            """,
            (player_id, club_id, start_year, end_year, transfer_type),
        )
    conn.commit()


def fetch_players(country_qid, limit=50):
    """Fetch male players for a given country QID from Wikidata."""
    query = QUERY_TEMPLATE.format(country_qid=country_qid, limit=limit)
    response = requests.get(
        WIKIDATA_SPARQL_URL,
        params={"query": query, "format": "json"},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["results"]["bindings"]


def fetch_top_league_players(league_qid, target_season, limit=50, offset=0, max_retries=3):
    """
    Fetch male players for ONE league QID, restricted to memberships
    active during target_season, starting at `offset` in a deterministic
    ORDER BY ?player ordering — this is what makes pagination reliable
    (a plain LIMIT with no ORDER BY has no guaranteed order, so re-running
    it just returns an arbitrary, possibly-repeated slice).

    Retries on transient server errors (502/503/504), which Wikidata's
    public endpoint occasionally returns under load — these are not
    caused by anything wrong with the query itself.
    """
    query = LEAGUE_QUERY_TEMPLATE.format(
        league_qids=f"wd:{league_qid}", target_season=target_season, offset=offset, limit=limit
    )

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                WIKIDATA_SPARQL_URL,
                params={"query": query, "format": "json"},
                headers=HEADERS,
                timeout=60,
            )
            response.raise_for_status()
            return response.json()["results"]["bindings"]
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (502, 503, 504) and attempt < max_retries:
                wait = 5 * attempt  # 5s, then 10s, then 15s
                print(f"  Got {status} from Wikidata, retrying in {wait}s (attempt {attempt}/{max_retries})...")
                time.sleep(wait)
                continue
            raise  # not a retryable error, or out of retries — let it fail loudly


def parse_league_row(row):
    """
    Pulls every piece we need out of one Wikidata result row: player,
    their country, the club, the club's country, the league, and the
    start/end years of that specific club membership.
    """
    def get(field):
        return row.get(field, {}).get("value")

    def qid(field):
        val = get(field)
        return val.split("/")[-1] if val else None

    return {
        "player_wikidata_id": qid("player"),
        "player_name": get("playerLabel"),
        "birth_date": get("birthDate")[:10] if get("birthDate") else None,
        "birth_place": get("birthPlaceLabel"),
        "nationality_wikidata_id": qid("nationality"),
        "nationality_name": get("nationalityLabel"),
        "club_wikidata_id": qid("club"),
        "club_name": get("clubLabel"),
        "club_country_wikidata_id": qid("clubCountry"),
        "club_country_name": get("clubCountryLabel"),
        "league_wikidata_id": qid("league"),
        "league_name": get("leagueLabel"),
        "start_year": int(get("start")[:4]) if get("start") else None,
        "end_year": int(get("end")[:4]) if get("end") else None,
        "transfer_type": get("transferTypeLabel"),
    }


def get_or_create_player(conn, player_dict):
    """
    Upsert a single player, same pattern as the other get_or_create_*
    functions. Returns the player's row id so it can be used immediately
    to link into player_clubs.
    """
    if not player_dict["wikidata_id"] or not player_dict["name"]:
        return None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO players (wikidata_id, name, birth_date, birth_place, country_id)
            VALUES (%(wikidata_id)s, %(name)s, %(birth_date)s, %(birth_place)s, %(country_id)s)
            ON CONFLICT (wikidata_id) DO UPDATE SET
                name = EXCLUDED.name,
                birth_date = EXCLUDED.birth_date,
                birth_place = EXCLUDED.birth_place,
                country_id = EXCLUDED.country_id
            RETURNING id;
            """,
            player_dict,
        )
        player_id = cur.fetchone()[0]
    conn.commit()
    return player_id


def run_country_pull(conn):
    """Original approach: loop over specific countries. Player-only, no club/league linking."""
    countries = {
        "France": "Q142",
        "Brazil": "Q155",
        "England": "Q21",
        "Argentina": "Q414",
    }

    for country_name, qid in countries.items():
        print(f"Fetching players for {country_name}...")
        country_id = get_or_create_country(conn, country_name, qid)
        rows = fetch_players(country_qid=qid, limit=50)
        count = 0
        for row in rows:
            def get(field):
                return row.get(field, {}).get("value")
            wikidata_id = get("player").split("/")[-1] if get("player") else None
            player_dict = {
                "wikidata_id": wikidata_id,
                "name": get("playerLabel"),
                "birth_date": get("birthDate")[:10] if get("birthDate") else None,
                "birth_place": get("birthPlaceLabel"),
                "country_id": country_id,
            }
            if get_or_create_player(conn, player_dict) is not None:
                count += 1
        print(f"  Inserted/updated {count} players.")
        time.sleep(1)


def run_league_pull(conn, limit_per_league=200, target_season=CURRENT_SEASON_YEAR):
    """
    Pull players from EACH league separately, restricted to memberships
    active during target_season, continuing from wherever the last run
    left off (per league) so each run fetches NEW players rather than
    re-fetching the same page. Progress is saved to fetch_progress.json.
    """
    progress = load_progress()
    league_counts = {}
    players_seen = set()
    total_rows = 0

    for league_name, league_qid in LEAGUES.items():
        offset = progress.get(league_name, 0)
        print(f"Fetching {league_name} ({target_season}/{str(target_season + 1)[-2:]} season), offset {offset}...")
        rows = fetch_top_league_players(league_qid, target_season, limit=limit_per_league, offset=offset)
        parsed = [parse_league_row(r) for r in rows]
        total_rows += len(parsed)
        league_counts[league_name] = len(parsed)

        for r in parsed:
            if not r["player_wikidata_id"] or not r["player_name"]:
                continue  # skip incomplete rows

            # --- country ---
            country_id = None
            if r["nationality_wikidata_id"] and r["nationality_name"]:
                country_id = get_or_create_country(
                    conn, r["nationality_name"], r["nationality_wikidata_id"]
                )

            # --- player ---
            player_id = get_or_create_player(conn, {
                "wikidata_id": r["player_wikidata_id"],
                "name": r["player_name"],
                "birth_date": r["birth_date"],
                "birth_place": r["birth_place"],
                "country_id": country_id,
            })
            if player_id is None:
                continue

            players_seen.add(r["player_wikidata_id"])

            # --- club (skip row cleanly if club data is missing) ---
            if not r["club_wikidata_id"] or not r["club_name"]:
                continue

            club_country_id = None
            if r["club_country_wikidata_id"] and r["club_country_name"]:
                club_country_id = get_or_create_country(
                    conn, r["club_country_name"], r["club_country_wikidata_id"]
                )

            club_id = get_or_create_club(
                conn, r["club_wikidata_id"], r["club_name"], club_country_id
            )

            # --- league, and linking club to it for the current season ---
            league_id = get_or_create_league(conn, league_name, league_qid)
            link_club_to_league(conn, club_id, league_id, target_season)

            # --- player <-> club stint ---
            # start_year is required by the unique index; skip linking if
            # Wikidata didn't have a start date qualifier for this membership.
            if r["start_year"]:
                link_player_to_club(
                    conn, player_id, club_id, r["start_year"], r["end_year"], r["transfer_type"]
                )

        time.sleep(1)  # polite delay between leagues

        # Advance progress by however many rows actually came back (not
        # the requested limit) — this naturally stops growing once a
        # league runs out of new matches, rather than drifting past the
        # real end of that league's results.
        progress[league_name] = offset + len(parsed)
        save_progress(progress)
        if len(parsed) < limit_per_league:
            print(f"  (Got fewer than requested — {league_name} may be running low on new matches.)")

    print(f"Processed {total_rows} rows, {len(players_seen)} unique players, across {len(LEAGUES)} leagues.")
    print("Breakdown by league (row matches, not unique players):")
    for league_name in LEAGUES:
        count = league_counts.get(league_name, 0)
        flag = "  <-- got 0 rows, check this one" if count == 0 else ""
        print(f"  {league_name}: {count}{flag}")


def main():
    conn = psycopg2.connect(**DB_CONFIG)

    # Comment/uncomment whichever pull you want to run.
    run_league_pull(conn)
    # run_country_pull(conn)

    conn.close()
    print("Done. Data is now in your local Postgres database.")


if __name__ == "__main__":
    main()