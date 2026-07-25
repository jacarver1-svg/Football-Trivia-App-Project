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

import time
import requests
import psycopg2

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

# Be a good citizen: Wikidata asks for a descriptive User-Agent on all requests.
HEADERS = {
    "User-Agent": "FootballTriviaApp/0.1 (personal project; contact: jacarver1@gmail.com)"
}

DB_CONFIG = {
    "dbname": "football_trivia",
    "user": "postgres",       # change if needed
    "host": "localhost",
    "port": 5432,
}

# Example SPARQL query: top football players by a given nationality,
# with birth date, birth place, and current club.
# Q937857 = "association football player" (occupation)
# You can change the country QID (Q145 = United Kingdom, Q142 = France, etc.)
QUERY_TEMPLATE = """
SELECT ?player ?playerLabel ?birthDate ?birthPlaceLabel ?clubLabel ?nationalityLabel WHERE {{
  ?player wdt:P106 wd:Q937857.        # occupation: football player
  ?player wdt:P27 wd:{country_qid}.   # country of citizenship
  OPTIONAL {{ ?player wdt:P569 ?birthDate. }}
  OPTIONAL {{ ?player wdt:P19 ?birthPlace. }}
  OPTIONAL {{ ?player wdt:P54 ?club. }}
  OPTIONAL {{ ?player wdt:P27 ?nationality. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT {limit}
"""


def fetch_players(country_qid="Q142", limit=50):
    """Fetch players for a given country QID from Wikidata. Default: France."""
    query = QUERY_TEMPLATE.format(country_qid=country_qid, limit=limit)
    response = requests.get(
        WIKIDATA_SPARQL_URL,
        params={"query": query, "format": "json"},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["results"]["bindings"]


def parse_player(row):
    def get(field):
        return row.get(field, {}).get("value")

    wikidata_id = get("player").split("/")[-1] if get("player") else None
    return {
        "wikidata_id": wikidata_id,
        "name": get("playerLabel"),
        "birth_date": get("birthDate")[:10] if get("birthDate") else None,
        "birth_place": get("birthPlaceLabel"),
        "nationality": get("nationalityLabel"),
        "current_club": get("clubLabel"),
    }


def insert_players(conn, players):
    with conn.cursor() as cur:
        for p in players:
            if not p["wikidata_id"] or not p["name"]:
                continue
            cur.execute(
                """
                INSERT INTO players (wikidata_id, name, birth_date, birth_place, nationality, current_club)
                VALUES (%(wikidata_id)s, %(name)s, %(birth_date)s, %(birth_place)s, %(nationality)s, %(current_club)s)
                ON CONFLICT (wikidata_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    birth_date = EXCLUDED.birth_date,
                    birth_place = EXCLUDED.birth_place,
                    nationality = EXCLUDED.nationality,
                    current_club = EXCLUDED.current_club;
                """,
                p,
            )
    conn.commit()


def main():
    # A few example country QIDs to pull from. Add more as you like.
    countries = {
        "France": "Q142",
        "Brazil": "Q155",
        "England": "Q21",
        "Argentina": "Q414",
    }

    conn = psycopg2.connect(**DB_CONFIG)

    for country_name, qid in countries.items():
        print(f"Fetching players for {country_name}...")
        rows = fetch_players(country_qid=qid, limit=50)
        players = [parse_player(r) for r in rows]
        insert_players(conn, players)
        print(f"  Inserted/updated {len(players)} players.")
        time.sleep(1)  # polite delay between queries

    conn.close()
    print("Done. Data is now in your local Postgres database.")


if __name__ == "__main__":
    main()