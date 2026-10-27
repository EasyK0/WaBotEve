from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from wabot.market_config import DATACASTER_NAME, DATACASTER_TYPE_ID, MarketItem, MarketSettings
from wabot.market_report import (
    OTHER_ITEMS_COLOUR,
    STATUS_COLOURS,
    ItemSnapshot,
    analyze_orders,
    build_report,
    group_orders,
    snapshot_state,
)

NOW = datetime(2026, 9, 20, 12, 30, tzinfo=UTC)
DATACASTER = MarketItem(DATACASTER_TYPE_ID, DATACASTER_NAME)
THRESHOLD = Decimal("500000")
JITA = 60003760
PERIMETER = 30000144


def order(price, units, *, buy=True, location=JITA, system=30000142):
    return {
        "price": Decimal(str(price)),
        "volume_remain": units,
        "volume_total": units + 100_000,
        "is_buy_order": buy,
        "location_id": location,
        "system_id": system,
    }


def report_text(batches):
    return "\n".join(
        str(value)
        for batch in batches
        for embed in batch
        for value in [
            embed.title,
            embed.description,
            embed.footer.text,
            *(part for field in embed.fields for part in (field.name, field.value)),
        ]
        if value
    )


def test_document_example_uses_remaining_units_and_exact_decimal_value():
    result = analyze_orders(
        [
            order("540000", 2000),
            order("510000", 5000),
            order("500000", 3000),
            order("495000", 8000),
        ],
        THRESHOLD,
    )
    assert result.safe_units == 10_000
    assert result.safe_gross_isk == Decimal("5130000000")
    assert result.safe_avg_price == Decimal("513000")
    assert result.next_below == Decimal("495000")
    assert result.best_buy_volume == 2000
    assert result.status == "SAFE"


@pytest.mark.parametrize(
    ("price", "units", "status", "safe"),
    [
        ("499999.99", 10_000, "STOP", 0),
        ("499999.99999999999", 10_000, "STOP", 0),
        ("500000", 5000, "SAFE", 5000),
        ("500000", 4999, "LOW DEPTH", 4999),
        ("500000.01", 1, "LOW DEPTH", 1),
        ("500000", 0, "STOP", 0),
    ],
)
def test_threshold_and_low_depth_boundaries(price, units, status, safe):
    result = analyze_orders([order(price, units)], THRESHOLD)
    assert result.status == status
    assert result.safe_units == safe


def test_duplicate_best_prices_and_spread():
    result = analyze_orders(
        [
            order("600000.01", 3),
            order("600000.01", 7),
            order("500000", 8),
            order("660000.011", 2, buy=False),
            order("660000.011", 9, buy=False),
            order("700000", 5, buy=False),
        ],
        THRESHOLD,
    )
    assert result.best_buy == Decimal("600000.01")
    assert result.best_buy_volume == 10
    assert result.best_sell == Decimal("660000.011")
    assert result.best_sell_volume == 11
    assert result.spread_isk == Decimal("60000.001")
    assert result.spread_percent == Decimal("10")
    assert result.safe_gross_isk == Decimal("10000000.10")


def test_missing_sides_have_no_price_and_do_not_use_zero_as_a_quote():
    empty = analyze_orders([], THRESHOLD)
    assert empty.best_buy is empty.best_sell is empty.spread_isk is empty.spread_percent is None
    assert empty.safe_units == 0
    assert empty.safe_avg_price is empty.next_below is None
    assert empty.status == "STOP"
    buy_only = analyze_orders([order("500000", 5000)], THRESHOLD)
    assert buy_only.best_sell is buy_only.spread_isk is None
    assert buy_only.status == "SAFE"
    sell_only = analyze_orders([order("550000", 5000, buy=False)], THRESHOLD)
    assert sell_only.best_buy is None
    assert sell_only.status == "STOP"


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-1", "not a price"])
def test_malformed_prices_cannot_produce_trade_status(price):
    row = order(1, 1)
    row["price"] = price
    with pytest.raises(ValueError):
        analyze_orders([row], THRESHOLD)


def test_scope_filter_preserves_locations_and_excludes_other_forge_orders():
    groups = group_orders(
        [
            order("510000", 5),
            order("520000", 7, location=100000001, system=PERIMETER),
            order("530000", 11, location=100000002, system=PERIMETER),
            order("9999999", 1_000_000, location=60003761, system=30000142),
            order("9999999", 1_000_000, location=60003762, system=30000000),
        ],
        MarketSettings(items=(DATACASTER,)),
    )
    assert set(groups) == {JITA, 100000001, 100000002}
    assert [analyze_orders(rows, THRESHOLD).safe_units for rows in groups.values()] == [5, 7, 11]


def test_combined_report_aggregates_market_and_shows_transport_and_local_depth():
    settings = MarketSettings(items=(DATACASTER,), datacaster_low_depth_units=1)
    rows = [
        order("540000", 2000),
        order("510000", 5000, location=100000001, system=PERIMETER),
        order("500000", 3000, location=100000002, system=PERIMETER),
        order("495000", 8000),
        order("570000", 20, buy=False, location=100000001, system=PERIMETER),
        order("9999999", 100_000, location=60003761),
    ]
    batches = build_report(
        settings, [ItemSnapshot(DATACASTER, rows, NOW)], {100000001: "Perimeter Hub"}, NOW
    )
    text = report_text(batches)
    assert len(batches) == len(batches[0]) == 1
    assert batches[0][0].title == "🟢 Jita and Perimeter Market Report • Datacasters"
    assert batches[0][0].colour.value == STATUS_COLOURS["SAFE"]
    assert "Across locations safe depth: **10,000 units**" in text
    assert "Gross before tax: 5,130,000,000 ISK" in text
    assert "Average: 513,000 ISK" in text
    assert "At: Jita 4-4" in text
    assert "**5,000 units** at Perimeter Hub" in text
    assert "Safe: 2,000 units • Best buy: 540,000 ISK" in text
    assert "Safe: 5,000 units • Best buy: 510,000 ISK" in text
    assert "Structure 100000002" in text
    assert "transporting items may be necessary" in text
    assert "9,999,999" not in text
    assert "2026-09-20 12:30:00 EVE Time (UTC)" in text
    assert "60 minutes" in text


def test_combined_top_price_volume_includes_ties_across_locations():
    settings = MarketSettings(items=(DATACASTER,))
    rows = [
        order("500000", 2500),
        order("500000", 3000, location=1234, system=PERIMETER),
    ]
    text = report_text(
        build_report(settings, [ItemSnapshot(DATACASTER, rows, NOW)], {1234: "Hub"}, NOW)
    )
    assert "Best buy: 500,000 ISK • 5,500 units at best price" in text
    assert "Best buy locations: Hub, Jita 4-4" in text
    assert "Best sell: N/A" in text
    assert "Best sell: 0 ISK" not in text


def test_new_structure_discovered_from_another_item_is_in_datacaster_breakdown():
    extra = MarketItem(1000, "Extra item")
    settings = MarketSettings(items=(DATACASTER, extra))
    batches = build_report(
        settings,
        [
            ItemSnapshot(DATACASTER, [order("500000", 1)], NOW),
            ItemSnapshot(extra, [order("42", 2, location=987654, system=PERIMETER)], NOW),
        ],
        {},
        NOW,
    )
    text = report_text(batches)
    assert "**Structure 987654** • location_id 987654\nSafe: 0 units • Best buy: N/A • STOP" in text
    assert "Extra item" in text
    assert "Best buy: 42 ISK • 2 units" in text
    assert len(batches) == 2
    assert "Extra item" not in report_text(batches[:1])
    assert "Datacaster" not in report_text(batches[1:])
    assert batches[1][0].title == "🟣 Jita and Perimeter Market Report • Other items"
    assert all(embed.timestamp == NOW for batch in batches for embed in batch)


@pytest.mark.parametrize(
    ("age", "declared", "expected"),
    [
        (0, "Live", "Live"),
        (20, "Cached", "Cached"),
        (30, "Cached", "Cached"),
        (30.01, "Cached", "Error"),
        (31, "Live", "Error"),
        (0, "Error", "Error"),
    ],
)
def test_freshness_boundaries(age, declared, expected):
    snapshot = ItemSnapshot(DATACASTER, [], NOW - timedelta(minutes=age), declared)
    assert snapshot_state(snapshot, NOW) == expected


def test_cached_report_displays_age_and_expired_data_never_has_safe_recommendation():
    settings = MarketSettings(items=(DATACASTER,))
    rows = [order("500000", 9000)]
    cached = build_report(
        settings, [ItemSnapshot(DATACASTER, rows, NOW - timedelta(minutes=20), "Cached")], {}, NOW
    )
    assert "Cached • data age 20m 0s" in report_text(cached)
    assert cached[0][0].footer.text == "Data source ESI • Cached"
    expired = build_report(
        settings, [ItemSnapshot(DATACASTER, rows, NOW - timedelta(minutes=31), "Live")], {}, NOW
    )
    text = report_text(expired)
    assert "DATA ERROR" in text
    assert "Error • last successful read 31m 0s ago" in text
    assert "safe instant sell" not in text
    assert "9,000" not in text
    assert "**SAFE**" not in text
    assert expired[0][0].colour.value == STATUS_COLOURS["DATA ERROR"]


def test_partial_item_error_retains_other_quotes_and_marks_footer_error():
    extra = MarketItem(1000, "Extra item")
    settings = MarketSettings(items=(DATACASTER, extra))
    batches = build_report(
        settings,
        [
            ItemSnapshot(DATACASTER, [], None, "Error"),
            ItemSnapshot(extra, [order("123456.78", 6)], NOW),
        ],
        {},
        NOW,
    )
    text = report_text(batches)
    assert "Data unavailable" in text
    assert "Best buy: 123,456.78 ISK • 6 units" in text
    assert batches[0][0].footer.text == "Data source ESI • Error"
    assert batches[1][0].footer.text == "Data source ESI • Live"
    assert batches[1][0].colour.value == OTHER_ITEMS_COLOUR
    assert "Data unavailable" not in report_text(batches[1:])


def test_malformed_item_is_unavailable_instead_of_breaking_whole_report():
    extra = MarketItem(1000, "Extra item")
    settings = MarketSettings(items=(DATACASTER, extra))
    bad_order = order("500000", 10)
    bad_order.pop("volume_remain")
    text = report_text(
        build_report(
            settings,
            [
                ItemSnapshot(DATACASTER, [bad_order], NOW),
                ItemSnapshot(extra, [order("99", 10)], NOW),
            ],
            {},
            NOW,
        )
    )
    assert "DATA ERROR" in text
    assert "Best buy: 99 ISK" in text


def test_extra_threshold_missing_snapshot_and_disabled_items():
    extra = MarketItem(1000, "Threshold item", min_buy_price=Decimal("100"))
    missing = MarketItem(1001, "Missing item")
    disabled = MarketItem(1002, "Disabled item", enabled=False)
    settings = MarketSettings(items=(DATACASTER, extra, missing, disabled))
    text = report_text(
        build_report(
            settings,
            [
                ItemSnapshot(DATACASTER, [], NOW),
                ItemSnapshot(extra, [order("100", 5000), order("99", 10000)], NOW),
                ItemSnapshot(disabled, [order("10000000", 10000)], NOW),
            ],
            {},
            NOW,
        )
    )
    assert "safe instant sell at ≥ 100 ISK: **5,000 units**" in text
    assert "Missing item\nData unavailable" in text
    assert "Disabled item" not in text


def test_many_items_locations_and_hostile_names_stay_within_discord_limits():
    extra_items = tuple(
        MarketItem(1000 + index, f"Item {index} " + "🛰" * 400) for index in range(60)
    )
    settings = MarketSettings(items=(DATACASTER, *extra_items))
    rows = [
        order("500000", 100, location=100000000 + index, system=PERIMETER) for index in range(160)
    ]
    snapshots = [ItemSnapshot(DATACASTER, rows, NOW)]
    snapshots.extend(ItemSnapshot(item, [order("123456.78", 15)], NOW) for item in extra_items)
    names = {
        100000000 + index: f"Hub {index} @everyone <@123> " + "🛰" * 400 for index in range(160)
    }
    batches = build_report(settings, snapshots, names, NOW)
    assert len(batches) > 1
    text = report_text(batches)
    assert "@everyone" not in text
    assert "<@123>" not in text
    for index in range(160):
        assert f"location_id {100000000 + index}" in text
    for index in range(60):
        assert f"Item {index} " in text

    def length(value):
        return len((value or "").encode("utf-16-le")) // 2

    for batch in batches:
        assert 1 <= len(batch) <= 10
        datacaster_batch = "Datacasters" in batch[0].title
        assert all(("Datacasters" in embed.title) == datacaster_batch for embed in batch)
        total = 0
        for embed in batch:
            assert len(embed.fields) <= 25
            assert length(embed.title) <= 256
            assert length(embed.description) <= 4096
            assert length(embed.footer.text) <= 2048
            assert embed.timestamp == NOW
            total += length(embed.title) + length(embed.description) + length(embed.footer.text)
            for field in embed.fields:
                assert 0 < length(field.name) <= 256
                assert 0 < length(field.value) <= 1024
                total += length(field.name) + length(field.value)
        assert total <= 6000
