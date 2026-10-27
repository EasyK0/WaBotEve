from dataclasses import dataclass

SYSTEMS = {"kino": "Kino", "niarja": "Niarja", "archee": "Archee"}
TIMER_SECONDS = 1380
GRACE_SECONDS = 300
POLL_SECONDS = 5
PAGE_SIZE = 10


@dataclass(frozen=True, slots=True)
class Timer:
    id: int
    interaction_id: str
    guild_id: str
    channel_id: str
    author_id: str
    author_name: str
    system: str
    payout_at: int
    due_at: int
    created_at: int
    status: str = "pending"
    attempt_count: int = 0
    next_attempt_at: int = 0
    sent_at: int | None = None
    message_id: str | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class RankEntry:
    author_id: str
    author_name: str
    count: int
    rank: int


@dataclass(frozen=True, slots=True)
class RankPage:
    entries: list[RankEntry]
    page: int
    total_pages: int
    total_authors: int
