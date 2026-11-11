"""Public, asynchronous ESI reads with complete-page and cache validation."""

import asyncio
import json
import logging
import math
import random
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

log = logging.getLogger(__name__)
BASE_URL = "https://esi.evetech.net/latest"
MAX_ATTEMPTS = 3
MAX_RETRY_WAIT = 30.0


class ESIError(RuntimeError):
    """The requested ESI snapshot could not be obtained safely."""


@dataclass(frozen=True, slots=True)
class FetchedOrders:
    orders: list[dict]
    fetched_at: datetime
    cached: bool = False


@dataclass(frozen=True, slots=True)
class HistoryDay:
    """One day of regional market history as published by ESI."""

    day: date
    average: Decimal
    highest: Decimal
    lowest: Decimal
    volume: int
    order_count: int


@dataclass(frozen=True, slots=True)
class _Response:
    payload: Any
    headers: dict[str, str]
    fetched_at: datetime
    expires_at: float
    cached: bool = False


def _http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = parsedate_to_datetime(value)
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        return None


def _seconds(value: str | None, default: float = 0.0) -> float:
    try:
        result = float(value)
        return max(0.0, result) if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def _expiry(headers: Mapping[str, str], now: datetime) -> float:
    control = headers.get("cache-control", "").lower()
    if "no-store" in control or "no-cache" in control:
        return time.monotonic()
    max_age = re.search(r"(?:^|,)\s*max-age\s*=\s*\"?(\d+)", control)
    age = _seconds(headers.get("age"))
    date = _http_date(headers.get("date"))
    age = max(age, max(0, (now - date).total_seconds()) if date else 0)
    if max_age:
        seconds = max(0, int(max_age.group(1)) - age)
    else:
        expires = _http_date(headers.get("expires"))
        seconds = max(0, (expires - now).total_seconds()) if expires else 0
    return time.monotonic() + seconds


class ESIClient:
    def __init__(self, timeout_seconds: float = 20):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("ESI timeout must be positive and finite")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[tuple, _Response] = {}
        self._cooldown_until = 0.0
        self._request_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _check_cooldown(self) -> None:
        remaining = self._cooldown_until - time.monotonic()
        if remaining > 0:
            raise ESIError(f"ESI cooldown: retry in {math.ceil(remaining)} seconds")

    def _observe_limits(self, headers: Mapping[str, str]) -> bool:
        """Stop the current cycle when the shared error budget is nearly exhausted."""
        remain = headers.get("x-esi-error-limit-remain")
        if remain is not None and _seconds(remain, 100) <= 10:
            reset = max(1.0, _seconds(headers.get("x-esi-error-limit-reset"), 60))
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + reset)
            return True
        # Newer ESI routes use a token bucket, and a successful request costs two tokens.
        remain = headers.get("x-ratelimit-remaining")
        if remain is not None and _seconds(remain, 100) < 2:
            window = re.search(r"/(\d+)([smh])", headers.get("x-ratelimit-limit", ""))
            reset = int(window[1]) * {"s": 1, "m": 60, "h": 3600}[window[2]] if window else 60
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + reset)
            return True
        return False

    async def _request(
        self, path: str, params: dict[str, str], *, type_id: int | None = None
    ) -> _Response:
        # Serialize requests so one response's limits apply before the next request begins.
        async with self._request_lock:
            return await self._request_locked(path, params, type_id=type_id)

    async def _request_locked(
        self, path: str, params: dict[str, str], *, type_id: int | None
    ) -> _Response:
        key = (path, tuple(sorted(params.items())))
        previous = self._cache.get(key)
        self._check_cooldown()
        if previous is not None and previous.expires_at > time.monotonic():
            return replace(previous, cached=True)
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": "wabot-market/1.0", "Accept": "application/json"},
            )
        request_headers = {}
        if previous is not None and previous.headers.get("etag"):
            request_headers["If-None-Match"] = previous.headers["etag"]
        waited = 0.0
        for attempt in range(MAX_ATTEMPTS):
            self._check_cooldown()
            started = time.monotonic()
            headers: dict[str, str] = {}
            low_budget = False
            status = None
            try:
                async with self._session.get(
                    BASE_URL + path, params=params, headers=request_headers, allow_redirects=False
                ) as response:
                    status = response.status
                    headers = {key.lower(): value for key, value in response.headers.items()}
                    low_budget = self._observe_limits(headers)
                    log.info(
                        "ESI type_id=%s HTTP=%s elapsed=%.3fs",
                        type_id,
                        status,
                        time.monotonic() - started,
                    )
                    now = datetime.now(UTC)
                    if status == 304:
                        if previous is None:
                            raise ESIError("ESI returned 304 without a cached response")
                        merged = previous.headers | headers
                        result = _Response(
                            previous.payload, merged, now, _expiry(merged, now), True
                        )
                        self._cache[key] = result
                        return result
                    if status == 200:
                        try:
                            payload = json.loads(await response.text(), parse_float=Decimal)
                        except (ValueError, UnicodeError) as exc:
                            raise ESIError("ESI returned invalid JSON") from exc
                        result = _Response(payload, headers, now, _expiry(headers, now))
                        if "no-store" not in headers.get("cache-control", "").lower():
                            self._cache[key] = result
                        else:
                            self._cache.pop(key, None)
                        return result
                    if status not in (420, 429) and not 500 <= status <= 599:
                        raise ESIError(f"ESI HTTP {status}")
            except (aiohttp.ClientError, TimeoutError) as exc:
                log.warning(
                    "ESI type_id=%s transport=%s elapsed=%.3fs",
                    type_id,
                    type(exc).__name__,
                    time.monotonic() - started,
                )
                if attempt + 1 == MAX_ATTEMPTS:
                    raise ESIError("ESI network request failed") from exc
            delay = 2**attempt + random.uniform(0, 0.5)
            retry_after = headers.get("retry-after")
            if retry_after:
                retry_date = _http_date(retry_after)
                retry_seconds = (
                    max(0.0, (retry_date - datetime.now(UTC)).total_seconds())
                    if retry_date
                    else _seconds(retry_after)
                )
                delay = max(delay, retry_seconds)
            if status in (420, 429) or retry_after:
                self._cooldown_until = max(self._cooldown_until, time.monotonic() + delay)
            if low_budget or attempt + 1 == MAX_ATTEMPTS or waited + delay > MAX_RETRY_WAIT:
                raise ESIError(f"ESI temporarily unavailable (HTTP {status})")
            log.warning("ESI type_id=%s HTTP=%s retry_in=%.2fs", type_id, status, delay)
            await asyncio.sleep(delay)
            waited += delay
        raise ESIError("ESI request failed")  # Defensive: all attempts above return or raise.

    @staticmethod
    def _page_count(response: _Response) -> int:
        try:
            pages = int(response.headers.get("x-pages", "1"))
        except ValueError as exc:
            raise ESIError("ESI returned invalid X-Pages") from exc
        if not 1 <= pages <= 10000:
            raise ESIError("ESI returned invalid X-Pages")
        return pages

    @staticmethod
    def _orders(payload: Any, type_id: int) -> list[dict]:
        if not isinstance(payload, list):
            raise ESIError("ESI market response is not a list")
        for order in payload:
            if not isinstance(order, dict):
                raise ESIError("ESI market response contains an invalid order")
            for field in ("order_id", "type_id", "location_id", "system_id", "volume_remain"):
                value = order.get(field)
                if type(value) is not int or value < (0 if field == "volume_remain" else 1):
                    raise ESIError(f"ESI market order has invalid {field}")
            price = order.get("price")
            if (
                not isinstance(price, (int, Decimal))
                or isinstance(price, bool)
                or not Decimal(price).is_finite()
                or price < 0
                or type(order.get("is_buy_order")) is not bool
                or order["type_id"] != type_id
            ):
                raise ESIError("ESI market order has invalid price, side or type_id")
        return payload

    async def fetch_orders(self, region_id: int, type_id: int) -> FetchedOrders:
        started = time.monotonic()
        path = f"/markets/{region_id}/orders/"
        params = {
            "datasource": "tranquility",
            "order_type": "all",
            "type_id": str(type_id),
            "page": "1",
        }
        first = await self._request(path, params, type_id=type_id)
        pages = self._page_count(first)
        responses = [first]
        orders = list(self._orders(first.payload, type_id))
        seen = {order["order_id"] for order in orders}
        if len(seen) != len(orders):
            raise ESIError("ESI pagination contains duplicate orders")
        for page in range(2, pages + 1):
            response = await self._request(path, params | {"page": str(page)}, type_id=type_id)
            if self._page_count(response) != pages:
                raise ESIError("ESI pagination changed page count during retrieval")
            if response.headers.get("last-modified") != first.headers.get("last-modified"):
                raise ESIError("ESI pagination changed snapshot during retrieval")
            # Without Last-Modified, crossing page one's expiry cannot be verified safely.
            if not first.headers.get("last-modified") and first.expires_at < time.monotonic():
                raise ESIError("ESI pagination snapshot expired during retrieval")
            page_orders = self._orders(response.payload, type_id)
            for order in page_orders:
                if order["order_id"] in seen:
                    raise ESIError("ESI pagination contains duplicate orders")
                seen.add(order["order_id"])
            orders.extend(page_orders)
            responses.append(response)
        log.info(
            "ESI type_id=%s pages=%s orders=%s elapsed=%.3fs",
            type_id,
            pages,
            len(orders),
            time.monotonic() - started,
        )
        return FetchedOrders(
            orders,
            min(response.fetched_at for response in responses),
            any(response.cached for response in responses),
        )

    @staticmethod
    def _history(payload: Any) -> list[HistoryDay]:
        if not isinstance(payload, list):
            raise ESIError("ESI market history is not a list")
        days = {}
        for entry in payload:
            if not isinstance(entry, dict):
                raise ESIError("ESI market history contains an invalid day")
            try:
                day = date.fromisoformat(entry["date"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ESIError("ESI market history has an invalid date") from exc
            prices = [entry.get(field) for field in ("average", "highest", "lowest")]
            counts = [entry.get(field) for field in ("volume", "order_count")]
            if any(
                isinstance(price, bool)
                or not isinstance(price, (int, Decimal))
                or not Decimal(price).is_finite()
                or price < 0
                for price in prices
            ) or any(type(count) is not int or count < 0 for count in counts):
                raise ESIError("ESI market history has an invalid price or volume")
            days[day] = HistoryDay(day, *(Decimal(price) for price in prices), *counts)
        return [days[day] for day in sorted(days)]

    async def fetch_history(self, region_id: int, type_id: int) -> list[HistoryDay]:
        """Daily history, oldest first; ESI publishes it once a day after downtime."""
        response = await self._request(
            f"/markets/{region_id}/history/",
            {"datasource": "tranquility", "type_id": str(type_id)},
            type_id=type_id,
        )
        return self._history(response.payload)

    async def validate_items(self, items: Iterable) -> dict[int, str]:
        errors = {}
        for item in items:
            enabled = item.get("enabled", True) if isinstance(item, Mapping) else item.enabled
            if not enabled:
                continue
            type_id = item["type_id"] if isinstance(item, Mapping) else item.type_id
            name = item["display_name"] if isinstance(item, Mapping) else item.display_name
            try:
                response = await self._request(
                    f"/universe/types/{type_id}/", {"language": "en"}, type_id=type_id
                )
                if not isinstance(response.payload, dict) or response.payload.get("name") != name:
                    errors[type_id] = (
                        "Configured name does not exactly match the official EVE type name"
                    )
            except ESIError as exc:
                errors[type_id] = str(exc)
        return errors

    async def location_name(self, location_id: int) -> str:
        # Player structures need SSO for their name endpoint; this client is public-only.
        if location_id >= 1_000_000_000_000:
            return f"Structure {location_id}"
        try:
            response = await self._request(f"/universe/stations/{location_id}/", {})
            if isinstance(response.payload, dict) and isinstance(response.payload.get("name"), str):
                return response.payload["name"]
        except ESIError:
            log.warning("ESI station name unavailable location_id=%s", location_id)
        return f"Station {location_id}"
