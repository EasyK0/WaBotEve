"""Decimal market analysis and Discord reports for the combined Jita/Perimeter market."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import discord

from wabot.market_analysis import PriceAnalysis, summary_line
from wabot.market_config import MarketItem, MarketSettings
from wabot.messages import MessageSettings

MAX_DATA_AGE = timedelta(minutes=30)
STATUS_COLOURS = {
    "SAFE": 0x22C55E,
    "STOP": 0xEF4444,
    "LOW DEPTH": 0xF59E0B,
    "DATA ERROR": 0x95A5A6,
}
STATUS_ICONS = {"SAFE": "🟢", "STOP": "🔴", "LOW DEPTH": "🟠", "DATA ERROR": "⚪"}
OTHER_ITEMS_COLOUR = 0x8B5CF6
ITEM_ICONS = {
    52244: "🔵",  # Veles Entropic Radiation Sink
    52240: "🟣",  # Zorya's Supratidal Entropic Disintegrator
    92541: "🟡",  # Capital Disintegrator Specialization
    52238: "🟠",  # Zorya's Heavy Entropic Disintegrator
    52236: "🟤",  # Zorya's Light Entropic Disintegrator
}


@dataclass(frozen=True, slots=True)
class ItemSnapshot:
    item: MarketItem
    orders: list[dict]
    fetched_at: datetime | None
    state: str = "Live"


@dataclass(frozen=True, slots=True)
class MarketAnalysis:
    best_buy: Decimal | None
    best_buy_volume: int
    best_sell: Decimal | None
    best_sell_volume: int
    spread_isk: Decimal | None
    spread_percent: Decimal | None
    safe_units: int
    safe_gross_isk: Decimal
    safe_avg_price: Decimal | None
    next_below: Decimal | None
    status: str | None


def group_orders(orders: Sequence[dict], settings: MarketSettings) -> dict[int, list[dict]]:
    """Keep Jita 4-4 and Perimeter only, retaining individual locations."""
    groups: dict[int, list[dict]] = {settings.jita_location_id: []}
    for order in orders:
        location_id = order.get("location_id")
        if isinstance(location_id, bool) or not isinstance(location_id, int):
            continue
        if (
            location_id == settings.jita_location_id
            or order.get("system_id") == settings.perimeter_system_id
        ):
            groups.setdefault(location_id, []).append(order)
    return groups


def _prices(orders: Sequence[dict]) -> list[tuple[bool, Decimal, int]]:
    values = []
    for order in orders:
        try:
            price = Decimal(str(order["price"]))
            units = order["volume_remain"]
            buy = order["is_buy_order"]
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise ValueError("Invalid market order") from exc
        if (
            not price.is_finite()
            or price < 0
            or isinstance(units, bool)
            or not isinstance(units, int)
            or units < 0
            or not isinstance(buy, bool)
        ):
            raise ValueError("Invalid market price, side or remaining volume")
        if units:
            values.append((buy, price, units))
    return values


def analyze_orders(
    orders: Sequence[dict], threshold: Decimal | None = None, low_depth_units: int = 5000
) -> MarketAnalysis:
    """Analyze supplied orders; callers choose a single location or the combined scope."""
    values = _prices(orders)
    buy_orders = [(price, units) for buy, price, units in values if buy]
    sell_orders = [(price, units) for buy, price, units in values if not buy]
    best_buy = max((price for price, _ in buy_orders), default=None)
    best_sell = min((price for price, _ in sell_orders), default=None)
    buy_volume = sum(units for price, units in buy_orders if price == best_buy)
    sell_volume = sum(units for price, units in sell_orders if price == best_sell)
    spread = best_sell - best_buy if best_sell is not None and best_buy is not None else None
    percent = spread / best_buy * Decimal(100) if spread is not None and best_buy else None
    safe_units = 0
    gross = Decimal(0)
    average = below = status = None
    if threshold is not None:
        eligible = [(price, units) for price, units in buy_orders if price >= threshold]
        safe_units = sum(units for _, units in eligible)
        gross = sum((price * units for price, units in eligible), Decimal(0))
        average = gross / safe_units if safe_units else None
        below = max((price for price, _ in buy_orders if price < threshold), default=None)
        status = (
            "STOP" if not safe_units else "LOW DEPTH" if safe_units < low_depth_units else "SAFE"
        )
    return MarketAnalysis(
        best_buy,
        buy_volume,
        best_sell,
        sell_volume,
        spread,
        percent,
        safe_units,
        gross,
        average,
        below,
        status,
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def snapshot_state(snapshot: ItemSnapshot, now: datetime) -> str:
    """An error, missing timestamp or data older than 30 minutes cannot be actionable."""
    if snapshot.state not in {"Live", "Cached"} or snapshot.fetched_at is None:
        return "Error"
    if _utc(now) - _utc(snapshot.fetched_at) > MAX_DATA_AGE:
        return "Error"
    return snapshot.state


def _length(value: str) -> int:
    # Count UTF-16 code units conservatively, including non-BMP station names.
    return len(value.encode("utf-16-le")) // 2


def _clip(value: str, limit: int) -> str:
    if _length(value) <= limit:
        return value
    return value.encode("utf-16-le")[: (limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def _name(value: str, limit: int = 160) -> str:
    value = " ".join(str(value).split()).replace("<", "‹").replace(">", "›")
    value = discord.utils.escape_mentions(value)
    value = discord.utils.escape_markdown(value)
    return _clip(value, limit)


def _money(value: Decimal | None, *, average: bool = False) -> str:
    if value is None:
        return "N/A"
    number = format(value, ",.2f" if average else ",f")
    if "." in number:
        number = number.rstrip("0").rstrip(".")
    return number + " ISK"


def _location(location_id: int, names: dict[int, str], settings: MarketSettings) -> str:
    if location_id == settings.jita_location_id:
        return "Jita 4-4"
    return _name(names.get(location_id) or f"Structure {location_id}")


def _data_label(snapshot: ItemSnapshot, now: datetime) -> str:
    state = snapshot_state(snapshot, now)
    if snapshot.fetched_at is None:
        return "Error • no successful read"
    age_seconds = max(0, int((_utc(now) - _utc(snapshot.fetched_at)).total_seconds()))
    age = f"{age_seconds // 60}m {age_seconds % 60}s"
    if state == "Error":
        return f"Error • last successful read {age} ago"
    return f"{state} • data age {age}"


def _quote_lines(analysis: MarketAnalysis) -> list[str]:
    buy_units = (
        f" • {analysis.best_buy_volume:,} units at best price"
        if analysis.best_buy is not None
        else ""
    )
    sell_units = (
        f" • {analysis.best_sell_volume:,} units at best price"
        if analysis.best_sell is not None
        else ""
    )
    percentage = f"{analysis.spread_percent:.2f}%" if analysis.spread_percent is not None else "N/A"
    return [
        f"🔵 Best buy: {_money(analysis.best_buy)}{buy_units}",
        f"🟣 Best sell: {_money(analysis.best_sell)}{sell_units}",
        f"Spread: {_money(analysis.spread_isk)} / {percentage}",
    ]


def _safe_lines(analysis: MarketAnalysis, threshold: Decimal) -> list[str]:
    return [
        f"Across locations: safe instant sell at ≥ {_money(threshold)}: **{analysis.safe_units:,} units**",
        f"Gross before tax: {_money(analysis.safe_gross_isk)}",
        f"Average: {_money(analysis.safe_avg_price, average=True)}",
        f"Next price below limit: {_money(analysis.next_below)}",
        f"STATUS: **{analysis.status}** {STATUS_ICONS[analysis.status or 'DATA ERROR']}",
    ]


def _best_locations(
    groups: dict[int, list[dict]],
    analysis: MarketAnalysis,
    buy: bool,
    names: dict[int, str],
    settings: MarketSettings,
) -> str:
    best = analysis.best_buy if buy else analysis.best_sell
    if best is None:
        return "N/A"
    locations = [
        _location(location_id, names, settings)
        for location_id, orders in sorted(groups.items())
        if any(side == buy and price == best for side, price, _ in _prices(orders))
    ]
    return ", ".join(locations)


def _chunks(value: str, limit: int = 1024) -> list[str]:
    """Split fields without dropping locations or items when Discord limits are reached."""
    pieces = []
    remaining = value
    while _length(remaining) > limit:
        head = remaining.encode("utf-16-le")[: limit * 2].decode("utf-16-le", errors="ignore")
        cut = head.rfind("\n")
        if cut <= 0:
            cut = head.rfind(" ")
        if cut <= 0:
            cut = len(head)
        pieces.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n ")
    if remaining:
        pieces.append(remaining)
    return pieces or ["N/A"]


def _embed_length(embed: discord.Embed) -> int:
    return sum(
        _length(value or "")
        for value in [
            embed.title,
            embed.description,
            embed.footer.text,
            embed.author.name,
            *(part for field in embed.fields for part in (field.name, field.value)),
        ]
    )


def _pack_embeds(
    description: str,
    fields: list[tuple[str, str]],
    colour: int,
    footer: str,
    now: datetime,
    *,
    title: str,
) -> list[list[discord.Embed]]:
    def new_embed(number: int) -> discord.Embed:
        embed = discord.Embed(
            title=title if number == 1 else f"{title} • continued {number}",
            description=description if number == 1 else None,
            colour=colour,
            timestamp=_utc(now),
        )
        embed.set_footer(text=footer)
        return embed

    embeds = [new_embed(1)]
    for field_name, value in fields:
        for part_number, chunk in enumerate(_chunks(value), start=1):
            suffix = f" (continued {part_number})" if part_number > 1 else ""
            name = _clip(field_name, 256 - _length(suffix)) + suffix
            current = embeds[-1]
            if (
                len(current.fields) >= 25
                or _embed_length(current) + _length(name) + _length(chunk) > 6000
            ):
                current = new_embed(len(embeds) + 1)
                embeds.append(current)
            current.add_field(name=name or "Market data", value=chunk, inline=False)
    batches: list[list[discord.Embed]] = []
    for embed in embeds:
        if (
            not batches
            or len(batches[-1]) == 10
            or sum(_embed_length(part) for part in batches[-1]) + _embed_length(embed) > 6000
        ):
            batches.append([])
        batches[-1].append(embed)
    return batches


def build_report(
    settings: MarketSettings,
    snapshots: Sequence[ItemSnapshot],
    location_names: dict[int, str],
    now: datetime,
    messages: MessageSettings | None = None,
    analyses: dict[int, PriceAnalysis] | None = None,
) -> list[list[discord.Embed]]:
    """Build separate Datacaster and other-item messages for the combined market.

    Aggregated depth describes orders across locations; items may have to be moved
    before those orders can be filled. An expired or failed item has no trade status.
    """
    messages = messages or MessageSettings()
    by_type = {snapshot.item.type_id: snapshot for snapshot in snapshots}
    items = sorted(settings.enabled_items, key=lambda item: not item.is_datacaster)
    grouped_fields: dict[bool, list[tuple[str, str]]] = {True: [], False: []}
    grouped_states: dict[bool, list[str]] = {True: [], False: []}
    datacaster_status = "DATA ERROR"
    all_locations = {settings.jita_location_id}
    for snapshot in snapshots:
        if snapshot.item.enabled:
            all_locations.update(group_orders(snapshot.orders, settings))

    for item in items:
        snapshot = by_type.get(item.type_id, ItemSnapshot(item, [], None, "Error"))
        state = snapshot_state(snapshot, now)
        datacaster = item.is_datacaster
        fields = grouped_fields[datacaster]
        threshold = settings.threshold_for(item)
        groups = group_orders(snapshot.orders, settings)
        analysis = None
        if state != "Error":
            try:
                analysis = analyze_orders(
                    [order for orders in groups.values() for order in orders],
                    threshold,
                    settings.datacaster_low_depth_units,
                )
            except (ValueError, InvalidOperation, ArithmeticError):
                state = "Error"
        grouped_states[datacaster].append(state)
        if state == "Error":
            fields.append(
                (
                    f"⚪ {_name(item.display_name, 240)}",
                    "Data unavailable • STATUS: **DATA ERROR** ⚪\n"
                    "Do not use unavailable or expired data for trading decisions.\n"
                    + _data_label(ItemSnapshot(item, [], snapshot.fetched_at, "Error"), now),
                )
            )
            if datacaster:
                datacaster_status = "DATA ERROR"
            continue

        assert analysis is not None
        lines = _quote_lines(analysis)
        lines.extend(
            [
                f"Best buy locations: {_best_locations(groups, analysis, True, location_names, settings)}",
                f"Best sell locations: {_best_locations(groups, analysis, False, location_names, settings)}",
            ]
        )
        if threshold is not None:
            lines.extend(_safe_lines(analysis, threshold))
        if analyses and item.type_id in analyses:
            lines.append(summary_line(analyses[item.type_id]))
        lines.append(_data_label(snapshot, now))
        item_icon = (
            STATUS_ICONS[analysis.status or "DATA ERROR"]
            if datacaster
            else ITEM_ICONS.get(item.type_id, "🟣")
        )
        fields.append((f"{item_icon} {_name(item.display_name, 240)}", "\n".join(lines)))
        if not datacaster:
            continue
        datacaster_status = analysis.status or "DATA ERROR"
        assert threshold is not None
        local_analyses = {
            location_id: analyze_orders(
                groups.get(location_id, []), threshold, settings.datacaster_low_depth_units
            )
            for location_id in sorted(all_locations)
        }
        deepest = max(
            local_analyses, key=lambda location_id: local_analyses[location_id].safe_units
        )
        summary = (
            f"Best Datacaster buy: {_money(analysis.best_buy)}\n"
            f"At: {_best_locations(groups, analysis, True, location_names, settings)}\n"
            f"Largest safe depth in one location: **{local_analyses[deepest].safe_units:,} units** "
            f"at {_location(deepest, location_names, settings)}\n"
            f"Across locations safe depth: **{analysis.safe_units:,} units**\n"
            f"{_data_label(snapshot, now)}"
        )
        fields.insert(0, (f"{STATUS_ICONS[datacaster_status]} Datacaster summary", summary))
        breakdown = [
            f"**{_location(location_id, location_names, settings)}** • location_id {location_id}\n"
            f"Safe: {result.safe_units:,} units • Best buy: {_money(result.best_buy)} • "
            f"{result.status} {STATUS_ICONS[result.status or 'DATA ERROR']}"
            for location_id, result in local_analyses.items()
        ]
        fields.append(("Datacaster safe depth by location", "\n".join(breakdown)))

    if not items:
        grouped_fields[False].append(("Market data", "No enabled monitored items."))
    description = messages.render(
        "market_description",
        updated=f"{_utc(now):%Y-%m-%d %H:%M:%S}",
        interval=f"{settings.interval_minutes:g}",
    )
    batches: list[list[discord.Embed]] = []
    for datacaster, label in ((True, "Datacasters"), (False, "Other items")):
        fields = grouped_fields[datacaster]
        if not fields:
            continue
        states = grouped_states[datacaster]
        state = "Error" if "Error" in states else "Cached" if "Cached" in states else "Live"
        group_colour = (
            STATUS_COLOURS[datacaster_status]
            if datacaster
            else (STATUS_COLOURS["DATA ERROR"] if state == "Error" else OTHER_ITEMS_COLOUR)
        )
        group_icon = (
            STATUS_ICONS[datacaster_status]
            if datacaster
            else (STATUS_ICONS["DATA ERROR"] if state == "Error" else "🟣")
        )
        # Keep delivery batches separate even when both groups fit in one message.
        batches.extend(
            _pack_embeds(
                description,
                fields,
                group_colour,
                messages.render("market_footer", state=state),
                now,
                title=messages.render("market_title", icon=group_icon, group=label, state=state),
            )
        )
    return batches
