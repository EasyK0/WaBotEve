CREATE TABLE timers (
    id INTEGER PRIMARY KEY,
    interaction_id TEXT NOT NULL UNIQUE,
    guild_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    system TEXT NOT NULL CHECK (system IN ('kino', 'niarja', 'archee')),
    payout_at INTEGER NOT NULL,
    due_at INTEGER NOT NULL CHECK (due_at = payout_at + 1380),
    created_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'sending', 'retry', 'sent', 'skipped_expired',
                   'failed', 'delivery_unknown')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at INTEGER NOT NULL,
    sent_at INTEGER,
    message_id TEXT,
    last_error TEXT
);

CREATE INDEX idx_timers_guild_system ON timers (guild_id, system);
CREATE INDEX idx_timers_guild_author ON timers (guild_id, author_id);
CREATE INDEX idx_timers_delivery ON timers (status, next_attempt_at, due_at);
