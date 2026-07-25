-- Football Trivia App: Postgres schema (v2)

CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

-- Normalized country reference, used by players and clubs instead of
-- loose free-text strings, so filtering/joining by country is reliable.
CREATE TABLE IF NOT EXISTS countries (
    id SERIAL PRIMARY KEY,
    wikidata_id TEXT UNIQUE,
    name TEXT UNIQUE NOT NULL
);

-- Raw facts pulled from Wikidata, kept separate from the trivia questions
-- themselves. This is your permanent "knowledge bank" — a one-time pull
-- that you own and re-use forever, no repeat API calls needed.
CREATE TABLE IF NOT EXISTS players (
    id SERIAL PRIMARY KEY,
    wikidata_id TEXT UNIQUE,        -- e.g. 'Q615' (Lionel Messi)
    name TEXT NOT NULL,
    birth_date DATE,
    birth_place TEXT,
    country_id INT REFERENCES countries(id),   -- nationality
    position TEXT,
    current_club TEXT
);

CREATE TABLE IF NOT EXISTS leagues (
    id SERIAL PRIMARY KEY,
    wikidata_id TEXT UNIQUE,
    name TEXT NOT NULL,
    country_id INT REFERENCES countries(id),
    tier INT                   -- e.g. 1 for top flight, 2 for second division
);

CREATE TABLE IF NOT EXISTS clubs (
    id SERIAL PRIMARY KEY,
    wikidata_id TEXT UNIQUE,
    name TEXT NOT NULL,
    country_id INT REFERENCES countries(id),
    founded_year INT,
    stadium TEXT
);

-- Tracks which league a club played in, per season. A club's league
-- membership changes over time (promotion/relegation), so this is
-- modeled as history, not a single current value.
CREATE TABLE IF NOT EXISTS club_league_seasons (
    id SERIAL PRIMARY KEY,
    club_id INT REFERENCES clubs(id),
    league_id INT REFERENCES leagues(id),
    season_start_year INT NOT NULL,   -- 2018 represents the "2018/19" season
    UNIQUE(club_id, league_id, season_start_year)
);

-- Tracks which club(s) a player has played for, and when.
CREATE TABLE IF NOT EXISTS player_clubs (
    id SERIAL PRIMARY KEY,
    player_id INT REFERENCES players(id),
    club_id INT REFERENCES clubs(id),
    start_year INT,
    end_year INT               -- NULL means current
);

CREATE TABLE IF NOT EXISTS trophies (
    id SERIAL PRIMARY KEY,
    wikidata_id TEXT UNIQUE,
    name TEXT NOT NULL,
    type TEXT                  -- 'individual' or 'team'
);

-- Individual awards only (Ballon d'Or, Golden Boot, etc.) — these
-- belong directly to a player, with no club/season derivation needed.
CREATE TABLE IF NOT EXISTS player_trophies (
    id SERIAL PRIMARY KEY,
    player_id INT REFERENCES players(id),
    trophy_id INT REFERENCES trophies(id),
    year INT
);

-- Team trophies (FA Cup, league titles, Champions League, etc.) belong
-- to a CLUB in a given season — not directly to players. Which players
-- "won" it is derived by overlapping this with player_clubs (see the
-- view below), so a player is only counted as a winner if they were
-- actually at that club during that season.
CREATE TABLE IF NOT EXISTS club_trophies (
    id SERIAL PRIMARY KEY,
    club_id INT REFERENCES clubs(id),
    trophy_id INT REFERENCES trophies(id),
    season_start_year INT NOT NULL,
    UNIQUE(club_id, trophy_id, season_start_year)
);

-- Unified view: every trophy a player has won, whether individual or
-- team-based, in one place. This is what powers questions like
-- "Which trophies has Rodri won?" or "How many of each trophy has
-- Messi won?" without having to hand-write the join every time.
CREATE OR REPLACE VIEW player_trophy_wins AS
SELECT
    player_id,
    trophy_id,
    year AS season_year,
    'individual' AS win_type
FROM player_trophies

UNION ALL

SELECT
    pc.player_id,
    ct.trophy_id,
    ct.season_start_year AS season_year,
    'team' AS win_type
FROM player_clubs pc
JOIN club_trophies ct ON ct.club_id = pc.club_id
WHERE ct.season_start_year >= pc.start_year
  AND (pc.end_year IS NULL OR ct.season_start_year <= pc.end_year);

-- The actual trivia content, written by you from the facts above
CREATE TABLE IF NOT EXISTS questions (
    id SERIAL PRIMARY KEY,
    category_id INT REFERENCES categories(id),
    question_text TEXT NOT NULL,
    difficulty SMALLINT CHECK (difficulty BETWEEN 1 AND 5),
    source_fact TEXT           -- optional note on which fact(s) this came from, for your own audit trail
);

CREATE TABLE IF NOT EXISTS answers (
    id SERIAL PRIMARY KEY,
    question_id INT REFERENCES questions(id) ON DELETE CASCADE,
    answer_text TEXT NOT NULL,
    is_correct BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_questions_category ON questions(category_id);
CREATE INDEX IF NOT EXISTS idx_answers_question ON answers(question_id);
CREATE INDEX IF NOT EXISTS idx_player_clubs_player ON player_clubs(player_id);
CREATE INDEX IF NOT EXISTS idx_player_clubs_club ON player_clubs(club_id);
CREATE INDEX IF NOT EXISTS idx_club_trophies_club ON club_trophies(club_id);