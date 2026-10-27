import time
from unittest.mock import Mock

import pytest

from wabot.channel_test import ChannelTestResult, saved_channels
from wabot.desktop_launcher import Launcher
from wabot.desktop_support import DesktopProfile, read_config
from wabot.messages import TEMPLATES


@pytest.fixture
def launcher(tmp_path, tk_window):
    root = tk_window
    profile = DesktopProfile(tmp_path)
    profile.save({"guild_ids": ["123"], "market": {"enabled": True, "channel_id": "456"}})
    app = Launcher(root, profile)
    app._error = Mock()
    deadline = time.monotonic() + 5
    while app.items_panel.loading and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    assert not app.items_panel.loading
    yield app


def test_system_routes_save_shared_channel_reload_and_remove(launcher):
    panel = launcher.pvp_panel
    for system, channel in (("Kino", "789"), ("Niarja", "789"), ("Archee", "987")):
        panel.channels[system].set(channel)
    panel.ping.set("none")
    panel.save()
    launcher._error.assert_not_called()
    config = read_config(launcher.profile.config_path)
    assert "pvp" not in config
    pvp = config["servers"][0]["pvp"]
    assert pvp["system_channels"] == {"Kino": "789", "Niarja": "789", "Archee": "987"}
    panel.reload()
    assert panel.channels["Archee"].get() == "987"
    panel.channels["Archee"].set("")
    panel.save()
    saved = read_config(launcher.profile.config_path)["servers"][0]["pvp"]
    assert "Archee" not in saved["system_channels"]


def test_panel_has_no_retired_threshold_controls(launcher):
    panel = launcher.pvp_panel
    for name in ("channel", "min_kills", "window", "cooldown", "legacy_frame"):
        assert not hasattr(panel, name)
    panel.ping.set("role")
    panel.save()
    launcher._error.assert_called_once()  # A role ping needs a role ID.


def test_test_button_uses_saved_routes_and_runs_while_bot_is_active(launcher, monkeypatch):
    panel = launcher.pvp_panel
    panel.channels["Kino"].set("789")
    panel.save()
    panel.channels["Kino"].set("999")  # Unsaved draft must not redirect the test.
    launcher.process = Mock()
    monkeypatch.setattr(DesktopProfile, "token", lambda self: "offline-test-token")
    jobs = []
    monkeypatch.setattr(
        "wabot.desktop_launcher.threading.Thread",
        lambda **kwargs: Mock(start=lambda: jobs.append(kwargs["target"])),
    )

    async def send(token, guilds, channels, *, report):
        assert channels == {"789": ["Kino"]}
        assert guilds == ("123",)
        result = ChannelTestResult("789", True, "Test message sent.")
        report(result)
        return [result]

    monkeypatch.setattr("wabot.desktop_launcher.send_test_messages", send)
    panel.test_button.invoke()
    assert launcher.testing_channels and len(jobs) == 1
    launcher.test_channels(pvp_only=True)
    assert len(jobs) == 1
    jobs[0]()
    launcher._poll()
    assert not launcher.testing_channels
    assert "1/1" in launcher.channel_test_notice.get()
    assert panel.channels["Kino"].get() == "999"
    launcher.process = None


def test_test_button_requires_saved_channel_and_token(launcher):
    launcher.test_channels(pvp_only=True)
    launcher._error.assert_called_once()
    assert not launcher.testing_channels
    launcher._error.reset_mock()
    panel = launcher.pvp_panel
    panel.channels["Kino"].set("789")
    panel.save()
    launcher.test_channels(pvp_only=True)
    launcher._error.assert_called_once()
    assert not launcher.testing_channels


def test_timer_editor_preview_save_validation_reset_and_reopen(launcher):
    panel = launcher.messages_panel
    assert panel.key == "timer_reminder"
    panel.editor.delete("1.0", "end")
    panel.editor.insert("1.0", "{mention}Fleet: check {system}! Timer {timer_id}")
    panel.preview()
    assert "@everyone" in panel.output.get("1.0", "end")
    assert "Kino" in panel.output.get("1.0", "end")
    panel.save()
    saved = read_config(launcher.profile.config_path)
    assert saved["messages"]["timer_reminder"].startswith("{mention}Fleet")
    assert saved["servers"][0]["market"]["channel_id"] == "456"
    panel.editor.insert("end", " {unknown}")
    panel.save()
    launcher._error.assert_called_once()
    assert read_config(launcher.profile.config_path) == saved
    launcher.config = saved
    launcher._load_fields()
    assert panel.editor.get("1.0", "end-1c") == saved["messages"]["timer_reminder"]
    panel.reset()
    assert panel.editor.get("1.0", "end-1c") == TEMPLATES["timer_reminder"].default
    panel.save()
    assert read_config(launcher.profile.config_path)["messages"] == {}


def test_switching_templates_keeps_drafts_and_running_guard(launcher):
    panel = launcher.messages_panel
    panel.editor.delete("1.0", "end")
    panel.editor.insert("1.0", "Timer {timer_id}")
    panel.choice.set(TEMPLATES["market_title"].label)
    panel.select()
    panel.editor.delete("1.0", "end")
    panel.editor.insert("1.0", "Report: {group}")
    panel.save()
    assert set(read_config(launcher.profile.config_path)["messages"]) == {
        "timer_reminder",
        "market_title",
    }
    launcher.process = Mock()
    launcher._controls()
    original = launcher.profile.config_path.read_bytes()
    panel.drafts["timer_reminder"] = "changed"
    panel.save()
    assert launcher.profile.config_path.read_bytes() == original


def test_saving_connection_settings_preserves_message_and_pvp_drafts(launcher):
    panel = launcher.messages_panel
    panel.editor.delete("1.0", "end")
    panel.editor.insert("1.0", "Draft timer {timer_id}")
    launcher.pvp_panel.channels["Kino"].set("777")
    launcher.interval.set("90")
    launcher.save_market()
    assert panel.editor.get("1.0", "end-1c") == "Draft timer {timer_id}"
    assert launcher.pvp_panel.channels["Kino"].get() == "777"
    launcher._error.assert_not_called()


def wait_statistics(launcher):
    deadline = time.monotonic() + 5
    while launcher.statistics_panel.loading and time.monotonic() < deadline:
        launcher.root.update()
        time.sleep(0.01)
    assert not launcher.statistics_panel.loading


def test_statistics_gui_add_edit_zero_and_history(launcher):
    panel = launcher.statistics_panel
    panel.refresh()
    wait_statistics(launcher)
    panel.author.set("789")
    panel.name.set("Pilot")
    panel.count.set("5")
    panel.save()
    wait_statistics(launcher)
    assert panel.players["789"]["kino"] == 5
    assert len(panel.history.get_children()) == 1
    panel.count.set("0")
    panel.save()
    wait_statistics(launcher)
    assert panel.players["789"]["kino"] == 0
    assert len(panel.history.get_children()) == 2
    launcher._error.assert_not_called()
    launcher.process = Mock()
    panel.count.set("9")
    panel.save()
    assert not panel.loading
    assert panel.players["789"]["kino"] == 0


def test_add_server_toggle_feature_shows_its_tab_and_saves(launcher, monkeypatch):
    answers = iter(["987654321", "Drugi serwer"])
    monkeypatch.setattr(
        "wabot.desktop_launcher.simpledialog.askstring", lambda *a, **k: next(answers)
    )
    launcher.add_server()
    launcher._error.assert_not_called()
    assert launcher.current_guild == "987654321"
    assert launcher.server_label.get() == "Drugi serwer (987654321)"
    # A new server starts with every feature off and hidden.
    for tab in launcher.feature_tabs.values():
        assert launcher.tabs.tab(tab, "state") == "hidden"
    switch = launcher.feature_checks[2]
    switch.invoke()
    assert launcher.tabs.tab(launcher.pvp_tab, "state") == "normal"
    assert launcher.tabs.select() == str(launcher.pvp_tab)
    saved = read_config(launcher.profile.config_path)
    assert saved["guild_ids"] == ["123", "987654321"]
    assert saved["servers"][1]["features"]["pvp"] is True
    assert saved["servers"][0]["market"]["channel_id"] == "456"
    # Switching back to the first server shows its own features.
    launcher.server_label.set(launcher.server_choice.cget("values")[0])
    launcher.select_server()
    assert launcher.tabs.tab(launcher.market_tab, "state") == "normal"
    assert launcher.tabs.tab(launcher.pvp_tab, "state") == "hidden"
    # Feature switches are locked while the bot runs.
    launcher.process = Mock()
    launcher._controls()
    launcher.feature_checks[2].invoke()
    assert not launcher.feature_vars["pvp"].get()
    launcher.process = None


def test_last_server_cannot_be_removed(launcher):
    launcher.remove_server()
    launcher._error.assert_called_once()
    assert read_config(launcher.profile.config_path)["guild_ids"] == ["123"]


def test_pvp_page_is_named_pvp_and_role_field_follows_ping(launcher):
    panel = launcher.pvp_panel
    assert any(button.cget("text") == "PvP" for button in launcher.nav_frame.winfo_children())
    panel.ping.set("none")
    panel._ping_changed()
    assert str(panel.role_entry.cget("state")) == "disabled"
    panel.ping.set("role")
    panel._ping_changed()
    assert str(panel.role_entry.cget("state")) == "normal"
    panel.alliances.set("99003581; 1354830081")
    panel.alliance_channel.set("555")
    panel.ping.set("none")
    panel.save()
    launcher._error.assert_not_called()
    assert panel.alliance_state.get().startswith("On: 2")


def test_all_pochven_channel_is_saved_and_listed_for_tests(launcher):
    panel = launcher.pvp_panel
    panel.pochven_channel.set("555")
    panel.ping.set("none")
    panel.save()
    launcher._error.assert_not_called()
    config = read_config(launcher.profile.config_path)
    assert config["servers"][0]["pvp"]["pochven_channel"] == "555"
    assert saved_channels(config, pvp_only=True) == {"555": ["All Pochven"]}
    assert "All Pochven" in panel.pochven_state.get()
