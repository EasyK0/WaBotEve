"""Editable Discord text with a small, validated placeholder vocabulary."""

from dataclasses import dataclass, field
from string import Formatter


def text_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def clip_text(value: str, limit: int) -> str:
    if text_length(value) <= limit:
        return value
    return (
        value.encode("utf-16-le")[: max(0, limit - 1) * 2].decode("utf-16-le", errors="ignore")
        + "…"
    )


@dataclass(frozen=True)
class Template:
    label: str
    default: str
    examples: dict[str, str]
    limit: int = 2000
    empty: bool = False


TIMER_VALUES = {
    "timer_id": "42",
    "system": "Kino",
    "payout": "20:20 EVE Time (UTC)",
    "respawn": "20:43 EVE Time (UTC)",
    "remaining": "<t:1789994580:R>",
    "author": "Pilot",
    "title": "Completion recorded",
    "increment": "+1",
}
RETIRED_PVP_KEYS = {
    "pvp_content",
    "pvp_title",
    "pvp_summary",
    "pvp_ships_label",
    "pvp_groups_label",
    "pvp_links_label",
    "pvp_report_label",
    "pvp_footer",
}
TEMPLATES = {
    "timer_reminder": Template(
        "Timer — ping after 23 minutes",
        "{delayed}{mention}A new site is expected. Check Kino, Niarja and Archee! "
        "After completing it, submit /timer with the payout time in EVE Time (UTC) "
        "and the completion system. Timer ID: {timer_id}",
        TIMER_VALUES | {"mention": "@everyone — ", "delayed": ""},
    ),
    "timer_delayed_prefix": Template("Timer — delay prefix", "Delayed reminder — ", {}, 100, True),
    "timer_confirmation": Template(
        "Timer — /timer confirmation",
        "{title} — {system} | Payout: {payout} | Expected respawn: {respawn} | "
        "Remaining: {remaining} | Completed sites: {increment} | Timer ID: {timer_id} | Reported by: {author}",
        TIMER_VALUES,
    ),
    "stats": Template(
        "Statistics — /stats",
        "**Reported completions — All time**\n{rows}\n**Total: {total}**",
        {"rows": "Kino: 3\nNiarja: 2\nArchee: 1", "total": "6"},
    ),
    "rank": Template(
        "Leaderboard — /rank",
        "**Completion reports leaderboard — All time**\n{rows}\nPage {page} of {pages}",
        {"rows": "1. Pilot — 3\n2. Scout — 2", "page": "1", "pages": "1"},
    ),
    "market_title": Template(
        "Market — title",
        "{icon} Jita and Perimeter Market Report • {group}",
        {"icon": "🟢", "group": "Datacasters", "state": "Live"},
        200,
    ),
    "market_description": Template(
        "Market — description",
        "Updated: {updated} EVE Time (UTC)\nAutomatic reports every {interval} minutes.\n"
        "Combined Jita 4-4 + Perimeter market. Prices and volumes aggregate the selected locations.\n"
        "**Across locations totals require selling at the listed locations; transporting items may be necessary.**\n"
        "Public ESI coverage only: player structure orders may be incomplete.",
        {"updated": "2026-09-21 01:34:00", "interval": "60"},
        1200,
    ),
    "market_footer": Template(
        "Market — footer", "Data source ESI • {state}", {"state": "Live"}, 400
    ),
}


def validate_template(key, value):
    spec = TEMPLATES[key]
    if not isinstance(value, str) or (not spec.empty and not value.strip()):
        raise ValueError(f"{spec.label}: message cannot be empty.")
    if text_length(value) > spec.limit:
        raise ValueError(f"{spec.label}: maximum {spec.limit} characters.")
    try:
        for _, name, format_spec, conversion in Formatter().parse(value):
            if name is not None and (name not in spec.examples or format_spec or conversion):
                raise ValueError(f"Unknown placeholder: {{{name}}}")
    except ValueError as exc:
        raise ValueError(
            f"{spec.label}: invalid placeholder. Use only the listed {{names}}."
        ) from exc
    # Optional mentions/delay markers must not be the entire message. Discord
    # rejects an empty rendered reminder even though its template was nonempty.
    if not spec.empty:
        example = spec.examples | {
            key: "" for key in ("mention", "delayed") if key in spec.examples
        }
        if not value.format_map(example).strip():
            raise ValueError(f"{spec.label}: add text besides optional ping/delay placeholders.")


@dataclass(frozen=True)
class MessageSettings:
    overrides: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.overrides, dict) or set(self.overrides) - set(TEMPLATES):
            raise ValueError("messages must be an object with known message names.")
        for key, value in self.overrides.items():
            validate_template(key, value)
        object.__setattr__(self, "overrides", dict(self.overrides))

    def text(self, key):
        return self.overrides.get(key, TEMPLATES[key].default)

    def render(self, key, **values):
        text = self.text(key).format_map(values)
        return clip_text(text, TEMPLATES[key].limit)

    def timer_mentions_everyone(self) -> bool:
        for delayed in ("", self.render("timer_delayed_prefix")):
            text = self.render(
                "timer_reminder", **(TIMER_VALUES | {"mention": "@everyone — ", "delayed": delayed})
            )
            if "@everyone" in text or "@here" in text:
                return True
        return False

    @classmethod
    def from_config(cls, config=None):
        if isinstance(config, dict):
            config = {key: value for key, value in config.items() if key not in RETIRED_PVP_KEYS}
        return cls({} if config is None else config)
