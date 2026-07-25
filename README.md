# Football Trivia — Data Setup

## 1. Create the database and tables

```bash
createdb football_trivia
psql football_trivia -f schema.sql
```

## 2. Install Python dependencies

```bash
pip install requests psycopg2-binary
```

Edit `DB_CONFIG` in `fetch_wikidata.py` if your Postgres user/password differ
from the defaults (this assumes a local install with no password, common for
dev setups).

## 3. Run the fetch script

```bash
python fetch_wikidata.py
```

This queries Wikidata's public SPARQL endpoint for players by nationality,
and upserts them into your local `players` table. It's a one-time pull —
run it again later only if you want to refresh/expand the data, not on a
recurring schedule.

## 4. Explore what you got

```sql
SELECT name, birth_date, nationality, current_club FROM players LIMIT 10;
```

## 5. Expanding the query

The `QUERY_TEMPLATE` in `fetch_wikidata.py` is a SPARQL query. A few useful
Wikidata property codes for football:

| Property | Meaning              |
|----------|-----------------------|
| P106     | occupation             |
| P27      | country of citizenship |
| P569     | date of birth           |
| P19      | place of birth          |
| P54      | member of sports team (club) |
| P166     | award received (trophies) |
| P1532    | country of sport (national team) |

You can add clauses for trophies, national team caps, goals, etc. by adding
similar `OPTIONAL` blocks and matching property codes. Test any new query
first at https://query.wikidata.org before adding it to the script — it has
an interactive query builder and lets you preview results instantly.

## 6. Turning facts into trivia questions

Once facts are in Postgres, write your own original question text from them,
e.g.:

```sql
INSERT INTO questions (category_id, question_text, difficulty, source_fact)
VALUES (1, 'Which club did this player play for in 2015?', 2, 'players.current_club history');
```

This keeps a clean separation: Wikidata facts are your reference data,
and the actual trivia questions are your own original content — which is
the safest and cleanest approach both legally and for app quality.

## Notes on Wikidata usage

- The public SPARQL endpoint is free with no API key, but has generous
  rate limits (roughly 60s of query time per 60s window per IP) — fine for
  occasional/one-time pulls like this, not for live per-request app traffic.
- Always send a descriptive `User-Agent` header (already set in the script).
- Wikidata content is CC0 (public domain) — safe to store and build on
  indefinitely, no attribution legally required (though crediting Wikidata
  is good practice).