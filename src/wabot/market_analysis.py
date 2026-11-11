"""Sell-timing analysis from ESI daily history and the current Jita/Perimeter quotes."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import discord

from wabot.market_esi import HistoryDay

WINDOW_DAYS = 30
MIN_DAYS = 7
GOOD_POSITION = Decimal(80)
WEAK_POSITION = Decimal(20)
GOOD_MIN_TREND = Decimal(-2)
TREND_NOTE = Decimal(5)
LOW_VOLUME = Decimal(5)
SPARK = "▁▂▃▄▅▆▇█"
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
VERDICTS = {
    "good": ("🟢", "Good time to sell", 0x22C55E),
    "neutral": ("🟡", "Neutral", 0xF59E0B),
    "weak": ("🔴", "Weak time to sell", 0xEF4444),
}


@dataclass(frozen=True, slots=True)
class PriceAnalysis:
    price_now: Decimal
    best_buy: Decimal | None
    best_sell: Decimal | None
    avg7: Decimal
    avg30: Decimal
    low30: Decimal
    high30: Decimal
    position: Decimal
    trend: Decimal
    volume7: Decimal
    volume30: Decimal
    best_weekday: str | None
    instant_net: Decimal | None
    listing_net: Decimal | None
    verdict: str
    notes: tuple[str, ...]
    days: tuple[HistoryDay, ...]

    @property
    def icon(self) -> str:
        return VERDICTS[self.verdict][0]

    @property
    def label(self) -> str:
        return VERDICTS[self.verdict][1]

    @property
    def colour(self) -> int:
        return VERDICTS[self.verdict][2]


def _window(history: Sequence[HistoryDay], end: date, days: int) -> list[HistoryDay]:
    start = end - timedelta(days=days - 1)
    return [day for day in history if start <= day.day <= end]


def _weighted(days: Sequence[HistoryDay]) -> Decimal:
    volume = sum(day.volume for day in days)
    if volume:
        return sum((day.average * day.volume for day in days), Decimal(0)) / volume
    return sum((day.average for day in days), Decimal(0)) / len(days)


def _best_weekday(days: Sequence[HistoryDay]) -> str | None:
    by_weekday: dict[int, list[Decimal]] = {}
    for day in days:
        by_weekday.setdefault(day.day.weekday(), []).append(day.average)
    # One sample per weekday says nothing; require two full weeks of data.
    if len(days) < 14 or len(by_weekday) < 7:
        return None
    best = max(by_weekday, key=lambda weekday: sum(by_weekday[weekday]) / len(by_weekday[weekday]))
    return WEEKDAYS[best]


def analyze(
    history: Sequence[HistoryDay],
    best_buy: Decimal | None,
    best_sell: Decimal | None,
    *,
    today: date,
    sales_tax: Decimal = Decimal("3.375"),
    broker_fee: Decimal = Decimal("1.5"),
    window: int = WINDOW_DAYS,
) -> PriceAnalysis | None:
    """None when fewer than seven days of history make any verdict meaningless."""
    # ESI publishes yesterday after downtime; anchor windows on the newest day, but
    # history that stopped long ago is not a current market.
    history = [day for day in history if day.day <= today]
    if not history or (today - history[-1].day).days > MIN_DAYS:
        return None
    end = history[-1].day
    days = _window(history, end, window)
    if len(days) < MIN_DAYS:
        return None
    week = _window(days, end, 7)
    if best_buy is not None and best_sell is not None:
        price_now = (best_buy + best_sell) / 2
    else:
        price_now = best_buy if best_buy is not None else best_sell
    if price_now is None:
        price_now = days[-1].average
    avg7 = _weighted(week)
    avg30 = _weighted(days)
    low = min(day.average for day in days)
    high = max(day.average for day in days)
    if high > low:
        position = (price_now - low) / (high - low) * 100
        position = max(Decimal(0), min(Decimal(100), position))
    else:
        position = Decimal(50)
    trend = (avg7 / avg30 - 1) * 100 if avg30 else Decimal(0)
    volume7 = Decimal(sum(day.volume for day in week)) / 7
    span = min(window, (end - days[0].day).days + 1)
    volume30 = Decimal(sum(day.volume for day in days)) / span
    if position >= GOOD_POSITION and trend > GOOD_MIN_TREND:
        verdict = "good"
    elif position <= WEAK_POSITION:
        verdict = "weak"
    else:
        verdict = "neutral"
    notes = []
    if trend <= -TREND_NOTE:
        notes.append("price falling")
    elif trend >= TREND_NOTE:
        notes.append("price rising")
    if volume30 < LOW_VOLUME:
        notes.append("low volume")
    instant = best_buy * (1 - sales_tax / 100) if best_buy is not None else None
    listing = best_sell * (1 - (sales_tax + broker_fee) / 100) if best_sell is not None else None
    return PriceAnalysis(
        price_now,
        best_buy,
        best_sell,
        avg7,
        avg30,
        low,
        high,
        position,
        trend,
        volume7,
        volume30,
        _best_weekday(days),
        instant,
        listing,
        verdict,
        tuple(notes),
        tuple(days),
    )


def sparkline(values: Sequence[Decimal], width: int = WINDOW_DAYS) -> str:
    values = list(values)[-width:]
    if not values:
        return ""
    low, high = min(values), max(values)
    if high == low:
        return SPARK[3] * len(values)
    steps = len(SPARK) - 1
    return "".join(SPARK[round((value - low) / (high - low) * steps)] for value in values)


def isk(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    places = 2 if abs(value) < 100 else 0
    return f"{value:,.{places}f}".replace(",", " ") + " ISK"


def _percent(value: Decimal, *, signed: bool = False) -> str:
    return f"{value:+.1f}%" if signed else f"{value:.0f}%"


def summary_line(analysis: PriceAnalysis) -> str:
    """One report line, e.g. `🟢 Good time to sell · 30d position 86% · trend +4.1%`."""
    line = (
        f"{analysis.icon} {analysis.label} · 30d position {_percent(analysis.position)} "
        f"· trend {_percent(analysis.trend, signed=True)}"
    )
    return line + "".join(f" · {note}" for note in analysis.notes)


def _better(analysis: PriceAnalysis) -> str:
    instant, listing = analysis.instant_net, analysis.listing_net
    if instant is None or listing is None:
        return ""
    if listing > instant:
        return f"\nListing gives **{isk(listing - instant)}** more per unit."
    return f"\nInstant sell gives **{isk(instant - listing)}** more per unit."


def _clean(name: str) -> str:
    name = " ".join(str(name).split()).replace("<", "‹").replace(">", "›")
    return discord.utils.escape_markdown(discord.utils.escape_mentions(name))[:200]


def analysis_embed(name: str, analysis: PriceAnalysis | None) -> discord.Embed:
    if analysis is None:
        embed = discord.Embed(
            title=f"⚪ {_clean(name)}",
            description="Not enough market history for an analysis (fewer than 7 days).",
            colour=0x95A5A6,
        )
        embed.set_footer(text="The Forge history (ESI)")
        return embed
    embed = discord.Embed(
        title=f"{analysis.icon} {analysis.label} · {_clean(name)}",
        description=(
            " · ".join(note.capitalize() for note in analysis.notes) if analysis.notes else None
        ),
        colour=analysis.colour,
    )
    fields = (
        ("Price now", isk(analysis.price_now)),
        ("7d average", isk(analysis.avg7)),
        ("30d average", isk(analysis.avg30)),
        ("30d range", f"{isk(analysis.low30)} – {isk(analysis.high30)}"),
        ("Position in range", _percent(analysis.position)),
        ("Trend 7d vs 30d", _percent(analysis.trend, signed=True)),
        (
            "Volume per day",
            f"{analysis.volume7:,.0f} (7d) · {analysis.volume30:,.0f} (30d)".replace(",", " "),
        ),
        ("Best weekday", analysis.best_weekday or "Not enough data"),
    )
    for field_name, value in fields:
        embed.add_field(name=field_name, value=value, inline=True)
    embed.add_field(
        name="Net per unit after fees",
        value=(
            f"Instant sell: {isk(analysis.instant_net)}\n"
            f"Listing: {isk(analysis.listing_net)}" + _better(analysis)
        ),
        inline=False,
    )
    chart = sparkline([day.average for day in analysis.days])
    embed.add_field(name="Price, last 30 days", value=f"`{chart}`", inline=False)
    embed.set_footer(
        text="Jita 4-4 + Perimeter orders · The Forge history (ESI) · statistics, not a guarantee"
    )
    return embed


def overview_embed(results: Sequence[tuple[str, PriceAnalysis | None]]) -> discord.Embed:
    embed = discord.Embed(title="Market analysis · watched items", colour=0x8B5CF6)
    lines = []
    for name, analysis in results:
        if analysis is None:
            lines.append(f"⚪ **{_clean(name)}** · not enough history")
        else:
            lines.append(
                f"**{_clean(name)}** · {isk(analysis.price_now)}\n{summary_line(analysis)}"
            )
    embed.description = "\n".join(lines)[:4000] or "No enabled watched items."
    embed.set_footer(text="Use /market-analysis item:<name> for details")
    return embed


BUTTON_PREFIX = "wabot:analysis:"


class AnalysisButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"wabot:analysis:(?P<type_id>[0-9]{1,12})"
):
    """Persistent report button; a click shows the item's analysis to that member only."""

    def __init__(self, type_id: int, label: str = "Analysis") -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                emoji="📈",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{BUTTON_PREFIX}{type_id}",
            )
        )
        self.type_id = type_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["type_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        handlers = getattr(interaction.client, "market_handlers", None)
        if handlers is None:
            await interaction.response.send_message(
                "Market analysis is not available right now.", ephemeral=True
            )
            return
        await handlers.analysis_button(interaction, self.type_id)


def analysis_view(items) -> discord.ui.View | None:
    """Up to 25 buttons (Discord's limit), one per enabled item, in report order."""
    view = discord.ui.View(timeout=None)
    for item in list(items)[:25]:
        view.add_item(AnalysisButton(item.type_id, item.display_name))
    return view if view.children else None
