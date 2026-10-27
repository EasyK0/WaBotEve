import json
from types import SimpleNamespace

import pytest

from wabot.config import ConfigurationError, Settings
from wabot.desktop_support import validate_config
from wabot.formatting import rank_message, stats_message, timer_message
from wabot.messages import TEMPLATES, MessageSettings
from wabot.models import RankEntry, RankPage, Timer
from wabot.transport import reminder_payload


@pytest.mark.parametrize("key", TEMPLATES)
def test_every_default_template_validates_and_previews(key):
    spec = TEMPLATES[key]
    messages = MessageSettings({key: spec.default})
    assert len(messages.render(key, **spec.examples)) <= spec.limit


@pytest.mark.parametrize(
    "template",
    [
        "{bad}",
        "{system.__class__}",
        "{system[0]}",
        "{timer_id:10000}",
        "{system!r}",
        "{",
        "{}",
        "",
        "x" * 2001,
    ],
)
def test_invalid_templates_are_rejected(template):
    with pytest.raises(ValueError):
        MessageSettings({"timer_reminder": template})


def test_custom_timer_uses_actual_values_and_does_not_expand_injected_fields():
    timer = Timer(42, "100", "10", "20", "30", "@everyone {timer_id}", "kino", 0, 1380, 1)
    messages = MessageSettings({"timer_reminder": "{mention}Go to {system}! {timer_id} — {author}"})
    result = reminder_payload(timer, delayed=False, mention_everyone=True, messages=messages)
    assert result["content"].startswith("@everyone — Go to Kino! 42")
    assert result["content"].count("@everyone") == 1
    assert "{timer\\_id}" in result["content"]
    assert result["allowed_mentions"]["users"] == []
    messages = MessageSettings({"timer_reminder": "Go to {system}!"})
    assert (
        "@"
        not in reminder_payload(timer, delayed=False, mention_everyone=True, messages=messages)[
            "content"
        ]
    )


def test_custom_confirmation_stats_and_rank():
    messages = MessageSettings(
        {
            "timer_confirmation": "Saved #{timer_id}: {system}",
            "stats": "Total {total}\n{rows}",
            "rank": "Page {page}/{pages}\n{rows}",
        }
    )
    timer = Timer(42, "100", "10", "20", "30", "Pilot", "kino", 0, 1380, 1)
    assert timer_message(timer, inserted=True, messages=messages) == "Saved #42: Kino"
    assert stats_message({"kino": 1, "niarja": 0, "archee": 0, "total": 1}, messages).startswith(
        "Total 1"
    )
    page = RankPage(
        page=1, total_pages=1, entries=[RankEntry("30", "Pilot", 1, 1)], total_authors=1
    )
    assert rank_message(page, SimpleNamespace(get_member=lambda _: None), messages).startswith(
        "Page 1/1"
    )


def test_cli_and_desktop_share_validation(tmp_path, monkeypatch):
    config = {
        "guild_ids": ["1"],
        "pvp": {"enabled": False, "channel_id": None},
        "messages": {"timer_reminder": "{mention}Check {system}!"},
    }
    validate_config(config)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("CONFIG_PATH", str(path))
    assert (
        Settings.from_env(require_token=False).messages.text("timer_reminder")
        == "{mention}Check {system}!"
    )
    for invalid in (None, [], {"timer_reminder": "{typo}"}):
        config["messages"] = invalid
        with pytest.raises(ValueError):
            validate_config(config)
        path.write_text(json.dumps(config))
        with pytest.raises(ConfigurationError):
            Settings.from_env(require_token=False)


@pytest.mark.parametrize("template", ["{mention}", "{delayed}", "{mention}{delayed} "])
def test_timer_template_must_remain_nonempty_without_a_ping(template):
    with pytest.raises(ValueError, match="optional"):
        MessageSettings({"timer_reminder": template})


def test_unicode_message_limits_include_emoji_and_expanded_placeholders():
    from wabot.messages import text_length

    with pytest.raises(ValueError, match="maximum"):
        MessageSettings({"timer_reminder": "🚀" * 1001})
    messages = MessageSettings({"timer_reminder": "{author}{author}"})
    result = messages.render("timer_reminder", author="🚀" * 900)
    assert text_length(result) <= 2000
    assert result.endswith("…")


def test_retired_pvp_templates_are_removed_when_loading_old_profiles():
    config = validate_config(
        {
            "guild_ids": ["1"],
            "messages": {
                "pvp_content": "old fight ping",
                "timer_reminder": "Check {system}!",
            },
        }
    )
    assert config["messages"] == {"timer_reminder": "Check {system}!"}
    assert all(not key.startswith("pvp_") for key in TEMPLATES)
