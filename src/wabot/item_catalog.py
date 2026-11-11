"""Searchable published EVE types from CCP's official Static Data Export."""

from __future__ import annotations

import copy
import gzip
import json
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from wabot.desktop_support import atomic_write
from wabot.market_config import MarketSettings

SDE_URL = "https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip"
BUNDLED_CATALOG = Path(__file__).with_name("assets") / "eve-items.json.gz"
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CatalogItem:
    type_id: int
    name: str
    marketable: bool


class ItemCatalog:
    def __init__(self, items: list[CatalogItem], updated_at: str):
        if not items or len({item.type_id for item in items}) != len(items):
            raise ValueError("Invalid EVE item catalog.")
        for item in items:
            if (
                type(item.type_id) is not int
                or item.type_id <= 0
                or not isinstance(item.name, str)
                or not item.name.strip()
                or type(item.marketable) is not bool
            ):
                raise ValueError("Invalid item in the EVE catalog.")
        self.items = sorted(items, key=lambda item: (item.name.casefold(), item.type_id))
        self.updated_at = updated_at
        self.by_id = {item.type_id: item for item in self.items}
        self._search = [(item, item.name.casefold()) for item in self.items]

    def search(self, query: str, limit: int = 200) -> tuple[list[CatalogItem], int]:
        words = query.casefold().split()
        matches = [
            item
            for item, name in self._search
            if all(word in name or word == str(item.type_id) for word in words)
        ]
        return matches[:limit], len(matches)

    def save(self, path: Path) -> None:
        payload = {
            "updated_at": self.updated_at,
            "items": [[item.type_id, item.name, item.marketable] for item in self.items],
        }
        atomic_write(
            path, gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), mtime=0)
        )

    @classmethod
    def read(cls, path: Path) -> ItemCatalog:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        return cls([CatalogItem(*row) for row in payload["items"]], payload["updated_at"])

    @classmethod
    def load(cls, cache: Path) -> ItemCatalog:
        for path in (cache, BUNDLED_CATALOG):
            try:
                return cls.read(path)
            except (OSError, EOFError, ValueError, KeyError, TypeError):
                continue
        raise ValueError("No EVE catalog yet. Click “Update catalog” to download it.")

    @classmethod
    def from_sde(cls, archive: zipfile.ZipFile) -> ItemCatalog:
        items = []
        with archive.open("types.jsonl") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("published") is True:
                    items.append(
                        CatalogItem(
                            row["_key"], row["name"]["en"], row.get("marketGroupID") is not None
                        )
                    )
        return cls(items, datetime.now(UTC).isoformat(timespec="seconds"))

    @classmethod
    def download(cls, cache: Path, progress=lambda message: None) -> ItemCatalog:
        request = urllib.request.Request(SDE_URL, headers={"User-Agent": "Wabot-item-catalog/1.0"})
        # Stream to disk, then read just types.jsonl; never extract the whole archive.
        with tempfile.TemporaryFile() as temporary:
            with urllib.request.urlopen(request, timeout=30) as response:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Katalog EVE przekracza dopuszczalny rozmiar.")
                    temporary.write(chunk)
                    progress(f"Pobieranie katalogu CCP: {total // (1024 * 1024)} MB…")
            temporary.seek(0)
            progress("Reading EVE items…")
            with zipfile.ZipFile(temporary) as archive:
                catalog = cls.from_sde(archive)
        catalog.save(cache)
        return catalog


def watchlist(config: dict) -> list[dict]:
    settings = MarketSettings.from_config(config.get("market"), environ={})
    return [
        dict(
            type_id=item.type_id,
            display_name=item.display_name,
            enabled=item.enabled,
            **(
                {"min_buy_price": str(item.min_buy_price)} if item.min_buy_price is not None else {}
            ),
        )
        for item in settings.items
    ]


def change_watchlist(config: dict, type_id: int, *, item: CatalogItem | None = None) -> dict:
    """Add/re-enable an item, or remove it; retain existing thresholds and settings."""
    updated = copy.deepcopy(config)
    items = watchlist(config)
    if item is not None:
        if not item.marketable or item.type_id != type_id:
            raise ValueError("This item is not available on the EVE market.")
        existing = next((entry for entry in items if entry["type_id"] == type_id), None)
        if existing:
            existing.update(enabled=True, display_name=item.name)
        else:
            items.append({"type_id": item.type_id, "display_name": item.name, "enabled": True})
    else:
        items = [entry for entry in items if entry["type_id"] != type_id]
        if not items:
            raise ValueError("Keep at least one item on the watch list.")
        if updated.get("market", {}).get("enabled") and not any(
            entry["enabled"] for entry in items
        ):
            raise ValueError("Reports need at least one enabled item.")
    updated.setdefault("market", {})["items"] = items
    MarketSettings.from_config(updated["market"], environ={})
    return updated
