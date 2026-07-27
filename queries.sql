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

-- Full picture: players, clubs, leagues, stint dates, transfer type
SELECT
    players.name AS player,
    countries.name AS nationality,
    players.birth_date,
    clubs.name AS club,
    club_countries.name AS club_country,
    leagues.name AS league,
    player_clubs.start_year,
    player_clubs.end_year,
    player_clubs.transfer_type
FROM players
LEFT JOIN countries ON countries.id = players.country_id
LEFT JOIN player_clubs ON player_clubs.player_id = players.id
LEFT JOIN clubs ON clubs.id = player_clubs.club_id
LEFT JOIN countries AS club_countries ON club_countries.id = clubs.country_id
LEFT JOIN club_league_seasons ON club_league_seasons.club_id = clubs.id
LEFT JOIN leagues ON leagues.id = club_league_seasons.league_id
ORDER BY players.name, player_clubs.start_year;


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