import time
from unittest.mock import Mock

import pytest

from wabot.desktop_launcher import Launcher
from wabot.desktop_support import DesktopProfile, read_config


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
    assert app.items_panel.catalog is not None
    yield app


def select_item(panel):
    panel.query.set("34")
    panel.results.selection_set("34")
    panel._buttons()


def test_gui_add_save_reload_remove_and_running_guard(launcher):
    panel = launcher.items_panel
    select_item(panel)
    panel.add_button.invoke()
    launcher._error.assert_not_called()
    saved = read_config(launcher.profile.config_path)
    assert len(saved["market"]["items"]) == 7
    assert saved["market"]["items"][-1]["display_name"] == "Tritanium"
    assert str(panel.add_button["state"]) == "disabled"
    launcher.config = read_config(launcher.profile.config_path)
    launcher._load_fields()
    assert "34" in panel.monitored.get_children()
    panel.monitored.selection_set("34")
    launcher.process = Mock()
    launcher._controls()
    panel.remove()  # Even direct callbacks must respect the running guard.
    assert len(read_config(launcher.profile.config_path)["market"]["items"]) == 7
    assert str(panel.remove_button["state"]) == "disabled"
    launcher.process = None
    launcher._controls()
    panel.remove_button.invoke()
    assert len(read_config(launcher.profile.config_path)["market"]["items"]) == 6
    launcher._error.assert_not_called()


def test_failed_save_keeps_ui_and_configuration(launcher):
    panel = launcher.items_panel
    path = launcher.profile.config_path
    original = path.read_bytes()
    select_item(panel)
    panel.launcher.profile = Mock()
    panel.launcher.profile.save.side_effect = OSError("disk unavailable")
    panel.add_button.invoke()
    launcher._error.assert_called_once()
    assert "34" not in panel.monitored.get_children()
    assert "items" not in launcher.config["market"]
    assert path.read_bytes() == original


def test_nonmarket_item_cannot_be_added(launcher):
    panel = launcher.items_panel
    item = next(item for item in panel.catalog.items if not item.marketable)
    panel.query.set(str(item.type_id))
    panel.results.selection_set(str(item.type_id))
    panel._buttons()
    assert str(panel.add_button["state"]) == "disabled"
