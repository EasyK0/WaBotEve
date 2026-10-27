import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from wabot.timekeeping import EXPIRED_TIME, INVALID_TIME, eve_time, resolve_payout


@pytest.mark.parametrize(
    "value,now,payout,due",
    [
        ("20:20", "2026-09-18T20:20:00", "2026-09-18T20:20:00", "2026-09-18T20:43:00"),
        (" 20:20 ", "2026-09-18T20:25:45", "2026-09-18T20:20:00", "2026-09-18T20:43:00"),
        ("23:50", "2026-09-19T00:02:00", "2026-09-18T23:50:00", "2026-09-19T00:13:00"),
        ("23:50", "2027-01-01T00:02:00", "2026-12-31T23:50:00", "2027-01-01T00:13:00"),
        ("20:20", "2026-09-18T20:42:59.999999", "2026-09-18T20:20:00", "2026-09-18T20:43:00"),
    ],
)
def test_last_utc_occurrence(value, now, payout, due):
    def stamp(text):
        return datetime.fromisoformat(text).replace(tzinfo=UTC)

    assert resolve_payout(value, stamp(now)) == (
        int(stamp(payout).timestamp()),
        int(stamp(due).timestamp()),
    )


@pytest.mark.parametrize(
    "value", ["24:00", "9:05", "09:5", "20:60", "text", "in 5m", "", "２０:２０", "20:20:00"]
)
def test_invalid_input(value):
    with pytest.raises(ValueError, match=re.escape(INVALID_TIME)):
        resolve_payout(value, datetime(2026, 9, 18, 20, 25, tzinfo=UTC))


@pytest.mark.parametrize("value", ["20:10", "20:34", "00:00"])
def test_expired_or_future_clock_time_is_rejected(value):
    with pytest.raises(ValueError, match=EXPIRED_TIME):
        resolve_payout(value, datetime(2026, 9, 18, 20, 33, tzinfo=UTC))


def test_utc_ignores_host_timezone():
    payout, due = resolve_payout(
        "20:20", datetime(2026, 9, 18, 22, 25, tzinfo=timezone(timedelta(hours=2)))
    )
    assert eve_time(payout) == "2026-09-18 20:20 EVE Time (UTC)"
    assert eve_time(due) == "2026-09-18 20:43 EVE Time (UTC)"
