"""
Auto-generate trivia questions from templates, using real data already in
the database — for both the correct answer AND the wrong-answer choices
(pulled from other real rows, not made up), so multiple-choice options
stay plausible.

Setup (same as fetch_wikidata.py — reuses the same .env file):
    pip install psycopg2-binary python-dotenv

Usage:
    python generate_questions.py            # generate a default batch of everything
    python generate_questions.py --preview  # print what WOULD be generated, insert nothing
"""

import sys
import random
import psycopg2

from config import DB_CONFIG

PREVIEW = "--preview" in sys.argv


def get_or_create_category(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM categories WHERE name = %s;", (name,))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("INSERT INTO categories (name) VALUES (%s) RETURNING id;", (name,))
        category_id = cur.fetchone()[0]
    conn.commit()
    return category_id


def question_already_exists(conn, question_text):
    """Prevents duplicate questions if you run this script more than once."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM questions WHERE question_text = %s;", (question_text,))
        return cur.fetchone() is not None


def save_question(conn, category_id, question_text, difficulty, source_fact, answers):
    """
    answers: list of (answer_text, is_correct) tuples.
    Skips saving (and just prints) if --preview was passed, or if this
    exact question text is already in the database.
    """
    if question_already_exists(conn, question_text):
        return False

    correct = [a for a in answers if a[1]]
    print(f"  Q: {question_text}")
    print(f"     -> {', '.join(a[0] for a in correct)}  (choices: {[a[0] for a in answers]})")

    if PREVIEW:
        return True

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO questions (category_id, question_text, difficulty, source_fact)
            VALUES (%s, %s, %s, %s)
            RETURNING id;
            """,
            (category_id, question_text, difficulty, source_fact),
        )
        question_id = cur.fetchone()[0]
        for answer_text, is_correct in answers:
            cur.execute(
                "INSERT INTO answers (question_id, answer_text, is_correct) VALUES (%s, %s, %s);",
                (question_id, answer_text, is_correct),
            )
    conn.commit()
    return True


# ---------- Template generators ----------

def generate_league_questions(conn, limit=20):
    """'What league does {club} play in?'"""
    category_id = get_or_create_category(conn, "League Membership")
    count = 0

    with conn.cursor() as cur:
        cur.execute("SELECT name FROM leagues;")
        all_leagues = [row[0] for row in cur.fetchall()]

        cur.execute(
            """
            SELECT * FROM (
                SELECT DISTINCT clubs.name, leagues.name
                FROM club_league_seasons
                JOIN clubs ON clubs.id = club_league_seasons.club_id
                JOIN leagues ON leagues.id = club_league_seasons.league_id
            ) AS deduped
            ORDER BY RANDOM()
            LIMIT %s;
            """,
            (limit,),
        )
        rows = cur.fetchall()

    print(f"\n--- League Membership ({len(rows)} candidates) ---")
    for club_name, league_name in rows:
        question_text = f"What league does {club_name} play in?"
        distractors = random.sample([l for l in all_leagues if l != league_name], min(3, len(all_leagues) - 1))
        answers = [(league_name, True)] + [(d, False) for d in distractors]
        random.shuffle(answers)
        if save_question(conn, category_id, question_text, 1, "club_league_seasons", answers):
            count += 1
    return count


def generate_founded_year_questions(conn, limit=20):
    """'What year was {club} founded?'"""
    category_id = get_or_create_category(conn, "Club History")
    count = 0

    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT founded_year FROM clubs WHERE founded_year IS NOT NULL;")
        all_years = [row[0] for row in cur.fetchall()]

        cur.execute(
            """
            SELECT name, founded_year FROM clubs
            WHERE founded_year IS NOT NULL
            ORDER BY RANDOM()
            LIMIT %s;
            """,
            (limit,),
        )
        rows = cur.fetchall()

    print(f"\n--- Club Founding Years ({len(rows)} candidates) ---")
    for club_name, founded_year in rows:
        question_text = f"What year was {club_name} founded?"
        pool = [y for y in all_years if y != founded_year]
        distractors = random.sample(pool, min(3, len(pool)))
        answers = [(str(founded_year), True)] + [(str(d), False) for d in distractors]
        random.shuffle(answers)
        if save_question(conn, category_id, question_text, 2, "clubs.founded_year", answers):
            count += 1
    return count


def generate_stadium_questions(conn, limit=20):
    """'What stadium does {club} play at?'"""
    category_id = get_or_create_category(conn, "Club History")
    count = 0

    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT stadium FROM clubs WHERE stadium IS NOT NULL;")
        all_stadiums = [row[0] for row in cur.fetchall()]

        cur.execute(
            """
            SELECT name, stadium FROM clubs
            WHERE stadium IS NOT NULL
            ORDER BY RANDOM()
            LIMIT %s;
            """,
            (limit,),
        )
        rows = cur.fetchall()

    print(f"\n--- Club Stadiums ({len(rows)} candidates) ---")
    for club_name, stadium in rows:
        question_text = f"What stadium does {club_name} play at?"
        pool = [s for s in all_stadiums if s != stadium]
        distractors = random.sample(pool, min(3, len(pool)))
        answers = [(stadium, True)] + [(d, False) for d in distractors]
        random.shuffle(answers)
        if save_question(conn, category_id, question_text, 2, "clubs.stadium", answers):
            count += 1
    return count


def generate_nationality_questions(conn, limit=20):
    """'What nationality is {player}?'"""
    category_id = get_or_create_category(conn, "Player Nationality")
    count = 0

    with conn.cursor() as cur:
        cur.execute("SELECT name FROM countries;")
        all_countries = [row[0] for row in cur.fetchall()]

        cur.execute(
            """
            SELECT players.name, countries.name
            FROM players
            JOIN countries ON countries.id = players.country_id
            ORDER BY RANDOM()
            LIMIT %s;
            """,
            (limit,),
        )
        rows = cur.fetchall()

    print(f"\n--- Player Nationality ({len(rows)} candidates) ---")
    for player_name, country_name in rows:
        question_text = f"What nationality is {player_name}?"
        pool = [c for c in all_countries if c != country_name]
        distractors = random.sample(pool, min(3, len(pool)))
        answers = [(country_name, True)] + [(d, False) for d in distractors]
        random.shuffle(answers)
        if save_question(conn, category_id, question_text, 1, "players.country_id", answers):
            count += 1
    return count


def generate_transfer_type_questions(conn, limit=20):
    """'Was {player}'s move to {club} a loan, transfer, or free transfer?'"""
    category_id = get_or_create_category(conn, "Transfers")
    count = 0

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT transfer_type FROM player_clubs
            WHERE transfer_type IS NOT NULL
              AND transfer_type !~ '^[0-9]+$';
            """
        )
        all_types = [row[0] for row in cur.fetchall()]

        cur.execute(
            """
            SELECT players.name, clubs.name, player_clubs.transfer_type, player_clubs.start_year
            FROM player_clubs
            JOIN players ON players.id = player_clubs.player_id
            JOIN clubs ON clubs.id = player_clubs.club_id
            WHERE player_clubs.transfer_type IS NOT NULL
              AND player_clubs.transfer_type !~ '^[0-9]+$'
            ORDER BY RANDOM()
            LIMIT %s;
            """,
            (limit,),
        )
        rows = cur.fetchall()

    print(f"\n--- Transfer Types ({len(rows)} candidates) ---")
    for player_name, club_name, transfer_type, start_year in rows:
        question_text = f"What type of move brought {player_name} to {club_name} in {start_year}?"
        pool = [t for t in all_types if t != transfer_type]
        distractors = pool[:3]  # usually only 2-3 distinct types exist total
        answers = [(transfer_type, True)] + [(d, False) for d in distractors]
        random.shuffle(answers)
        if len(answers) >= 2 and save_question(conn, category_id, question_text, 3, "player_clubs.transfer_type", answers):
            count += 1
    return count


def generate_join_year_questions(conn, limit=20):
    """'In what year did {player} join {club}?'"""
    category_id = get_or_create_category(conn, "Transfers")
    count = 0

    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT start_year FROM player_clubs WHERE start_year IS NOT NULL;")
        all_years = [row[0] for row in cur.fetchall()]

        cur.execute(
            """
            SELECT players.name, clubs.name, player_clubs.start_year
            FROM player_clubs
            JOIN players ON players.id = player_clubs.player_id
            JOIN clubs ON clubs.id = player_clubs.club_id
            WHERE player_clubs.start_year IS NOT NULL
            ORDER BY RANDOM()
            LIMIT %s;
            """,
            (limit,),
        )
        rows = cur.fetchall()

    print(f"\n--- Join Years ({len(rows)} candidates) ---")
    for player_name, club_name, start_year in rows:
        question_text = f"In what year did {player_name} join {club_name}?"
        pool = [y for y in all_years if y != start_year]
        distractors = random.sample(pool, min(3, len(pool)))
        answers = [(str(start_year), True)] + [(str(d), False) for d in distractors]
        random.shuffle(answers)
        if save_question(conn, category_id, question_text, 2, "player_clubs.start_year", answers):
            count += 1
    return count


def generate_trophy_count_questions(conn, limit=15):
    """'How many trophies has {club} won (on record)?'"""
    category_id = get_or_create_category(conn, "Trophies")
    count = 0

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT clubs.name, COUNT(*) AS trophy_count
            FROM club_trophies
            JOIN clubs ON clubs.id = club_trophies.club_id
            GROUP BY clubs.name
            ORDER BY RANDOM()
            LIMIT %s;
            """,
            (limit,),
        )
        rows = cur.fetchall()

        cur.execute(
            """
            SELECT COUNT(*) FROM club_trophies GROUP BY club_id;
            """
        )
        all_counts = [row[0] for row in cur.fetchall()]

    print(f"\n--- Trophy Counts ({len(rows)} candidates) ---")
    for club_name, trophy_count in rows:
        question_text = f"How many trophies does the database have on record for {club_name}?"
        pool = list({c for c in all_counts if c != trophy_count})
        distractors = random.sample(pool, min(3, len(pool)))
        answers = [(str(trophy_count), True)] + [(str(d), False) for d in distractors]
        random.shuffle(answers)
        if len(answers) >= 2 and save_question(conn, category_id, question_text, 3, "club_trophies (COUNT)", answers):
            count += 1
    return count


# ---------- Multi-answer ("select all that apply") generators ----------
# These use the exact same answers table — is_correct just gets set to
# TRUE on more than one row for the same question. No schema change needed.

def generate_league_roster_questions(conn, max_correct=8):
    """'Which of these clubs play in the {league}?' (select all)"""
    category_id = get_or_create_category(conn, "League Membership")
    count = 0

    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM leagues;")
        leagues = cur.fetchall()

        cur.execute("SELECT name FROM clubs;")
        all_club_names = [row[0] for row in cur.fetchall()]

    print(f"\n--- League Rosters (select all) ({len(leagues)} candidates) ---")
    for league_id, league_name in leagues:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM (
                    SELECT DISTINCT clubs.name
                    FROM club_league_seasons
                    JOIN clubs ON clubs.id = club_league_seasons.club_id
                    WHERE club_league_seasons.league_id = %s
                ) AS deduped
                ORDER BY RANDOM()
                LIMIT %s;
                """,
                (league_id, max_correct),
            )
            correct_clubs = [row[0] for row in cur.fetchall()]

        if len(correct_clubs) < 2:
            continue  # not enough real data yet to make a meaningful question

        question_text = f"Which of these clubs play in the {league_name}? (select all that apply)"
        distractor_pool = [c for c in all_club_names if c not in correct_clubs]
        distractors = random.sample(distractor_pool, min(4, len(distractor_pool)))

        answers = [(c, True) for c in correct_clubs] + [(d, False) for d in distractors]
        random.shuffle(answers)
        if save_question(conn, category_id, question_text, 2, "club_league_seasons (multi)", answers):
            count += 1
    return count


def generate_player_clubs_questions(conn, limit=15, max_correct=6):
    """'Which clubs has {player} played for?' (select all)"""
    category_id = get_or_create_category(conn, "Player Career")
    count = 0

    with conn.cursor() as cur:
        cur.execute("SELECT name FROM clubs;")
        all_club_names = [row[0] for row in cur.fetchall()]

        # Only players with 2+ stints make an interesting multi-answer question
        cur.execute(
            """
            SELECT players.id, players.name
            FROM player_clubs
            JOIN players ON players.id = player_clubs.player_id
            GROUP BY players.id, players.name
            HAVING COUNT(*) >= 2
            ORDER BY RANDOM()
            LIMIT %s;
            """,
            (limit,),
        )
        candidates = cur.fetchall()

    print(f"\n--- Player Career Clubs (select all) ({len(candidates)} candidates) ---")
    for player_id, player_name in candidates:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT clubs.name
                FROM player_clubs
                JOIN clubs ON clubs.id = player_clubs.club_id
                WHERE player_clubs.player_id = %s
                LIMIT %s;
                """,
                (player_id, max_correct),
            )
            correct_clubs = [row[0] for row in cur.fetchall()]

        if len(correct_clubs) < 2:
            continue

        question_text = f"Which of these clubs has {player_name} played for?"
        distractor_pool = [c for c in all_club_names if c not in correct_clubs]
        distractors = random.sample(distractor_pool, min(3, len(distractor_pool)))

        answers = [(c, True) for c in correct_clubs] + [(d, False) for d in distractors]
        random.shuffle(answers)
        if save_question(conn, category_id, question_text, 2, "player_clubs (multi)", answers):
            count += 1
    return count


def generate_country_players_questions(conn, limit=10, max_correct=5):
    """'Which of these players are from {country}?' (select all)"""
    category_id = get_or_create_category(conn, "Player Nationality")
    count = 0

    with conn.cursor() as cur:
        cur.execute("SELECT name FROM players;")
        all_player_names = [row[0] for row in cur.fetchall()]

        # Only countries with several players make a meaningful question
        cur.execute(
            """
            SELECT countries.id, countries.name
            FROM players
            JOIN countries ON countries.id = players.country_id
            GROUP BY countries.id, countries.name
            HAVING COUNT(*) >= 3
            ORDER BY RANDOM()
            LIMIT %s;
            """,
            (limit,),
        )
        candidates = cur.fetchall()

    print(f"\n--- Players by Country (select all) ({len(candidates)} candidates) ---")
    for country_id, country_name in candidates:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT name FROM players
                WHERE country_id = %s
                ORDER BY RANDOM()
                LIMIT %s;
                """,
                (country_id, max_correct),
            )
            correct_players = [row[0] for row in cur.fetchall()]

        if len(correct_players) < 2:
            continue

        question_text = f"Which of these players are from {country_name}?"
        distractor_pool = [p for p in all_player_names if p not in correct_players]
        distractors = random.sample(distractor_pool, min(3, len(distractor_pool)))

        answers = [(p, True) for p in correct_players] + [(d, False) for d in distractors]
        random.shuffle(answers)
        if save_question(conn, category_id, question_text, 2, "players.country_id (multi)", answers):
            count += 1
    return count


def generate_club_trophies_multi_questions(conn, limit=10, max_correct=6):
    """'Which trophies has {club} won?' (select all) — only runs if club_trophies has data."""
    category_id = get_or_create_category(conn, "Trophies")
    count = 0

    with conn.cursor() as cur:
        cur.execute("SELECT name FROM trophies;")
        all_trophy_names = [row[0] for row in cur.fetchall()]

        cur.execute(
            """
            SELECT clubs.id, clubs.name
            FROM club_trophies
            JOIN clubs ON clubs.id = club_trophies.club_id
            GROUP BY clubs.id, clubs.name
            HAVING COUNT(DISTINCT trophy_id) >= 2
            ORDER BY RANDOM()
            LIMIT %s;
            """,
            (limit,),
        )
        candidates = cur.fetchall()

    print(f"\n--- Club Trophies (select all) ({len(candidates)} candidates) ---")
    for club_id, club_name in candidates:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT trophies.name
                FROM club_trophies
                JOIN trophies ON trophies.id = club_trophies.trophy_id
                WHERE club_trophies.club_id = %s
                LIMIT %s;
                """,
                (club_id, max_correct),
            )
            correct_trophies = [row[0] for row in cur.fetchall()]

        if len(correct_trophies) < 2:
            continue

        question_text = f"Which of these trophies has {club_name} won?"
        distractor_pool = [t for t in all_trophy_names if t not in correct_trophies]
        distractors = random.sample(distractor_pool, min(3, len(distractor_pool)))

        answers = [(t, True) for t in correct_trophies] + [(d, False) for d in distractors]
        random.shuffle(answers)
        if save_question(conn, category_id, question_text, 3, "club_trophies (multi)", answers):
            count += 1
    return count


def main():
    conn = psycopg2.connect(**DB_CONFIG)

    if PREVIEW:
        print("=== PREVIEW MODE: nothing will be saved to the database ===")

    totals = {
        "League Membership": generate_league_questions(conn, limit=20),
        "Club Founding Years": generate_founded_year_questions(conn, limit=20),
        "Club Stadiums": generate_stadium_questions(conn, limit=20),
        "Player Nationality": generate_nationality_questions(conn, limit=20),
        "Transfer Types": generate_transfer_type_questions(conn, limit=20),
        "Join Years": generate_join_year_questions(conn, limit=20),
        "Trophy Counts": generate_trophy_count_questions(conn, limit=15),
        "League Rosters (multi)": generate_league_roster_questions(conn),
        "Player Career Clubs (multi)": generate_player_clubs_questions(conn, limit=15),
        "Players by Country (multi)": generate_country_players_questions(conn, limit=10),
        "Club Trophies (multi)": generate_club_trophies_multi_questions(conn, limit=10),
    }

    conn.close()

    print("\n=== Summary ===")
    for name, n in totals.items():
        print(f"  {name}: {n} question(s) {'would be ' if PREVIEW else ''}saved")
    print(f"  TOTAL: {sum(totals.values())}")


if __name__ == "__main__":
    main()