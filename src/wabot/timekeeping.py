import re
from datetime import UTC, datetime, timedelta

from wabot.models import TIMER_SECONDS

INVALID_TIME = "Invalid payout time. Use HH:MM in EVE Time (UTC)."
EXPIRED_TIME = "The respawn time has already passed. No completion was recorded."


def resolve_payout(value: str, now: datetime) -> tuple[int, int]:
    """Resolve the last occurrence of HH:MM using the interaction's UTC creation time."""
    if now.tzinfo is None:
        raise ValueError("Interaction creation time must be timezone-aware.")
    value = value.strip()
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError(INVALID_TIME)
    now = now.astimezone(UTC)
    hour, minute = map(int, value.split(":"))
    payout = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if payout > now:
        payout -= timedelta(days=1)
    due = payout + timedelta(seconds=TIMER_SECONDS)
    if due <= now:
        raise ValueError(EXPIRED_TIME)
    return int(payout.timestamp()), int(due.timestamp())


def eve_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d %H:%M EVE Time (UTC)")
