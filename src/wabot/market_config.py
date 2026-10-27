"""Market configuration, independent of Discord and the ESI network client."""

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation

DATACASTER_TYPE_ID = 55831
DATACASTER_NAME = "Triglavian Transconduit Datacaster"
DATACASTER_PRICE_FLOOR = Decimal("500000")


def _positive_integer(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _price(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f"{name} must be a positive, finite decimal price.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a positive, finite decimal price.") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{name} must be a positive, finite decimal price.")
    return result


def _percent(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f"{name} must be a percentage from 0 to 100.")
    try:
        result = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a percentage from 0 to 100.") from exc
    if not result.is_finite() or not 0 <= result < 100:
        raise ValueError(f"{name} must be a percentage from 0 to 100.")
    return result


def _positive_duration(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{name} must be a positive, finite number.")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive, finite number.") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive, finite number.")
    return result


@dataclass(frozen=True, slots=True)
class MarketItem:
    type_id: int
    display_name: str
    enabled: bool = True
    min_buy_price: Decimal | None = None

    def __post_init__(self) -> None:
        _positive_integer(self.type_id, "market.items.type_id")
        if (
            not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or self.display_name != self.display_name.strip()
        ):
            raise ValueError("market.items.display_name must be an exact, non-empty item name.")
        if type(self.enabled) is not bool:
            raise ValueError("market.items.enabled must be a boolean.")
        if self.min_buy_price is not None:
            price = _price(self.min_buy_price, "market.items.min_buy_price")
            object.__setattr__(self, "min_buy_price", price)
            if self.enabled and self.is_datacaster and price < DATACASTER_PRICE_FLOOR:
                raise ValueError("Datacaster min_buy_price cannot be below 500000 ISK.")

    @property
    def is_datacaster(self) -> bool:
        return self.type_id == DATACASTER_TYPE_ID or self.display_name == DATACASTER_NAME


DEFAULT_MARKET_ITEMS = (
    MarketItem(DATACASTER_TYPE_ID, DATACASTER_NAME, min_buy_price=DATACASTER_PRICE_FLOOR),
    MarketItem(52244, "Veles Entropic Radiation Sink"),
    MarketItem(52240, "Zorya's Supratidal Entropic Disintegrator"),
    MarketItem(92541, "Capital Disintegrator Specialization"),
    MarketItem(52238, "Zorya's Heavy Entropic Disintegrator"),
    MarketItem(52236, "Zorya's Light Entropic Disintegrator"),
)

_ENV_FIELDS = {
    "MARKET_REPORT_ENABLED": "enabled",
    "MARKET_CHANNEL_ID": "channel_id",
    "MARKET_INTERVAL_MINUTES": "interval_minutes",
    "MARKET_REGION_ID": "region_id",
    "JITA_LOCATION_ID": "jita_location_id",
    "PERIMETER_SYSTEM_ID": "perimeter_system_id",
    "DATACASTER_MIN_BUY_PRICE": "datacaster_min_buy_price",
    "DATACASTER_LOW_DEPTH_UNITS": "datacaster_low_depth_units",
    "MARKET_REQUEST_TIMEOUT_SECONDS": "request_timeout_seconds",
}
_INTEGER_FIELDS = {
    "region_id",
    "jita_location_id",
    "perimeter_system_id",
    "datacaster_low_depth_units",
}
_DURATION_FIELDS = {"interval_minutes", "request_timeout_seconds"}


@dataclass(frozen=True, slots=True)
class MarketSettings:
    enabled: bool = False
    channel_id: str | None = None
    interval_minutes: float = 60
    region_id: int = 10000002
    jita_location_id: int = 60003760
    perimeter_system_id: int = 30000144
    datacaster_min_buy_price: Decimal = DATACASTER_PRICE_FLOOR
    datacaster_low_depth_units: int = 5000
    request_timeout_seconds: float = 20
    # Defaults assume Accounting V and Broker Relations V without standings.
    sales_tax_percent: Decimal = Decimal("3.375")
    broker_fee_percent: Decimal = Decimal("1.5")
    items: tuple[MarketItem, ...] = DEFAULT_MARKET_ITEMS

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("market.enabled must be a boolean.")
        if self.channel_id is not None and (
            not isinstance(self.channel_id, str)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", self.channel_id)
            or int(self.channel_id) >= 2**64
        ):
            raise ValueError("market.channel_id must be a Discord channel ID string.")
        if self.enabled and self.channel_id is None:
            raise ValueError("Set MARKET_CHANNEL_ID or market.channel_id before enabling reports.")
        for name in _INTEGER_FIELDS:
            _positive_integer(getattr(self, name), f"market.{name}")
        for name in _DURATION_FIELDS:
            object.__setattr__(
                self, name, _positive_duration(getattr(self, name), f"market.{name}")
            )
        price = _price(self.datacaster_min_buy_price, "market.datacaster_min_buy_price")
        if price < DATACASTER_PRICE_FLOOR:
            raise ValueError("Datacaster minimum buy price cannot be below 500000 ISK.")
        object.__setattr__(self, "datacaster_min_buy_price", price)
        for name, label in (
            ("sales_tax_percent", "Sales tax"),
            ("broker_fee_percent", "Broker fee"),
        ):
            object.__setattr__(self, name, _percent(getattr(self, name), label))
        if self.sales_tax_percent + self.broker_fee_percent >= 100:
            raise ValueError("Sales tax and broker fee together must be below 100%.")
        if not isinstance(self.items, tuple) or not self.items:
            raise ValueError("market.items must contain at least one MarketItem.")
        if any(not isinstance(item, MarketItem) for item in self.items):
            raise ValueError("market.items must contain MarketItem objects.")
        if len({item.type_id for item in self.items}) != len(self.items):
            raise ValueError("market.items must not contain duplicate type_id values.")
        if len({item.display_name.casefold() for item in self.items}) != len(self.items):
            raise ValueError("market.items must not contain duplicate display_name values.")
        if self.enabled and not self.enabled_items:
            raise ValueError("Enable at least one market item before enabling reports.")

    @property
    def enabled_items(self) -> tuple[MarketItem, ...]:
        return tuple(item for item in self.items if item.enabled)

    def threshold_for(self, item: MarketItem) -> Decimal | None:
        if item.is_datacaster:
            return max(self.datacaster_min_buy_price, item.min_buy_price or DATACASTER_PRICE_FLOOR)
        return item.min_buy_price

    @classmethod
    def from_config(
        cls, config: object = None, *, environ: Mapping[str, str] | None = None
    ) -> "MarketSettings":
        """Parse optional JSON configuration with environment overrides.

        Raises ValueError; the application configuration layer supplies its own
        ConfigurationError, avoiding a circular import.
        """
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise ValueError("market must be a configuration object.")
        unknown = set(config) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown market configuration fields: {', '.join(sorted(unknown))}.")
        values = dict(config)
        environment = os.environ if environ is None else environ
        for variable, name in _ENV_FIELDS.items():
            if variable not in environment:
                continue
            raw = environment[variable].strip()
            if name == "enabled":
                if raw.lower() not in {"true", "false"}:
                    raise ValueError(f"{variable} must be true or false.")
                values[name] = raw.lower() == "true"
            elif name in _INTEGER_FIELDS:
                if not re.fullmatch(r"[0-9]+", raw):
                    raise ValueError(f"{variable} must be a positive integer.")
                values[name] = int(raw)
            elif name in _DURATION_FIELDS:
                try:
                    values[name] = Decimal(raw)
                except InvalidOperation as exc:
                    raise ValueError(f"{variable} must be a positive, finite number.") from exc
            elif name == "channel_id":
                values[name] = raw or None
            else:
                values[name] = raw
        if "items" in values:
            raw_items = values["items"]
            if not isinstance(raw_items, list) or not raw_items:
                raise ValueError("market.items must be a non-empty array of item objects.")
            parsed_items = []
            item_fields = {field.name for field in fields(MarketItem)}
            for raw_item in raw_items:
                if not isinstance(raw_item, dict) or set(raw_item) - item_fields:
                    raise ValueError("market.items entries must be objects with known item fields.")
                if not {"type_id", "display_name"} <= set(raw_item):
                    raise ValueError("market.items entries require type_id and display_name.")
                parsed_items.append(MarketItem(**raw_item))
            values["items"] = tuple(parsed_items)
        return cls(**values)
