"""
Pull football (soccer) player data from Wikidata into your local Postgres DB.

This is a ONE-TIME (or occasional) pull — not a live API dependency.
Once it's in your `football_trivia` database, it's yours to query forever
with no rate limits and no cost.

Setup:
    pip install requests psycopg2-binary python-dotenv

Usage:
    python fetch_wikidata.py

All tunable settings (leagues, season, DB connection, request pacing,
retry behavior) live in config.py — edit that file, not this one, to
change them.
"""

import os
import re
import json
import time
import requests
import psycopg2

from config import (
    DB_CONFIG,
    HEADERS,
    WIKIDATA_SPARQL_URL,
    WIKIDATA_API_URL,
    LEAGUES,
    TROPHY_COMPETITIONS,
    CURRENT_SEASON_YEAR,
    SPARQL_TIMEOUT,
    MAX_RETRIES,
    RETRY_BASE_WAIT,
    RATE_LIMIT_FALLBACK_WAIT,
    DEFAULT_LIMIT_PER_LEAGUE,
    ENRICH_SLEEP_SECONDS,
    LEAGUE_SLEEP_SECONDS,
    MANUAL_SEASON_CLUBS,
    TROPHY_SEASON_FILTER,
    FETCH_COMPETITION_CHAMPIONS,
    ENRICH_CLUB_TROPHIES,
    PROGRESS_FILE,
)

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
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
}}
LIMIT {limit}
"""

# Pulls players across ANY of the leagues listed in LEAGUES, in one request,
# instead of looping per country. Captures each player's own nationality
# inline, since we're no longer fixing the country ahead of time.
# Finds the specific season ITEM for a competition (e.g. "2024-25 Premier
# League"), not the competition item itself. Same P3450 relationship
# already used by COMPETITION_CHAMPIONS_QUERY for trophy history.
SEASON_ITEM_QUERY = """
SELECT ?season ?seasonLabel ?startTime WHERE {{
  ?season wdt:P3450 wd:{competition_qid}.  # season is an edition of this competition
  OPTIONAL {{ ?season wdt:P580 ?startTime. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
}}
"""

# P1923 ("participating team") is usually stored on the season item, but
# editors often instead (or additionally) record the reverse statement --
# P1344 ("participant in") on the CLUB item, pointing at the season. The
# two are supposed to be reciprocal but frequently aren't kept in sync, so
# checking both surfaces far more leagues than P1923 alone.
SEASON_PARTICIPANTS_QUERY = """
SELECT DISTINCT ?team ?teamLabel ?teamCountry ?teamCountryLabel WHERE {{
  {{ wd:{season_qid} wdt:P1923 ?team. }}
  UNION
  {{ ?team wdt:P1344 wd:{season_qid}. }}
  OPTIONAL {{ ?team wdt:P17 ?teamCountry. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
}}
"""

# Pulls players ONLY from the pre-confirmed club list for this league's
# season (resolved via fetch_league_season_clubs() below) -- so a player
# can only be attributed to a league/season if their club was actually
# verified to be IN that league that season, not just whatever a club's
# current P118 happens to say.
SEASON_CLUB_PLAYERS_QUERY = """
SELECT ?player ?playerLabel ?birthDate ?birthPlaceLabel ?position ?positionLabel
       ?club ?start ?end ?transferType ?transferTypeLabel
       ?nationality ?nationalityLabel WHERE {{
  VALUES ?club {{ {club_qids} }}

  ?player wdt:P106 wd:Q937857.        # occupation: football player
  ?player wdt:P21 wd:Q6581097.        # sex or gender: male
  ?player wdt:P27 ?nationality.       # country of citizenship

  ?player p:P54 ?membership.          # a specific club-membership fact
  ?membership ps:P54 ?club.           # ...at one of the confirmed season clubs

  ?membership pq:P580 ?start.                          # start time (required — needed for the season filter below)
  OPTIONAL {{ ?membership pq:P582 ?end. }}            # qualifier: end time
  OPTIONAL {{ ?membership pq:P1642 ?transferType. }}  # qualifier: acquisition transaction (loan/transfer/free transfer)
  OPTIONAL {{ ?player wdt:P569 ?birthDate. }}
  OPTIONAL {{ ?player wdt:P19 ?birthPlace. }}
  OPTIONAL {{ ?player wdt:P413 ?position. }}          # position played on team / speciality

  # Same active-during-the-season filter as before, now layered on top
  # of a club list that's already confirmed correct for this season.
  FILTER(YEAR(?start) <= {target_season})
  FILTER(!BOUND(?end) || YEAR(?end) >= {target_season})

  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
}}
ORDER BY ?player
OFFSET {offset}
LIMIT {limit}
"""

# NOTE: the template above relies on ?club wdt:P118 ?league, which
# reflects a club's CURRENT league only — NOT which league it was in for
# whatever target_season you actually asked for. This means historical
# season pulls using this template only ever return TODAY's clubs, not
# that season's real roster (silently wrong for any promoted/relegated
# club). Kept here for reference; run_league_pull() now uses the
# season-aware two-stage approach below instead.

SEASON_CLUBS_QUERY = """
SELECT ?season ?seasonLabel ?startTime ?club ?clubLabel WHERE {{
  ?season wdt:P3450 wd:{league_qid}.   # season is an edition of this league
  ?season wdt:P1923 ?club.             # participating team — this is the season-accurate
                                         # club list, unlike P118 which only reflects today
  OPTIONAL {{ ?season wdt:P580 ?startTime. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
}}
"""

PLAYERS_BY_CLUBS_QUERY = """
SELECT ?player ?playerLabel ?birthDate ?birthPlaceLabel ?position ?positionLabel
       ?club ?clubLabel ?clubCountry ?clubCountryLabel
       ?nationality ?nationalityLabel
       ?start ?end ?transferType ?transferTypeLabel WHERE {{
  VALUES ?club {{ {club_qids} }}       # the EXACT clubs found for this season via P1923,
                                         # not a P118-based current-league filter

  ?player wdt:P106 wd:Q937857.
  ?player wdt:P21 wd:Q6581097.
  ?player wdt:P27 ?nationality.

  ?player p:P54 ?membership.
  ?membership ps:P54 ?club.

  OPTIONAL {{ ?club wdt:P17 ?clubCountry. }}
  ?membership pq:P580 ?start.
  OPTIONAL {{ ?membership pq:P582 ?end. }}
  OPTIONAL {{ ?membership pq:P1642 ?transferType. }}
  OPTIONAL {{ ?player wdt:P569 ?birthDate. }}
  OPTIONAL {{ ?player wdt:P19 ?birthPlace. }}
  OPTIONAL {{ ?player wdt:P413 ?position. }}

  FILTER(YEAR(?start) <= {target_season})
  FILTER(!BOUND(?end) || YEAR(?end) >= {target_season})

  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
}}
LIMIT {limit}
"""


def fetch_season_clubs(league_qid, target_season, max_retries=MAX_RETRIES):
    """
    Finds the ACTUAL clubs that played in a league during target_season,
    via each season edition's 'participating team' (P1923) — unlike
    P118, this is season-specific, so it correctly reflects promoted/
    relegated clubs instead of always returning today's lineup.

    Returns a dict of {club_wikidata_id: club_name} for clubs found in
    the season matching target_season.
    """
    query = SEASON_CLUBS_QUERY.format(league_qid=league_qid)
    rows = run_sparql_query(query, max_retries=max_retries)

    matching_clubs = {}
    for row in rows:
        season_name = row.get("seasonLabel", {}).get("value")
        start_time = row.get("startTime", {}).get("value")
        season_year = extract_season_start_year(season_name, start_time)
        if season_year != target_season:
            continue
        club_uri = row.get("club", {}).get("value")
        club_qid = club_uri.split("/")[-1] if club_uri else None
        club_name = row.get("clubLabel", {}).get("value")
        if club_qid and club_name:
            matching_clubs[club_qid] = club_name
    return matching_clubs


def fetch_players_for_clubs(club_qids, target_season, limit, max_retries=MAX_RETRIES):
    """
    Pulls player squads for an EXPLICIT list of club QIDs (a specific
    season's real roster, from fetch_season_clubs), instead of filtering
    by a league's current P118 membership.
    """
    values_str = " ".join(f"wd:{qid}" for qid in club_qids)
    query = PLAYERS_BY_CLUBS_QUERY.format(club_qids=values_str, target_season=target_season, limit=limit)
    return run_sparql_query(query, max_retries=max_retries)


CLUB_DETAILS_QUERY = """
SELECT ?stadiumLabel ?founded WHERE {{
  OPTIONAL {{ wd:{club_qid} wdt:P115 ?stadium. }}   # home venue
  OPTIONAL {{ wd:{club_qid} wdt:P571 ?founded. }}    # inception (founding date)
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
}}
LIMIT 1
"""

CLUB_TROPHIES_QUERY = """
SELECT ?award ?awardLabel ?year ?parentLeague ?parentLeagueLabel WHERE {{
  wd:{club_qid} p:P2522 ?awardStmt.       # competition won (the correct property for league/cup titles)
  ?awardStmt ps:P2522 ?award.
  ?awardStmt pq:P585 ?year.               # qualifier: point in time (year won) — required, not optional,
                                            # so the FILTER below has something to check
  OPTIONAL {{ ?award wdt:P3450 ?parentLeague. }}  # if this IS a season-specific league title,
                                                    # this points back to the parent league itself
  {year_filter}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
}}
"""


def run_sparql_query(query, max_retries=MAX_RETRIES):
    """
    Shared request helper for every SPARQL query in this script.
    Handles three kinds of transient failure differently:
      - Timeout: the query itself took too long for Wikidata to compute
        (common with the heavier qualifier-based queries this script
        uses). Retried with a short backoff — this is not a rate-limit
        issue, just a slow query, so a plain retry is appropriate.
      - 502/503/504: server-side infrastructure hiccups — also safe to
        retry with a short, fixed backoff.
      - 429: Wikidata EXPLICITLY telling us to slow down. This is not a
        random hiccup — it's a real rate-limit signal. We respect their
        Retry-After header if they send one (the correct way to respond
        to a 429), and fall back to a longer wait if they don't.
    """
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                WIKIDATA_SPARQL_URL,
                params={"query": query, "format": "json"},
                headers=HEADERS,
                timeout=SPARQL_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()["results"]["bindings"]

        except requests.exceptions.Timeout:
            if attempt < max_retries:
                wait = RETRY_BASE_WAIT * attempt
                print(f"  Query timed out (>{SPARQL_TIMEOUT}s). Retrying in {wait}s "
                      f"(attempt {attempt}/{max_retries})...")
                time.sleep(wait)
                continue
            raise

        except requests.exceptions.ConnectionError:
            # The connection itself was dropped mid-request (no HTTP status
            # at all) — a stronger signal than a timeout or a 502. Could be
            # a transient network blip, but could also mean the server is
            # actively rejecting sustained traffic from this IP. Backs off
            # much more conservatively than the other transient errors.
            if attempt < max_retries:
                wait = RATE_LIMIT_FALLBACK_WAIT * attempt
                print(f"  Connection was reset by the remote host. Waiting {wait}s before "
                      f"retrying (attempt {attempt}/{max_retries})...")
                time.sleep(wait)
                continue
            raise

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None

            if status == 429 and attempt < max_retries:
                retry_after = e.response.headers.get("Retry-After")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else RATE_LIMIT_FALLBACK_WAIT * attempt
                print(f"  Got 429 (rate limited) from Wikidata. Waiting {wait}s before retrying "
                      f"(attempt {attempt}/{max_retries})...")
                time.sleep(wait)
                continue

            if status in (502, 503, 504) and attempt < max_retries:
                wait = RETRY_BASE_WAIT * attempt
                print(f"  Got {status} from Wikidata, retrying in {wait}s (attempt {attempt}/{max_retries})...")
                time.sleep(wait)
                continue

            raise  # not retryable, or out of retries — let it fail loudly


def fetch_club_details(club_qid, max_retries=3):
    """Fetch a club's stadium and founding year. Returns one row or None."""
    query = CLUB_DETAILS_QUERY.format(club_qid=club_qid)
    rows = run_sparql_query(query, max_retries=max_retries)
    return rows[0] if rows else None

def fetch_club_basic_info(club_qids, max_retries=MAX_RETRIES):
    """Looks up name/country for a manually-supplied list of club QIDs."""
    if not club_qids:
        return []
    values = " ".join(f"wd:{q}" for q in club_qids)
    query = f"""
    SELECT ?team ?teamLabel ?teamCountry ?teamCountryLabel WHERE {{
      VALUES ?team {{ {values} }}
      OPTIONAL {{ ?team wdt:P17 ?teamCountry. }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
    }}
    """
    rows = run_sparql_query(query, max_retries=max_retries)
    clubs = []
    for row in rows:
        team_uri = row.get("team", {}).get("value")
        team_qid = team_uri.split("/")[-1] if team_uri else None
        team_name = row.get("teamLabel", {}).get("value")
        country_uri = row.get("teamCountry", {}).get("value")
        country_qid = country_uri.split("/")[-1] if country_uri else None
        country_name = row.get("teamCountryLabel", {}).get("value")
        if team_qid and team_name:
            clubs.append((team_qid, team_name, country_qid, country_name))
    return clubs

def fetch_club_trophies(club_qid, max_retries=3, season_filter=TROPHY_SEASON_FILTER):
    """
    Fetch every 'competition won' entry for a club, with the year it was
    won. If season_filter is set (a year, e.g. 2025), only trophies won
    that year are fetched — otherwise, full trophy history is pulled.
    """
    year_filter = f"FILTER(YEAR(?year) = {season_filter})" if season_filter else ""
    query = CLUB_TROPHIES_QUERY.format(club_qid=club_qid, year_filter=year_filter)
    return run_sparql_query(query, max_retries=max_retries)


def update_club_details(conn, club_id, stadium, founded_year):
    """Fills in stadium/founded_year without overwriting existing values with NULL."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE clubs SET
                stadium = COALESCE(%s, stadium),
                founded_year = COALESCE(%s, founded_year)
            WHERE id = %s;
            """,
            (stadium, founded_year, club_id),
        )
    conn.commit()


def get_or_create_trophy(conn, wikidata_id, name, trophy_type="team", parent_league_id=None,
                          country_id=None, scope=None):
    """Same upsert pattern as everything else, for trophies."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM trophies WHERE wikidata_id = %s;", (wikidata_id,))
        row = cur.fetchone()
        if row:
            updates, params = [], []
            if parent_league_id is not None:
                updates.append("parent_league_id = %s")
                params.append(parent_league_id)
            if country_id is not None:
                updates.append("country_id = %s")
                params.append(country_id)
            if scope is not None:
                updates.append("scope = %s")
                params.append(scope)
            if updates:
                params.append(row[0])
                cur.execute(f"UPDATE trophies SET {', '.join(updates)} WHERE id = %s;", params)
                conn.commit()
            return row[0]
        cur.execute(
            """
            INSERT INTO trophies (wikidata_id, name, type, parent_league_id, country_id, scope)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (wikidata_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING id;
            """,
            (wikidata_id, name, trophy_type, parent_league_id, country_id, scope),
        )
        trophy_id = cur.fetchone()[0]
    conn.commit()
    return trophy_id

def search_club_qid(name, max_retries=MAX_RETRIES):
    """
    Searches Wikidata by name via the wbsearchentities API (a plain text
    search, not SPARQL) and returns candidate (qid, label, description)
    matches. Ambiguous names return multiple candidates -- always check the
    description (it usually says the club's sport/country) before picking one.
    """
    params = {
        "action": "wbsearchentities",
        "search": name,
        "language": "en",
        "type": "item",
        "format": "json",
        "limit": 10,
    }
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(WIKIDATA_API_URL, params=params, headers=HEADERS, timeout=SPARQL_TIMEOUT)
            response.raise_for_status()
            results = response.json().get("search", [])
            return [(r["id"], r.get("label", ""), r.get("description", "")) for r in results]
        except requests.exceptions.RequestException:
            if attempt < max_retries:
                time.sleep(RETRY_BASE_WAIT * attempt)
                continue
            raise


def build_manual_season_clubs_snippet(league_name, target_season, club_names):
    """
    Semi-automates a MANUAL_SEASON_CLUBS entry: takes a plain list of club
    names (copy-paste them straight out of the Wikipedia season page's
    table) and resolves each to a QID, printing a ready-to-paste config.py
    snippet with the club name kept as an inline comment for auditability.
    Ambiguous or unmatched names are flagged instead of guessed -- fix
    those lines by hand before pasting.
    """
    print(f'MANUAL_SEASON_CLUBS[("{league_name}", {target_season})] = [')
    for name in club_names:
        candidates = search_club_qid(name)
        football_candidates = [
            c for c in candidates
            if any(word in c[2].lower() for word in ("football", "soccer", "f.c.", "fc "))
        ]
        if len(football_candidates) == 1:
            qid, label, desc = football_candidates[0]
            print(f'    "{qid}",  # {label} -- {desc}')
        elif football_candidates:
            print(f'    # AMBIGUOUS for "{name}", pick one:')
            for qid, label, desc in football_candidates:
                print(f'    #   "{qid}",  # {label} -- {desc}')
        else:
            print(f'    # NO FOOTBALL-CLUB MATCH for "{name}" -- check spelling or search manually')
        time.sleep(0.5)
    print("]")

def preview_club_roster(club_qid, target_season, limit=30):
    """
    Sanity check for a single club QID before trusting it: prints the
    players Wikidata has on record with a club membership active during
    target_season. Catches wrong QIDs (reserve team, a same-named club in
    a different country, a women's team, etc.) before they pollute your
    database -- if the names printed don't look like who you'd expect at
    that club that season, the QID is probably wrong.
    """
    rows = fetch_season_club_players([club_qid], target_season, limit=limit)
    if not rows:
        print(f"  No players found for {club_qid} in {target_season} -- wrong QID, or no qualified P54 data for that season.")
        return
    print(f"  {len(rows)} player(s) found for {club_qid} in {target_season}:")
    for row in rows:
        name = row.get("playerLabel", {}).get("value", "?")
        pos = row.get("positionLabel", {}).get("value", "")
        print(f"    - {name}" + (f" ({pos})" if pos else ""))

def link_club_trophy(conn, club_id, trophy_id, season_start_year):
    """Insert a club_trophies row, ignoring if it already exists."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO club_trophies (club_id, trophy_id, season_start_year)
            VALUES (%s, %s, %s)
            ON CONFLICT (club_id, trophy_id, season_start_year) DO NOTHING;
            """,
            (club_id, trophy_id, season_start_year),
        )
    conn.commit()


def club_already_enriched(conn, club_id):
    """
    Checks the DATABASE (not just this run's in-memory set) for whether
    a club already has enrichment data — so re-running the script on a
    different season doesn't waste requests re-fetching stadium/founding
    info for clubs you already have. Trophies aren't checked here since
    a club could genuinely win a new trophy over time, so those still
    get re-fetched each run (harmless — ON CONFLICT DO NOTHING skips
    exact duplicates).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT stadium, founded_year FROM clubs WHERE id = %s;", (club_id,))
        row = cur.fetchone()
        return row is not None and row[0] is not None and row[1] is not None


def enrich_club(conn, club_id, club_qid, skip_details_if_present=True):
    """
    One-time-per-club lookup for stadium, founding year, and every trophy
    Wikidata has on record for them (with a year attached). Called once
    per unique club per run — see the `enriched_clubs` set in
    run_league_pull(), so a club with 30 players doesn't trigger 30
    redundant Wikidata requests within the same run.
    """
    if skip_details_if_present and club_already_enriched(conn, club_id):
        print(f"    (club already has stadium/founded_year, skipping details fetch)")
    else:
        details = fetch_club_details(club_qid)
        if details:
            def get(field):
                return details.get(field, {}).get("value")
            stadium = get("stadiumLabel")
            founded_raw = get("founded")
            founded_year = int(founded_raw[:4]) if founded_raw else None
            update_club_details(conn, club_id, stadium, founded_year)
        time.sleep(ENRICH_SLEEP_SECONDS)

    if not ENRICH_CLUB_TROPHIES:
        return

    trophy_rows = fetch_club_trophies(club_qid)
    for row in trophy_rows:
        award_uri = row.get("award", {}).get("value")
        award_qid = award_uri.split("/")[-1] if award_uri else None
        award_name = row.get("awardLabel", {}).get("value")
        year_raw = row.get("year", {}).get("value")
        season_year = int(year_raw[:4]) if year_raw else None

        parent_league_uri = row.get("parentLeague", {}).get("value")
        parent_league_qid = parent_league_uri.split("/")[-1] if parent_league_uri else None
        parent_league_id = None
        if parent_league_qid:
            # Only resolves if this parent league is one you actually
            # track (matches a wikidata_id already in your leagues
            # table) — league titles from leagues outside your tracked
            # five will just leave this NULL, which is fine.
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM leagues WHERE wikidata_id = %s;", (parent_league_qid,))
                match = cur.fetchone()
                parent_league_id = match[0] if match else None

        # Skip anything missing a year — a trophy we can't date isn't
        # useful for "how many has X won" or "who won it in season Y"
        # questions, and season_start_year is NOT NULL in the schema.
        if not award_qid or not award_name or not season_year:
            continue

        trophy_id = get_or_create_trophy(conn, award_qid, award_name, "team", parent_league_id)
        link_club_trophy(conn, club_id, trophy_id, season_year)
    time.sleep(ENRICH_SLEEP_SECONDS)


COMPETITION_CHAMPIONS_QUERY = """
SELECT ?season ?seasonLabel ?startTime ?winner ?winnerLabel ?winnerCountry ?winnerCountryLabel WHERE {{
  ?season wdt:P3450 wd:{competition_qid}.  # season is an edition of this competition
  ?season wdt:P1346 ?winner.                # winner of that season — this is authoritative,
                                              # maintained on the competition/season page itself,
                                              # not dependent on each club backfilling their own page
  OPTIONAL {{ ?season wdt:P580 ?startTime. }}  # season start date, when recorded on the season item
  OPTIONAL {{ ?winner wdt:P17 ?winnerCountry. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
}}
"""


def fetch_competition_champions(competition_qid, max_retries=MAX_RETRIES):
    """
    Fetch EVERY known season and its winner for one competition, in a
    single request — far more complete than asking each club what
    they've won, since competition/season pages tend to be far more
    consistently maintained than individual club pages.
    """
    query = COMPETITION_CHAMPIONS_QUERY.format(competition_qid=competition_qid)
    return run_sparql_query(query, max_retries=max_retries)


def extract_season_start_year(season_label, start_time_value):
    """
    Prefer the season item's own start-time property when present (most
    reliable). Falls back to parsing the leading 4-digit year out of the
    season's label (e.g. "2015-16 Premier League" -> 2015), since that
    format is consistent for these competitions even when P580 is missing.
    """
    if start_time_value:
        return int(start_time_value[:4])
    if season_label:
        match = re.match(r"^(\d{4})", season_label)
        if match:
            return int(match.group(1))
    return None


def fetch_predecessor_competitions(competition_qid, max_retries=MAX_RETRIES):
    """
    Walks Wikidata's 'replaces' (P1365) chain backward from a competition,
    returning EVERY predecessor it can find, however many rebrands deep
    (the '+' in the property path means one-or-more hops, so this finds
    the whole chain in a single request, not just the most recent rename).

    E.g. querying Premier League's QID returns Football League First
    Division, and would keep walking further back if that itself had a
    'replaces' link.
    """
    query = f"""
    SELECT ?predecessor ?predecessorLabel WHERE {{
      wd:{competition_qid} wdt:P1365+ ?predecessor.
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
    }}
    """
    return run_sparql_query(query, max_retries=max_retries)


def fetch_competition_country(competition_qid, max_retries=MAX_RETRIES):
    """
    Determines whether a competition belongs to a single country (a
    domestic league or cup) or spans multiple countries (continental/
    international, like the Champions League). Queried once per
    competition — this is a property of the competition as a whole, not
    of any individual season, so it doesn't need to be re-checked per row.

    Returns (country_wikidata_id, country_name, scope) — country fields
    are None when no single country applies (scope='continental').
    """
    query = f"""
    SELECT ?country ?countryLabel WHERE {{
      OPTIONAL {{ wd:{competition_qid} wdt:P17 ?country. }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt". }}
    }}
    LIMIT 1
    """
    rows = run_sparql_query(query, max_retries=max_retries)
    if rows and rows[0].get("country"):
        country_uri = rows[0]["country"]["value"]
        country_qid = country_uri.split("/")[-1]
        country_name = rows[0].get("countryLabel", {}).get("value")
        return (country_qid, country_name, "domestic")
    return (None, None, "continental")


def process_champion_rows(conn, rows, parent_league_id, country_id=None, scope=None):
    """
    Shared logic for turning SPARQL champion rows into database rows —
    used for both a competition's own history AND any predecessor
    competitions found via fetch_predecessor_competitions(). Passing the
    SAME parent_league_id/country_id/scope for both is what merges
    pre-rebrand and post-rebrand titles into one continuous history
    under a single leagues row, tagged consistently.
    """
    saved = 0
    for row in rows:
        def get(field):
            return row.get(field, {}).get("value")

        season_uri = get("season")
        season_qid = season_uri.split("/")[-1] if season_uri else None
        season_name = get("seasonLabel")
        start_time = get("startTime")
        season_start_year = extract_season_start_year(season_name, start_time)

        winner_uri = get("winner")
        winner_qid = winner_uri.split("/")[-1] if winner_uri else None
        winner_name = get("winnerLabel")

        if not season_qid or not season_name or not winner_qid or not winner_name or not season_start_year:
            continue  # incomplete row — skip rather than store a guess

        winner_country_id = None
        winner_country_qid = get("winnerCountry")
        winner_country_qid = winner_country_qid.split("/")[-1] if winner_country_qid else None
        winner_country_name = get("winnerCountryLabel")
        if winner_country_qid and winner_country_name:
            winner_country_id = get_or_create_country(conn, winner_country_name, winner_country_qid)

        club_id = get_or_create_club(conn, winner_qid, winner_name, winner_country_id)
        trophy_id = get_or_create_trophy(
            conn, season_qid, season_name, "team", parent_league_id,
            country_id=country_id, scope=scope,
        )
        link_club_trophy(conn, club_id, trophy_id, season_start_year)
        saved += 1
    return saved


def preview_predecessor_chains():
    """
    Shows what fetch_all_competition_champions() WOULD pull in via the
    'replaces' chain for every tracked competition — without writing
    anything to the database. Run this first, review the output, and
    only enable include_predecessors=True once you're satisfied the
    chains look right (no unexpected competitions, no suspiciously long
    chains for a competition you don't recognize).
    """
    for competition_name, competition_qid in TROPHY_COMPETITIONS.items():
        predecessors = fetch_predecessor_competitions(competition_qid)
        if not predecessors:
            print(f"{competition_name}: no predecessors found.")
            continue
        names = [p.get("predecessorLabel", {}).get("value", "?") for p in predecessors]
        print(f"{competition_name}: would also merge in -> {', '.join(names)}")


def fetch_all_competition_champions(conn, include_predecessors=False):
    """
    Populates trophies/club_trophies with COMPLETE champion history for
    every competition in TROPHY_COMPETITIONS — one request per
    competition, not one per club. Run this once per session; it's
    independent of the player-pulling logic in run_league_pull().

    If include_predecessors is True, also walks each competition's
    'replaces' chain (rebrands like Premier League <- Football League
    First Division) and folds those older titles into the SAME leagues
    row, so trophy counts reflect the competition's full history
    regardless of what it was called at the time.
    """
    for competition_name, competition_qid in TROPHY_COMPETITIONS.items():
        print(f"Fetching champion history for {competition_name}...")

        # If this competition IS one of your tracked leagues, its season
        # trophies should link back via parent_league_id.
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM leagues WHERE wikidata_id = %s;", (competition_qid,))
            match = cur.fetchone()
            parent_league_id = match[0] if match else None

        # Domestic (single country) vs continental — checked once per
        # competition, not per season.
        country_qid, country_name, scope = fetch_competition_country(competition_qid)
        country_id = None
        if country_qid and country_name:
            country_id = get_or_create_country(conn, country_name, country_qid)
        print(f"  Scope: {scope}" + (f" ({country_name})" if country_name else ""))

        rows = fetch_competition_champions(competition_qid)
        saved = process_champion_rows(conn, rows, parent_league_id, country_id=country_id, scope=scope)

        if include_predecessors:
            predecessors = fetch_predecessor_competitions(competition_qid)
            for pred in predecessors:
                pred_uri = pred.get("predecessor", {}).get("value")
                pred_qid = pred_uri.split("/")[-1] if pred_uri else None
                pred_name = pred.get("predecessorLabel", {}).get("value", "unknown predecessor")
                if not pred_qid:
                    continue
                print(f"  Also fetching predecessor: {pred_name}...")
                pred_rows = fetch_competition_champions(pred_qid)
                # Same parent_league_id as above — this is what merges
                # the pre-rebrand titles into the current league's history.
                saved += process_champion_rows(
                    conn, pred_rows, parent_league_id, country_id=country_id, scope=scope
                )
                time.sleep(LEAGUE_SLEEP_SECONDS)
            saved += 1

        print(f"  {saved} champion season(s) recorded for {competition_name}.")
        time.sleep(LEAGUE_SLEEP_SECONDS)

# Progress tracking helpers use PROGRESS_FILE, imported from config.py.


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
        cur.execute("SELECT id FROM leagues WHERE wikidata_id = %s;", (wikidata_id,))
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


def stint_dates_look_valid(start_year, end_year, birth_date):
    """
    Sanity-checks a club stint's dates before it gets inserted, catching
    the same kinds of bad Wikidata qualifier data your diagnostic
    queries look for after the fact (e.g. joining a club before being
    born, or an end_year before the start_year). Returns (is_valid,
    cleaned_start_year, cleaned_end_year, reason) — invalid individual
    fields get nulled out rather than rejecting the whole row outright,
    since often only ONE of the two dates is actually wrong.
    """
    reason = None

    if end_year is not None and start_year is not None and end_year < start_year:
        reason = f"end_year ({end_year}) before start_year ({start_year}) — dropping end_year"
        end_year = None

    if start_year is not None and birth_date is not None:
        birth_year = int(str(birth_date)[:4])
        if start_year < birth_year:
            reason = f"start_year ({start_year}) before birth year ({birth_year}) — dropping start_year"
            start_year = None

    return (start_year, end_year, reason)


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


def fetch_league_season_clubs(competition_qid, target_season, league_name=None, max_retries=MAX_RETRIES):
    """
    Resolves the actual set of clubs Wikidata records as having PARTICIPATED
    in this competition during target_season, via the season item's P1923
    statements -- not via each club's current (and frequently stale/
    overwritten-on-promotion) P118 value.

    Returns a list of (club_qid, club_name, club_country_qid, club_country_name)
    tuples. Empty list means no season item / no P1923 data was found for
    this competition+year (common for lower divisions or older seasons —
    check manually before assuming zero clubs is correct).
    """
    if league_name and (league_name, target_season) in MANUAL_SEASON_CLUBS:
            return fetch_club_basic_info(MANUAL_SEASON_CLUBS[(league_name, target_season)], max_retries=max_retries)

    season_query = SEASON_ITEM_QUERY.format(competition_qid=competition_qid)
    season_rows = run_sparql_query(season_query, max_retries=max_retries)

    season_qid = None
    for row in season_rows:
        season_uri = row.get("season", {}).get("value")
        label = row.get("seasonLabel", {}).get("value")
        start_time = row.get("startTime", {}).get("value")
        if season_uri and extract_season_start_year(label, start_time) == target_season:
            season_qid = season_uri.split("/")[-1]
            break

    if not season_qid:
        return []

    participants_query = SEASON_PARTICIPANTS_QUERY.format(season_qid=season_qid)
    rows = run_sparql_query(participants_query, max_retries=max_retries)

    clubs = []
    for row in rows:
        team_uri = row.get("team", {}).get("value")
        team_qid = team_uri.split("/")[-1] if team_uri else None
        team_name = row.get("teamLabel", {}).get("value")
        country_uri = row.get("teamCountry", {}).get("value")
        country_qid = country_uri.split("/")[-1] if country_uri else None
        country_name = row.get("teamCountryLabel", {}).get("value")
        if team_qid and team_name:
            clubs.append((team_qid, team_name, country_qid, country_name))
    return clubs


def fetch_season_club_players(club_qids, target_season, limit=50, offset=0, max_retries=MAX_RETRIES):
    """
    Fetch players for a pre-confirmed set of clubs (the real season roster
    from fetch_league_season_clubs()), restricted to memberships active
    during target_season. Same OFFSET/LIMIT pagination as before.
    """
    if not club_qids:
        return []
    club_values = " ".join(f"wd:{qid}" for qid in club_qids)
    query = SEASON_CLUB_PLAYERS_QUERY.format(
        club_qids=club_values, target_season=target_season, offset=offset, limit=limit
    )
    return run_sparql_query(query, max_retries=max_retries)


def preview_season_clubs(target_season=CURRENT_SEASON_YEAR, leagues=None):
    """
    Dry run: prints which clubs would be linked to each league for
    target_season, without touching the database. Run this FIRST after
    changing CURRENT_SEASON_YEAR (or before trusting this approach at all)
    to sanity-check the resolved club list against what you know to be
    true (right count, no obviously wrong/missing clubs).
    """
    for league_name, league_qid in (leagues or LEAGUES).items():
        clubs = fetch_league_season_clubs(league_qid, target_season, league_name=league_name)
        print(f"{league_name} ({target_season}/{str(target_season + 1)[-2:]}): {len(clubs)} clubs")
        for club_qid, club_name, _, _ in clubs:
            print(f"  - {club_name} ({club_qid})")
        time.sleep(LEAGUE_SLEEP_SECONDS)

def diagnose_league_seasons(target_season, leagues=None):
    """
    For each league, checks whether Wikidata has (a) a season item matching
    target_season at all, and (b) whether that season item has P1923
    (participating team) data populated yet. Distinguishes "no season item
    found" from "season item exists but is empty" -- preview_season_clubs()
    only shows you the end result (0 clubs), not which of those two this is.
    """
    for league_name, league_qid in (leagues or LEAGUES).items():
        season_rows = run_sparql_query(SEASON_ITEM_QUERY.format(competition_qid=league_qid))

        season_qid = None
        matched_label = None
        for row in season_rows:
            season_uri = row.get("season", {}).get("value")
            label = row.get("seasonLabel", {}).get("value")
            start_time = row.get("startTime", {}).get("value")
            if season_uri and extract_season_start_year(label, start_time) == target_season:
                season_qid = season_uri.split("/")[-1]
                matched_label = label
                break

        if not season_qid:
            print(f"{league_name}: NO SEASON ITEM found for {target_season} ({len(season_rows)} season items exist total for this competition)")
            time.sleep(LEAGUE_SLEEP_SECONDS)
            continue

        participants = run_sparql_query(SEASON_PARTICIPANTS_QUERY.format(season_qid=season_qid))
        status = "OK" if participants else "EMPTY -- season item exists but no P1923 data yet"
        print(f"{league_name}: {matched_label!r} ({season_qid}) -> {len(participants)} clubs [{status}]")
        time.sleep(LEAGUE_SLEEP_SECONDS)

def parse_season_player_row(row):
    """
    Pulls player + club-membership fields out of one SEASON_CLUB_PLAYERS_QUERY
    result row. Club identity/name/country are NOT re-parsed here — they're
    already known from fetch_league_season_clubs(), which run_league_pull()
    uses to map ?club back to the right club_id.
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
        "position": get("positionLabel"),
        "nationality_wikidata_id": qid("nationality"),
        "nationality_name": get("nationalityLabel"),
        "club_wikidata_id": qid("club"),
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
            INSERT INTO players (wikidata_id, name, birth_date, birth_place, country_id, position)
            VALUES (%(wikidata_id)s, %(name)s, %(birth_date)s, %(birth_place)s, %(country_id)s, %(position)s)
            ON CONFLICT (wikidata_id) DO UPDATE SET
                name = EXCLUDED.name,
                birth_date = EXCLUDED.birth_date,
                birth_place = EXCLUDED.birth_place,
                country_id = EXCLUDED.country_id,
                position = EXCLUDED.position
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
                "position": None,  # this older query path doesn't fetch position
            }
            if get_or_create_player(conn, player_dict) is not None:
                count += 1
        print(f"  Inserted/updated {count} players.")
        time.sleep(1)


def run_league_pull(conn, limit_per_league=DEFAULT_LIMIT_PER_LEAGUE, target_season=CURRENT_SEASON_YEAR):
    """
    Pull players from EACH league separately, restricted to memberships
    active during target_season. Club-to-league linkage now comes from the
    season item's confirmed participant list (P1923), not from each club's
    current P118 value, so promoted/relegated clubs land in the correct
    season instead of whichever league they're in as of right now.

    `target_season` controls WHICH season is pulled (defaults to
    CURRENT_SEASON_YEAR from config.py). `limit_per_league` controls how
    many player rows are fetched per league per run (defaults to
    DEFAULT_LIMIT_PER_LEAGUE from config.py) — both overridable by passing
    different values in, e.g. run_league_pull(conn, limit_per_league=500, target_season=2022).
    """
    progress = load_progress()
    league_counts = {}
    players_seen = set()
    enriched_clubs = set()
    total_rows = 0

    for league_name, league_qid in LEAGUES.items():
        print(f"Resolving {league_name} clubs for the {target_season}/{str(target_season + 1)[-2:]} season...")
        season_clubs = fetch_league_season_clubs(league_qid, target_season, league_name=league_name)
        if not season_clubs:
            print(f"  No season item / participating-team data found for {league_name} in {target_season} — skipping.")
            league_counts[league_name] = 0
            time.sleep(LEAGUE_SLEEP_SECONDS)
            continue

        league_id = get_or_create_league(conn, league_name, league_qid)

        # Record every club Wikidata's season item says actually played in
        # this league this season, regardless of whether any of their
        # players get matched below.
        club_lookup = {}  # club_qid -> our internal club_id
        for club_qid, club_name, club_country_qid, club_country_name in season_clubs:
            club_country_id = None
            if club_country_qid and club_country_name:
                club_country_id = get_or_create_country(conn, club_country_name, club_country_qid)
            club_id = get_or_create_club(conn, club_qid, club_name, club_country_id)
            link_club_to_league(conn, club_id, league_id, target_season)
            club_lookup[club_qid] = club_id
        print(f"  {len(club_lookup)} clubs confirmed in {league_name} for this season.")
        time.sleep(LEAGUE_SLEEP_SECONDS)

        progress_key = f"{league_name}_{target_season}"
        offset = progress.get(progress_key, 0)
        print(f"  Fetching players (limit={limit_per_league}, offset={offset})...")
        rows = fetch_season_club_players(
            list(club_lookup.keys()), target_season, limit=limit_per_league, offset=offset
        )
        parsed = [parse_season_player_row(r) for r in rows]
        total_rows += len(parsed)
        league_counts[league_name] = len(parsed)

        for r in parsed:
            if not r["player_wikidata_id"] or not r["player_name"]:
                continue  # skip incomplete rows

            country_id = None
            if r["nationality_wikidata_id"] and r["nationality_name"]:
                country_id = get_or_create_country(
                    conn, r["nationality_name"], r["nationality_wikidata_id"]
                )

            player_id = get_or_create_player(conn, {
                "wikidata_id": r["player_wikidata_id"],
                "name": r["player_name"],
                "birth_date": r["birth_date"],
                "birth_place": r["birth_place"],
                "country_id": country_id,
                "position": r["position"],
            })
            if player_id is None:
                continue
            players_seen.add(r["player_wikidata_id"])

            club_id = club_lookup.get(r["club_wikidata_id"])
            if club_id is None:
                continue  # VALUES already restricted to confirmed clubs; guard just in case

            # Fetch stadium/founded_year/trophies ONCE per club per run.
            if club_id not in enriched_clubs:
                enrich_club(conn, club_id, r["club_wikidata_id"])
                enriched_clubs.add(club_id)

            if r["start_year"]:
                clean_start, clean_end, issue = stint_dates_look_valid(
                    r["start_year"], r["end_year"], r["birth_date"]
                )
                if issue:
                    print(f"    Data quality note for {r['player_name']}: {issue}")
                if clean_start:
                    link_player_to_club(
                        conn, player_id, club_id, clean_start, clean_end, r["transfer_type"]
                    )

        time.sleep(LEAGUE_SLEEP_SECONDS)

        progress[progress_key] = offset + len(parsed)
        save_progress(progress)
        if len(parsed) < limit_per_league:
            print(f"  (Got fewer than requested — {league_name} may be running low on new matches.)")

    print(f"Processed {total_rows} rows, {len(players_seen)} unique players, {len(enriched_clubs)} clubs enriched (stadium/founded/trophies), across {len(LEAGUES)} leagues.")
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

    # Complete champion history for your tracked leagues + UCL — cheap
    # (one request per competition), independent of the player pull above.
    # This is NOT season-specific — skip it for repeated season-by-season
    # historical pulls via config.FETCH_COMPETITION_CHAMPIONS.
    if FETCH_COMPETITION_CHAMPIONS:
        fetch_all_competition_champions(conn)
    else:
        print("Skipping competition champions fetch (FETCH_COMPETITION_CHAMPIONS=False in config.py).")

    conn.close()
    print("Done. Data is now in your local Postgres database.")


if __name__ == "__main__":
    main()