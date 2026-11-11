import discord

from wabot.messages import MessageSettings
from wabot.models import SYSTEMS, RankPage, Timer
from wabot.timekeeping import eve_time

NO_MENTIONS = discord.AllowedMentions.none()


def safe_name(value: str) -> str:
    # Keep each author on one line and neutralize markdown and Discord mention syntax.
    clean = " ".join(value.split())[:80]
    clean = clean.replace("<", "‹").replace(">", "›")
    return discord.utils.escape_markdown(discord.utils.escape_mentions(clean))


def timer_values(timer: Timer) -> dict:
    return {
        "system": SYSTEMS[timer.system],
        "payout": eve_time(timer.payout_at),
        "respawn": eve_time(timer.due_at),
        "remaining": f"<t:{timer.due_at}:R>",
        "timer_id": str(timer.id),
        "author": safe_name(timer.author_name) or "User " + timer.author_id,
    }


def timer_message(timer: Timer, *, inserted: bool, messages: MessageSettings | None = None) -> str:
    title = "Completion recorded" if inserted else "Completion already recorded"
    increment = "+1" if inserted else "+0 (duplicate interaction)"
    return (messages or MessageSettings()).render(
        "timer_confirmation", title=title, increment=increment, **timer_values(timer)
    )


def stats_message(counts: dict[str, int], messages: MessageSettings | None = None) -> str:
    rows = "\n".join(f"{name}: {counts[key]}" for key, name in SYSTEMS.items())
    return (messages or MessageSettings()).render("stats", rows=rows, total=counts["total"])


def rank_message(
    result: RankPage, guild: discord.Guild, messages: MessageSettings | None = None
) -> str:
    rows = []
    for entry in result.entries:
        member = guild.get_member(int(entry.author_id))
        name = member.display_name if member else entry.author_name
        name = safe_name(name) if name else ""
        name = name or f"User {entry.author_id}"
        rows.append(f"{entry.rank}. {name} — {entry.count}")
    return (messages or MessageSettings()).render(
        "rank", rows="\n".join(rows), page=result.page, pages=result.total_pages
    )
