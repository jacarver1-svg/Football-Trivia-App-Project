-- ============================================================
-- Football Trivia — Quick Reference Queries
-- Copy/paste any of these into psql, pgAdmin, or SQLTools.
-- ============================================================


-- ---------- OVERVIEW / COUNTS ----------

-- Row counts across every table, one glance at the whole database
SELECT
    (SELECT COUNT(*) FROM players) AS players,
    (SELECT COUNT(*) FROM countries) AS countries,
    (SELECT COUNT(*) FROM clubs) AS clubs,
    (SELECT COUNT(*) FROM leagues) AS leagues,
    (SELECT COUNT(*) FROM club_league_seasons) AS club_league_links,
    (SELECT COUNT(*) FROM player_clubs) AS player_club_stints,
    (SELECT COUNT(*) FROM trophies) AS trophies,
    (SELECT COUNT(*) FROM player_trophies) AS individual_trophy_wins,
    (SELECT COUNT(*) FROM club_trophies) AS club_trophy_wins,
    (SELECT COUNT(*) FROM questions) AS questions;

-- Unique player count (players.wikidata_id is UNIQUE, so this is
-- already deduplicated — no need to DISTINCT it)
SELECT COUNT(*) FROM players;

-- Total stints vs. unique players with at least one stint —
-- the gap between these two is players with multiple clubs/spells
SELECT
    (SELECT COUNT(*) FROM player_clubs) AS total_stints,
    (SELECT COUNT(DISTINCT player_id) FROM player_clubs) AS unique_players_with_stints;


-- ---------- SEE THE DATA ----------

-- All players with nationality
SELECT players.name, countries.name AS nationality, players.birth_date
FROM players
LEFT JOIN countries ON countries.id = players.country_id
ORDER BY players.name;

-- Full picture: players, clubs, MOST RECENT league/season, stint dates,
-- transfer type. Uses a LATERAL join to grab just the latest season per
-- club, avoiding duplicate-looking rows now that clubs can have several
-- club_league_seasons entries (multiple pulls, promotions/relegations).
SELECT
    players.name AS player,
    countries.name AS nationality,
    players.birth_date,
    players.position,
    clubs.name AS club,
    leagues.name AS league,
    latest_season.season_start_year,
    player_clubs.start_year,
    player_clubs.end_year,
    player_clubs.transfer_type
FROM players
LEFT JOIN countries ON countries.id = players.country_id
LEFT JOIN player_clubs ON player_clubs.player_id = players.id
LEFT JOIN clubs ON clubs.id = player_clubs.club_id
LEFT JOIN LATERAL (
    SELECT cls.league_id, cls.season_start_year
    FROM club_league_seasons cls
    WHERE cls.club_id = clubs.id
    ORDER BY cls.season_start_year DESC
    LIMIT 1
) latest_season ON true
LEFT JOIN leagues ON leagues.id = latest_season.league_id
ORDER BY players.name, player_clubs.start_year;


-- ---------- PLAYER POSITIONS ----------

-- How many players have a position filled in
SELECT COUNT(*) AS total_players, COUNT(position) AS with_position
FROM players;

-- Breakdown by position (useful sanity check — should be a handful of
-- sensible values: goalkeeper, defender, midfielder, forward, etc.,
-- not a long tail of oddities)
SELECT position, COUNT(*)
FROM players
WHERE position IS NOT NULL
GROUP BY position
ORDER BY COUNT(*) DESC;


-- ---------- EXPANDED LEAGUE COVERAGE (18 tracked leagues) ----------

-- Player count per league (current squads, via most recent club_league_seasons)
SELECT leagues.name AS league, COUNT(DISTINCT players.id) AS player_count
FROM players
JOIN player_clubs ON player_clubs.player_id = players.id
JOIN clubs ON clubs.id = player_clubs.club_id
JOIN club_league_seasons ON club_league_seasons.club_id = clubs.id
JOIN leagues ON leagues.id = club_league_seasons.league_id
GROUP BY leagues.name
ORDER BY player_count DESC;

-- Club count per league (sanity check — a top flight typically has
-- ~18-24 clubs; wildly off numbers are worth a second look)
SELECT leagues.name AS league, COUNT(DISTINCT clubs.id) AS club_count
FROM club_league_seasons
JOIN clubs ON clubs.id = club_league_seasons.club_id
JOIN leagues ON leagues.id = club_league_seasons.league_id
GROUP BY leagues.name
ORDER BY club_count DESC;

-- Any tracked league with ZERO players pulled — worth investigating
-- (a wrong QID, a P118/roster coverage gap, etc.)
SELECT leagues.name
FROM leagues
LEFT JOIN club_league_seasons ON club_league_seasons.league_id = leagues.id
WHERE club_league_seasons.id IS NULL;


-- ---------- TROPHY COMPETITIONS (24 tracked) ----------

-- Champion seasons recorded per competition (leagues + cups + UCL/UEL)
SELECT
    COALESCE(parent_leagues.name, 'cup / continental') AS competition_type,
    trophies.name,
    club_trophies.season_start_year
FROM club_trophies
JOIN trophies ON trophies.id = club_trophies.trophy_id
LEFT JOIN leagues AS parent_leagues ON parent_leagues.id = trophies.parent_league_id
ORDER BY competition_type, club_trophies.season_start_year DESC
LIMIT 50;

-- Which tracked competitions have NO champion data at all yet
SELECT name FROM leagues
WHERE id NOT IN (SELECT DISTINCT parent_league_id FROM trophies WHERE parent_league_id IS NOT NULL);


-- ---------- DATA QUALITY CHECKS ----------

-- Players with NO club stint at all (e.g. leftover from early test pulls)
SELECT players.name, players.wikidata_id, countries.name AS nationality
FROM players
LEFT JOIN countries ON countries.id = players.country_id
LEFT JOIN player_clubs ON player_clubs.player_id = players.id
WHERE player_clubs.id IS NULL
ORDER BY players.name;

-- Breakdown of transfer_type values actually present (loan/transfer/
-- free transfer/youth association football/blank)
SELECT transfer_type, COUNT(*)
FROM player_clubs
GROUP BY transfer_type
ORDER BY COUNT(*) DESC;

-- Most-traveled players (highest number of stints)
SELECT players.name, COUNT(*) AS num_stints
FROM player_clubs
JOIN players ON players.id = player_clubs.player_id
GROUP BY players.name
ORDER BY num_stints DESC
LIMIT 10;


-- ---------- ADDITIONAL SANITY CHECKS (run after any fetch) ----------

-- Any transfer_type values that are just numbers (like the "2016" bug)
SELECT players.name, clubs.name, player_clubs.transfer_type
FROM player_clubs
JOIN players ON players.id = player_clubs.player_id
JOIN clubs ON clubs.id = player_clubs.club_id
WHERE transfer_type ~ '^[0-9]+$';

-- Stints where end_year is BEFORE start_year (should never happen)
SELECT players.name, clubs.name, player_clubs.start_year, player_clubs.end_year
FROM player_clubs
JOIN players ON players.id = player_clubs.player_id
JOIN clubs ON clubs.id = player_clubs.club_id
WHERE end_year IS NOT NULL AND end_year < start_year;

-- Players/clubs/countries/trophies whose name is just a raw Wikidata QID
-- (means the label service found no usable name for that item)
SELECT id, name, wikidata_id FROM players WHERE name ~ '^Q[0-9]+$';
SELECT id, name, wikidata_id FROM clubs WHERE name ~ '^Q[0-9]+$';
SELECT id, name, wikidata_id FROM countries WHERE name ~ '^Q[0-9]+$';
SELECT id, name, wikidata_id FROM trophies WHERE name ~ '^Q[0-9]+$';

-- Duplicate club/player names with different wikidata_ids — could be
-- genuine (two real people/clubs sharing a name), but worth a glance
-- before writing a question that assumes uniqueness
SELECT name, COUNT(*) FROM clubs GROUP BY name HAVING COUNT(*) > 1;
SELECT name, COUNT(*) FROM players GROUP BY name HAVING COUNT(*) > 1;

-- Clubs missing a country / players missing a nationality
SELECT name, wikidata_id FROM clubs WHERE country_id IS NULL;
SELECT name, wikidata_id FROM players WHERE country_id IS NULL;

-- Founded years / trophy seasons that look implausible
SELECT name, founded_year FROM clubs
WHERE founded_year IS NOT NULL AND (founded_year < 1850 OR founded_year > 2026);

SELECT clubs.name, trophies.name, club_trophies.season_start_year
FROM club_trophies
JOIN clubs ON clubs.id = club_trophies.club_id
JOIN trophies ON trophies.id = club_trophies.trophy_id
WHERE season_start_year < 1850 OR season_start_year > 2026;

-- Players who joined a club before they were even born (red flag for
-- bad qualifier data on Wikidata)
SELECT players.name, clubs.name, players.birth_date, player_clubs.start_year
FROM player_clubs
JOIN players ON players.id = player_clubs.player_id
JOIN clubs ON clubs.id = player_clubs.club_id
WHERE players.birth_date IS NOT NULL
  AND player_clubs.start_year < EXTRACT(YEAR FROM players.birth_date);

-- club_league_seasons entries with a dangling club_id or league_id
-- (shouldn't happen given foreign keys, but confirms integrity at a glance)
SELECT COUNT(*) FROM club_league_seasons cls
LEFT JOIN clubs ON clubs.id = cls.club_id
LEFT JOIN leagues ON leagues.id = cls.league_id
WHERE clubs.id IS NULL OR leagues.id IS NULL;


-- ---------- QUESTION-GENERATION READINESS CHECKS ----------

-- How many clubs have stadium/founded_year filled in (from enrich_club)
SELECT
    COUNT(*) AS total_clubs,
    COUNT(stadium) AS with_stadium,
    COUNT(founded_year) AS with_founded_year
FROM clubs;

-- How many club_trophies rows exist, and for how many distinct clubs/trophies
SELECT
    COUNT(*) AS total_trophy_records,
    COUNT(DISTINCT club_id) AS distinct_clubs_with_trophies,
    COUNT(DISTINCT trophy_id) AS distinct_trophy_types
FROM club_trophies;

-- Which trophies actually came through (useful before trusting them in questions)
SELECT trophies.name, COUNT(*) AS times_awarded
FROM club_trophies
JOIN trophies ON trophies.id = club_trophies.trophy_id
GROUP BY trophies.name
ORDER BY times_awarded DESC;

-- How many player_clubs stints have a usable transfer_type (for loan/transfer questions)
SELECT transfer_type, COUNT(*)
FROM player_clubs
WHERE transfer_type IS NOT NULL
GROUP BY transfer_type;

-- How many players have a known nationality (for "what nationality is X" questions)
SELECT
    COUNT(*) AS total_players,
    COUNT(country_id) AS with_nationality
FROM players;


-- ---------- LEAGUE CHAMPIONS (uses trophies.parent_league_id) ----------

-- Which club won which season of a specific tracked league (only shows
-- genuine league titles, not cups/continental trophies/stray junk)
SELECT clubs.name AS champion, club_trophies.season_start_year
FROM club_trophies
JOIN clubs ON clubs.id = club_trophies.club_id
JOIN trophies ON trophies.id = club_trophies.trophy_id
JOIN leagues ON leagues.id = trophies.parent_league_id
WHERE leagues.name = 'Premier League'
ORDER BY club_trophies.season_start_year DESC;

-- Same, but across ALL five tracked leagues at once
SELECT leagues.name AS league, clubs.name AS champion, club_trophies.season_start_year
FROM club_trophies
JOIN clubs ON clubs.id = club_trophies.club_id
JOIN trophies ON trophies.id = club_trophies.trophy_id
JOIN leagues ON leagues.id = trophies.parent_league_id
ORDER BY leagues.name, club_trophies.season_start_year DESC;

-- How many trophies are linked to a tracked league vs. not (gut-check
-- on how much of your trophy data is genuine league titles vs. other)
SELECT
    COUNT(*) FILTER (WHERE parent_league_id IS NOT NULL) AS league_title_trophies,
    COUNT(*) FILTER (WHERE parent_league_id IS NULL) AS other_trophies
FROM trophies;

-- Trophies NOT linked to a tracked league — eyeball these to see what's
-- actually in there (cups, continental competitions, or leftover junk)
SELECT name FROM trophies WHERE parent_league_id IS NULL ORDER BY name;


-- Which trophies has a specific player won (individual + team, via the view)
SELECT trophies.name, ptw.season_year, ptw.win_type
FROM player_trophy_wins ptw
JOIN trophies ON trophies.id = ptw.trophy_id
JOIN players ON players.id = ptw.player_id
WHERE players.name = 'Rodri';

-- How many of each trophy has a specific player won
SELECT trophies.name, COUNT(*) AS times_won
FROM player_trophy_wins ptw
JOIN trophies ON trophies.id = ptw.trophy_id
JOIN players ON players.id = ptw.player_id
WHERE players.name = 'Lionel Messi'
GROUP BY trophies.name
ORDER BY times_won DESC;

-- Which players of a given nationality played for a given club
SELECT players.name, player_clubs.start_year, player_clubs.end_year
FROM player_clubs
JOIN players ON players.id = player_clubs.player_id
JOIN countries ON countries.id = players.country_id
JOIN clubs ON clubs.id = player_clubs.club_id
WHERE countries.name = 'France' AND clubs.name = 'Bayern Munich';

-- Which players of a given nationality won a specific trophy
SELECT DISTINCT players.name, ptw.season_year
FROM player_trophy_wins ptw
JOIN players ON players.id = ptw.player_id
JOIN countries ON countries.id = players.country_id
JOIN trophies ON trophies.id = ptw.trophy_id
WHERE countries.name = 'Brazil' AND trophies.name = 'FA Cup';

-- Players who were ever on loan somewhere
SELECT players.name, clubs.name AS club, player_clubs.start_year, player_clubs.end_year
FROM player_clubs
JOIN players ON players.id = player_clubs.player_id
JOIN clubs ON clubs.id = player_clubs.club_id
WHERE player_clubs.transfer_type = 'loan'
ORDER BY players.name;

-- Which teams were in a given league during a specific season
-- (only meaningful once club_league_seasons has real season-by-season
-- history beyond the single "current season" marker the fetch script uses)
SELECT clubs.name
FROM club_league_seasons
JOIN clubs ON clubs.id = club_league_seasons.club_id
JOIN leagues ON leagues.id = club_league_seasons.league_id
WHERE leagues.name = 'Premier League'
  AND club_league_seasons.season_start_year = 2025;