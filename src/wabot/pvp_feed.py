"""Read zKillboard's R2Z2 live feed and resolve public EVE metadata."""

import asyncio
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import aiohttp

from wabot.market_esi import ESIClient, ESIError

FEED_URL = "https://r2z2.zkillboard.com/ephemeral/"
POCHVEN_REGION_ID = 10000070
EMPTY_POLL_SECONDS = 6
REQUEST_INTERVAL_SECONDS = 1 / 15


class FeedError(RuntimeError):
    pass


class ZkillFeed:
    def __init__(self):
        self._session = None
        self._next_request = 0.0

    async def close(self):
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _get(self, filename):
        # R2Z2 permits 15 requests/second and requires six seconds after a 404.
        # Waiting after each completed request also leaves room for network latency.
        delay = self._next_request - time.monotonic()
        if delay > EMPTY_POLL_SECONDS:
            raise FeedError("zKillboard rate-limit cooldown is active")
        if delay > 0:
            await asyncio.sleep(delay)
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={
                    "User-Agent": "Wabot-Pochven/1.6 (Discord killmail monitor)",
                    "Accept": "application/json",
                },
            )
        try:
            async with self._session.get(FEED_URL + filename) as response:
                if response.status == 404:
                    self._next_request = time.monotonic() + EMPTY_POLL_SECONDS
                    return None
                if response.status in (420, 429, 503):
                    retry = response.headers.get("Retry-After", "60")
                    try:
                        seconds = int(retry)
                    except ValueError:
                        try:
                            seconds = (
                                parsedate_to_datetime(retry) - datetime.now(UTC)
                            ).total_seconds()
                        except (ValueError, TypeError):
                            seconds = 60
                    self._next_request = time.monotonic() + max(60, seconds)
                    raise FeedError("zKillboard temporarily unavailable")
                if response.status != 200:
                    raise FeedError(f"zKillboard HTTP {response.status}")
                payload = await response.json()
                if not isinstance(payload, dict) or "error" in payload:
                    raise FeedError("Invalid zKillboard response")
                return payload
        finally:
            self._next_request = max(
                self._next_request, time.monotonic() + REQUEST_INTERVAL_SECONDS
            )

    async def latest_sequence(self):
        payload = await self._get("sequence.json")
        sequence = payload.get("sequence") if payload else None
        if type(sequence) is not int or sequence <= 0:
            raise FeedError("Invalid zKillboard sequence")
        return sequence

    async def packet(self, sequence):
        return await self._get(f"{sequence}.json")


class PvpESI(ESIClient):
    async def record(self, path):
        result = await self._request(path, {})
        if not isinstance(result.payload, dict):
            raise ESIError("Invalid EVE metadata")
        return result.payload

    async def pochven_systems(self):
        region = await self.record(f"/universe/regions/{POCHVEN_REGION_ID}/")
        systems = set()
        for constellation in region["constellations"]:
            data = await self.record(f"/universe/constellations/{constellation}/")
            systems.update(data["systems"])
        if not systems:
            raise ESIError("Pochven system list is empty")
        return systems

    async def name(self, kind, entity_id):
        paths = {
            "system": "universe/systems",
            "corporation": "corporations",
            "alliance": "alliances",
            "character": "characters",
            "type": "universe/types",
        }
        data = await self.record(f"/{paths[kind]}/{entity_id}/")
        return data["name"]
