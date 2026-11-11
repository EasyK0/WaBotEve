CREATE TABLE statistics_adjustments (
    id INTEGER PRIMARY KEY,
    guild_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    system TEXT NOT NULL CHECK (system IN ('kino', 'niarja', 'archee')),
    before_count INTEGER NOT NULL CHECK (before_count >= 0),
    after_count INTEGER NOT NULL CHECK (after_count >= 0),
    delta INTEGER NOT NULL CHECK (delta = after_count - before_count),
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_statistics_guild_author ON statistics_adjustments (guild_id, author_id);
CREATE VIEW completion_events AS
    SELECT guild_id, author_id, author_name, system, 1 AS delta,
           created_at, 0 AS source, id FROM timers
    UNION ALL
    SELECT guild_id, author_id, author_name, system, delta,
           created_at, 1 AS source, id FROM statistics_adjustments;
CREATE VIEW completion_totals AS
    SELECT guild_id, author_id, system, SUM(delta) AS count
    FROM completion_events GROUP BY guild_id, author_id, system;
