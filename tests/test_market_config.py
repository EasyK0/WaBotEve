import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from wabot.config import ConfigurationError, Settings
from wabot.market_config import (
    DATACASTER_NAME,
    DATACASTER_TYPE_ID,
    DEFAULT_MARKET_ITEMS,
    MarketItem,
    MarketSettings,
)


def parse(config=None, **environment):
    return MarketSettings.from_config(config, environ=environment)


@pytest.fixture
def config_environment(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    for key in (
        "MARKET_REPORT_ENABLED",
        "MARKET_CHANNEL_ID",
        "MARKET_INTERVAL_MINUTES",
        "MARKET_REGION_ID",
        "JITA_LOCATION_ID",
        "PERIMETER_SYSTEM_ID",
        "DATACASTER_MIN_BUY_PRICE",
        "DATACASTER_LOW_DEPTH_UNITS",
        "MARKET_REQUEST_TIMEOUT_SECONDS",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CONFIG_PATH", str(path))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "bot.sqlite3"))
    return path


def test_legacy_configuration_is_disabled_and_positional_settings_still_work(config_environment):
    config_environment.write_text(json.dumps({"guild_ids": ["123"]}), encoding="utf-8")
    loaded = Settings.from_env(require_token=False)
    positional = Settings("unused", ("123",), Path("db"), Path("backups"), None, "DEBUG")
    assert loaded.market == positional.market == MarketSettings()
    assert not loaded.market.enabled
    assert positional.log_level == "DEBUG"
    assert len(loaded.market.enabled_items) == 6


def test_defaults_have_official_unique_ids_and_datacaster_floor():
    assert [item.type_id for item in DEFAULT_MARKET_ITEMS] == [
        55831,
        52244,
        52240,
        92541,
        52238,
        52236,
    ]
    settings = parse()
    assert settings.threshold_for(DEFAULT_MARKET_ITEMS[0]) == Decimal("500000")
    assert settings.threshold_for(DEFAULT_MARKET_ITEMS[1]) is None
    with pytest.raises(FrozenInstanceError):
        settings.enabled = True


def test_environment_overrides_json_and_preserves_decimal_precision():
    settings = parse(
        {"enabled": False, "channel_id": "1", "interval_minutes": 10},
        MARKET_REPORT_ENABLED="TRUE",
        MARKET_CHANNEL_ID="1234567890123456789",
        MARKET_INTERVAL_MINUTES="2.5",
        MARKET_REGION_ID="10000002",
        JITA_LOCATION_ID="60003760",
        PERIMETER_SYSTEM_ID="30000144",
        DATACASTER_MIN_BUY_PRICE="500000.000000000000000001",
        DATACASTER_LOW_DEPTH_UNITS="125",
        MARKET_REQUEST_TIMEOUT_SECONDS="3.5",
    )
    assert settings.enabled
    assert settings.channel_id == "1234567890123456789"
    assert settings.interval_minutes == 2.5
    assert settings.request_timeout_seconds == 3.5
    assert settings.datacaster_low_depth_units == 125
    assert settings.threshold_for(settings.items[0]) == Decimal("500000.000000000000000001")


def test_json_decimal_prices_are_not_rounded_by_float_parser(config_environment):
    config_environment.write_text(
        '{"guild_ids":["1"],"market":{"datacaster_min_buy_price":500000.000000000000000001}}',
        encoding="utf-8",
    )
    assert Settings.from_env(require_token=False).market.datacaster_min_buy_price == Decimal(
        "500000.000000000000000001"
    )


@pytest.mark.parametrize(
    "market",
    [
        None,
        [],
        True,
        {"unexpected": 1},
        {"enabled": "true"},
        {"enabled": 1},
        {"enabled": True},
        {"channel_id": 123},
        {"channel_id": "0"},
        {"channel_id": "01"},
        {"channel_id": str(2**64)},
        {"channel_id": "channel"},
        {"channel_id": ""},
        {"interval_minutes": 0},
        {"interval_minutes": -1},
        {"interval_minutes": True},
        {"interval_minutes": "10"},
        {"interval_minutes": float("inf")},
        {"request_timeout_seconds": 0},
        {"request_timeout_seconds": float("nan")},
        {"region_id": 1.5},
        {"region_id": True},
        {"jita_location_id": "60003760"},
        {"perimeter_system_id": -1},
        {"datacaster_low_depth_units": False},
        {"datacaster_low_depth_units": 0},
        {"datacaster_min_buy_price": "499999.999999999999999999"},
        {"datacaster_min_buy_price": "Infinity"},
        {"datacaster_min_buy_price": "NaN"},
        {"datacaster_min_buy_price": True},
        {"datacaster_min_buy_price": "nonsense"},
        {"items": []},
        {"items": {}},
        {"items": [None]},
        {"items": [{}]},
        {"items": [{"type_id": 1, "display_name": "Item", "unknown": 1}]},
    ],
)
def test_invalid_market_values_raise_application_configuration_error(config_environment, market):
    config_environment.write_text(
        json.dumps({"guild_ids": ["1"], "market": market}), encoding="utf-8"
    )
    with pytest.raises(ConfigurationError):
        Settings.from_env(require_token=False)


@pytest.mark.parametrize(
    "environment",
    [
        {"MARKET_REPORT_ENABLED": "yes"},
        {"MARKET_REPORT_ENABLED": "1"},
        {"MARKET_REPORT_ENABLED": "true", "MARKET_CHANNEL_ID": ""},
        {"MARKET_INTERVAL_MINUTES": "NaN"},
        {"MARKET_REQUEST_TIMEOUT_SECONDS": "Infinity"},
        {"MARKET_REQUEST_TIMEOUT_SECONDS": "slow"},
        {"MARKET_REGION_ID": "1.5"},
        {"JITA_LOCATION_ID": "-1"},
        {"DATACASTER_LOW_DEPTH_UNITS": "0"},
        {"DATACASTER_MIN_BUY_PRICE": "499999.99"},
    ],
)
def test_invalid_environment_rejected(environment):
    with pytest.raises(ValueError):
        MarketSettings.from_config(environ=environment)


@pytest.mark.parametrize(
    "item",
    [
        {"type_id": True, "display_name": "Item"},
        {"type_id": "1", "display_name": "Item"},
        {"type_id": 1.5, "display_name": "Item"},
        {"type_id": 0, "display_name": "Item"},
        {"type_id": 1, "display_name": ""},
        {"type_id": 1, "display_name": " "},
        {"type_id": 1, "display_name": " Item "},
        {"type_id": 1, "display_name": None},
        {"type_id": 1, "display_name": "Item", "enabled": "false"},
        {"type_id": 1, "display_name": "Item", "min_buy_price": True},
        {"type_id": 1, "display_name": "Item", "min_buy_price": "NaN"},
        {"type_id": 1, "display_name": "Item", "min_buy_price": 0},
    ],
)
def test_invalid_items_rejected(item):
    with pytest.raises(ValueError):
        parse({"items": [item]})


@pytest.mark.parametrize(
    "second",
    [
        {"type_id": 1, "display_name": "Second"},
        {"type_id": 2, "display_name": "First"},
        {"type_id": 2, "display_name": "FIRST"},
    ],
)
def test_duplicate_items_rejected(second):
    with pytest.raises(ValueError, match="duplicate"):
        parse({"items": [{"type_id": 1, "display_name": "First"}, second]})


def test_disabled_items_can_be_kept_but_reports_require_an_enabled_item():
    items = [{"type_id": 1, "display_name": "Example", "enabled": False}]
    assert parse({"items": items}).enabled_items == ()
    with pytest.raises(ValueError, match="at least one"):
        parse({"items": items, "enabled": True, "channel_id": "123"})


@pytest.mark.parametrize("type_id,name", [(DATACASTER_TYPE_ID, "Wrong name"), (1, DATACASTER_NAME)])
def test_datacaster_floor_cannot_be_bypassed_by_name_or_id(type_id, name):
    with pytest.raises(ValueError, match="500000"):
        MarketItem(type_id, name, min_buy_price=Decimal("499999.99"))


def test_custom_thresholds_apply_and_datacaster_uses_stricter_threshold():
    settings = parse({"datacaster_min_buy_price": "600000"})
    assert settings.threshold_for(MarketItem(1, "Another item", min_buy_price="12.34")) == Decimal(
        "12.34"
    )
    assert settings.threshold_for(MarketItem(DATACASTER_TYPE_ID, DATACASTER_NAME)) == Decimal(
        "600000"
    )
    assert settings.threshold_for(
        MarketItem(DATACASTER_TYPE_ID, DATACASTER_NAME, min_buy_price=Decimal("700000"))
    ) == Decimal("700000")


def test_example_ships_without_a_private_channel(config_environment):
    example = Path(__file__).parents[1] / "config.example.json"
    config = json.loads(example.read_text(encoding="utf-8"))
    config["guild_ids"] = ["123"]
    config_environment.write_text(json.dumps(config), encoding="utf-8")
    settings = Settings.from_env(require_token=False)
    assert not settings.market.enabled
    assert settings.market.channel_id is None
    assert settings.market.items == DEFAULT_MARKET_ITEMS
