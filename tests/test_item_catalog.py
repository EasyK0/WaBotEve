import io
import json
import zipfile
from decimal import Decimal

import pytest

from wabot.item_catalog import CatalogItem, ItemCatalog, change_watchlist, watchlist
from wabot.market_config import MarketSettings


def sde_bytes(rows):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("types.jsonl", "\n".join(json.dumps(row) for row in rows))
    return output.getvalue()


def test_sde_search_includes_published_nonmarket_types_and_uses_english_names():
    raw = sde_bytes(
        [
            {
                "_key": 34,
                "name": {"en": "Tritanium", "de": "Other"},
                "published": True,
                "marketGroupID": 18,
            },
            {"_key": 35, "name": {"en": "Quest Item"}, "published": True},
            {"_key": 36, "name": {"en": "Hidden Item"}, "published": False},
        ]
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        catalog = ItemCatalog.from_sde(archive)
    assert len(catalog.items) == 2
    assert catalog.search("  tRiTaN  ")[0] == [CatalogItem(34, "Tritanium", True)]
    assert catalog.search("34")[0][0].name == "Tritanium"
    assert catalog.search("item quest")[0] == [CatalogItem(35, "Quest Item", False)]
    assert catalog.search("absent") == ([], 0)
    assert len(catalog.search("", limit=1)[0]) == 1
    assert catalog.search("", limit=1)[1] == 2


def test_catalog_cache_roundtrip_and_corrupt_cache_falls_back_to_bundle(tmp_path):
    cache = tmp_path / "catalog.gz"
    catalog = ItemCatalog([CatalogItem(34, "Tritanium", True)], "2026-09-21")
    catalog.save(cache)
    assert ItemCatalog.load(cache).items == catalog.items
    cache.write_bytes(b"broken")
    bundled = ItemCatalog.load(cache)
    assert len(bundled.items) > 19000
    assert bundled.by_id[34] == CatalogItem(34, "Tritanium", True)


def test_refresh_download_is_atomic_and_preserves_old_cache_on_invalid_data(tmp_path, monkeypatch):
    cache = tmp_path / "catalog.gz"
    ItemCatalog([CatalogItem(34, "Tritanium", True)], "old").save(cache)
    original = cache.read_bytes()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(b"invalid ZIP"))
    with pytest.raises(zipfile.BadZipFile):
        ItemCatalog.download(cache)
    assert cache.read_bytes() == original
    raw = sde_bytes(
        [{"_key": 35, "name": {"en": "Pyerite"}, "published": True, "marketGroupID": 18}]
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(raw))
    catalog = ItemCatalog.download(cache)
    assert catalog.by_id[35].name == "Pyerite"
    assert ItemCatalog.load(cache).items == catalog.items


def test_adding_item_materializes_defaults_and_preserves_market_settings():
    config = {
        "guild_ids": ["123"],
        "market": {
            "enabled": True,
            "channel_id": "456",
            "interval_minutes": 75,
            "datacaster_min_buy_price": "600000",
        },
    }
    result = change_watchlist(config, 34, item=CatalogItem(34, "Tritanium", True))
    assert "items" not in config["market"]
    settings = MarketSettings.from_config(result["market"], environ={})
    assert len(settings.items) == 7
    assert settings.items[-1].display_name == "Tritanium"
    assert settings.threshold_for(settings.items[0]) == Decimal("600000")
    assert settings.interval_minutes == 75 and settings.channel_id == "456"


def test_readd_reenables_without_duplicates_or_losing_price_threshold():
    config = {
        "market": {
            "items": [
                {
                    "type_id": 34,
                    "display_name": "Tritanium",
                    "enabled": False,
                    "min_buy_price": "5.25",
                }
            ]
        }
    }
    result = change_watchlist(config, 34, item=CatalogItem(34, "Tritanium", True))
    result = change_watchlist(result, 34, item=CatalogItem(34, "Tritanium", True))
    assert watchlist(result) == [
        {"type_id": 34, "display_name": "Tritanium", "enabled": True, "min_buy_price": "5.25"}
    ]


def test_remove_and_nonmarket_validation():
    config = change_watchlist({}, 34, item=CatalogItem(34, "Tritanium", True))
    assert len(watchlist(change_watchlist(config, 34))) == 6
    with pytest.raises(ValueError, match="EVE market"):
        change_watchlist(config, 35, item=CatalogItem(35, "Quest Item", False))
    only = {"market": {"items": [{"type_id": 34, "display_name": "Tritanium"}]}}
    with pytest.raises(ValueError, match="at least one"):
        change_watchlist(only, 34)
