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
    "User-Agent": "FootballTriviaApp/0.1 (https://github.com/jacarver1-svg/Football-Trivia-App-Project; contact: jacarver1@gmail.com)"
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


def parse_player(row, country_id):
    def get(field):
        return row.get(field, {}).get("value")

    wikidata_id = get("player").split("/")[-1] if get("player") else None
    return {
        "wikidata_id": wikidata_id,
        "name": get("playerLabel"),
        "birth_date": get("birthDate")[:10] if get("birthDate") else None,
        "birth_place": get("birthPlaceLabel"),
        "country_id": country_id,     # our own countries.id, not Wikidata's
        "current_club": get("clubLabel"),
    }


def insert_players(conn, players):
    with conn.cursor() as cur:
        for p in players:
            if not p["wikidata_id"] or not p["name"]:
                continue
            cur.execute(
                """
                INSERT INTO players (wikidata_id, name, birth_date, birth_place, country_id, current_club)
                VALUES (%(wikidata_id)s, %(name)s, %(birth_date)s, %(birth_place)s, %(country_id)s, %(current_club)s)
                ON CONFLICT (wikidata_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    birth_date = EXCLUDED.birth_date,
                    birth_place = EXCLUDED.birth_place,
                    country_id = EXCLUDED.country_id,
                    current_club = EXCLUDED.current_club;
                """,
                p,
            )
    conn.commit()


def main():
    # Each entry: display name -> Wikidata country QID.
    countries = {
        "France": "Q142",
        "Brazil": "Q155",
        "England": "Q21",
        "Argentina": "Q414",
    }

    conn = psycopg2.connect(**DB_CONFIG)

    for country_name, qid in countries.items():
        print(f"Fetching players for {country_name}...")

        # Resolve (or create) this country's row ONCE per country,
        # not once per player — every player from this batch shares
        # the same country_id.
        country_id = get_or_create_country(conn, country_name, qid)

        rows = fetch_players(country_qid=qid, limit=50)
        players = [parse_player(r, country_id) for r in rows]
        insert_players(conn, players)
        print(f"  Inserted/updated {len(players)} players.")
        time.sleep(1)  # polite delay between queries

    conn.close()
    print("Done. Data is now in your local Postgres database.")


if __name__ == "__main__":
    main()