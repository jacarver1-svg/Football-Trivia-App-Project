"""
Add and review trivia questions/answers in the football_trivia database.

Setup (same as fetch_wikidata.py — reuses the same .env file):
    pip install psycopg2-binary python-dotenv

Usage:
    python manage_questions.py add     # interactively add a new question
    python manage_questions.py list    # view all questions currently in the database
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "dbname": os.environ.get("FOOTBALL_DB_NAME", "football_trivia"),
    "user": os.environ.get("FOOTBALL_DB_USER", "postgres"),
    "password": os.environ.get("FOOTBALL_DB_PASSWORD"),
    "host": os.environ.get("FOOTBALL_DB_HOST", "localhost"),
    "port": int(os.environ.get("FOOTBALL_DB_PORT", 5432)),
}


def get_or_create_category(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM categories WHERE name = %s;", (name,))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO categories (name) VALUES (%s) RETURNING id;", (name,)
        )
        category_id = cur.fetchone()[0]
    conn.commit()
    return category_id


def add_question(conn):
    print("\n--- Add a new trivia question ---")

    category_name = input("Category (e.g. 'Club History', 'Trophies'): ").strip()
    category_id = get_or_create_category(conn, category_name)

    question_text = input("Question text: ").strip()

    while True:
        difficulty_raw = input("Difficulty (1-5): ").strip()
        if difficulty_raw.isdigit() and 1 <= int(difficulty_raw) <= 5:
            difficulty = int(difficulty_raw)
            break
        print("  Please enter a number from 1 to 5.")

    source_fact = input("Source fact (optional, e.g. 'club_trophies'): ").strip() or None

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
    conn.commit()

    print("\nNow add answers. Enter each one, marking whether it's correct.")
    print("(Press Enter with no text when you're done adding answers.)\n")

    answers = []
    while True:
        answer_text = input(f"  Answer {len(answers) + 1} (blank to finish): ").strip()
        if not answer_text:
            break
        is_correct = input("    Is this correct? (y/n): ").strip().lower().startswith("y")
        answers.append((answer_text, is_correct))

    if not answers:
        print("\nNo answers entered — question saved but has no answer choices yet.")
        return

    if not any(is_correct for _, is_correct in answers):
        print("\nWarning: no answer was marked correct. Saving anyway, but fix this later.")

    with conn.cursor() as cur:
        for answer_text, is_correct in answers:
            cur.execute(
                """
                INSERT INTO answers (question_id, answer_text, is_correct)
                VALUES (%s, %s, %s);
                """,
                (question_id, answer_text, is_correct),
            )
    conn.commit()

    print(f"\nSaved question #{question_id} with {len(answers)} answer(s).")


def list_questions(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT questions.id, categories.name, questions.question_text,
                   questions.difficulty, questions.source_fact
            FROM questions
            LEFT JOIN categories ON categories.id = questions.category_id
            ORDER BY questions.id;
            """
        )
        questions = cur.fetchall()

        if not questions:
            print("No questions in the database yet. Run 'python manage_questions.py add' to create one.")
            return

        for q_id, category, text, difficulty, source_fact in questions:
            print(f"\n[{q_id}] ({category or 'Uncategorized'}, difficulty {difficulty}) {text}")
            if source_fact:
                print(f"    source: {source_fact}")

            cur.execute(
                "SELECT answer_text, is_correct FROM answers WHERE question_id = %s ORDER BY id;",
                (q_id,),
            )
            for answer_text, is_correct in cur.fetchall():
                marker = "✓" if is_correct else " "
                print(f"    [{marker}] {answer_text}")

    print(f"\nTotal: {len(questions)} question(s).")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("add", "list"):
        print("Usage:")
        print("  python manage_questions.py add     # add a new question")
        print("  python manage_questions.py list    # view all questions")
        return

    conn = psycopg2.connect(**DB_CONFIG)

    if sys.argv[1] == "add":
        add_question(conn)
    elif sys.argv[1] == "list":
        list_questions(conn)

    conn.close()


if __name__ == "__main__":
    main()