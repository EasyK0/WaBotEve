import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from tests.test_market import NOW, admin_interaction, market  # noqa: F401
from tests.test_market_esi import Response, client_with
from wabot.commands import CommandHandlers
from wabot.market_analysis import (
    BUTTON_PREFIX,
    AnalysisButton,
    analysis_embed,
    analysis_view,
    analyze,
    sparkline,
    summary_line,
)
from wabot.market_commands import UNAVAILABLE, UNKNOWN_ITEM, MarketCommands
from wabot.market_config import MarketSettings
from wabot.market_esi import ESIError, FetchedOrders, HistoryDay

TODAY = NOW.date()


def history(prices, *, volume=100, end=TODAY - timedelta(days=1)):
    start = end - timedelta(days=len(prices) - 1)
    return [
        HistoryDay(
            start + timedelta(days=index),
            Decimal(price),
            Decimal(price) + 10,
            Decimal(price) - 10,
            volume,
            5,
        )
        for index, price in enumerate(prices)
    ]


def run(prices, buy=None, sell=None, **kwargs):
    return analyze(history(prices, **kwargs), buy, sell, today=TODAY)


def order(price, *, buy, location=60003760, system=30000142):
    return {
        "order_id": int(price) * (2 if buy else 3),
        "type_id": 55831,
        "location_id": location,
        "system_id": system,
        "is_buy_order": buy,
        "price": Decimal(price),
        "volume_remain": 10,
    }


def test_rising_price_near_the_top_is_a_good_time_to_sell():
    result = run(range(100, 130), buy=Decimal(128), sell=Decimal(130))
    assert result.verdict == "good" and result.price_now == 129
    assert result.position == 100 and result.low30 == 100 and result.high30 == 129
    assert result.avg7 == 126 and result.trend > 5
    assert result.notes == ("price rising",)
    assert summary_line(result) == (
        "🟢 Good time to sell · 30d position 100% · trend +10.0% · price rising"
    )


def test_low_price_is_weak_and_falling_trend_is_noted():
    result = run(range(130, 100, -1), buy=Decimal(100))
    assert result.verdict == "weak" and result.position == 0
    assert result.notes == ("price falling",)
    assert result.price_now == 100  # Only the buy side exists.


def test_high_price_with_falling_trend_is_neutral_not_good():
    prices = [200] * 23 + [150] * 7
    result = run(prices, sell=Decimal(195))
    assert result.position == 90 and result.trend <= -2
    assert result.verdict == "neutral" and result.notes == ("price falling",)


def test_flat_market_and_no_orders_fall_back_to_last_average():
    result = run([50] * 10, volume=1)
    assert result.price_now == 50 and result.position == 50 and result.trend == 0
    assert result.verdict == "neutral" and result.notes == ("low volume",)
    assert result.volume30 == 1 and result.volume7 == 1


def test_short_or_stale_history_has_no_analysis():
    assert run([100] * 6) is None
    assert run([100] * 30, end=TODAY - timedelta(days=20)) is None
    assert analyze([], None, None, today=TODAY) is None


def test_window_is_thirty_days_and_volume_is_weighted():
    days = history([1000] * 30 + [10] * 30)
    assert analyze(days, None, None, today=TODAY).high30 == 10
    days = history([100, 100, 100, 100, 100, 100, 400])
    days[-1] = HistoryDay(days[-1].day, Decimal(400), Decimal(400), Decimal(400), 600, 1)
    result = analyze(days, None, None, today=TODAY)
    assert result.avg7 == result.avg30 == 250  # (6 * 100 * 100 + 400 * 600) / 1200


def test_net_prices_after_tax_and_broker_fee():
    result = analyze(
        history([100] * 10),
        Decimal(1000),
        Decimal(1200),
        today=TODAY,
        sales_tax=Decimal(4),
        broker_fee=Decimal(2),
    )
    assert result.instant_net == 960 and result.listing_net == 1128
    embed = analysis_embed("Item", result).to_dict()
    net = next(field for field in embed["fields"] if field["name"].startswith("Net"))
    assert "Listing gives **168 ISK** more per unit." in net["value"]


def test_best_weekday_needs_two_weeks():
    assert run([100] * 10).best_weekday is None
    prices = [100] * 28
    result = analyze(history(prices), None, None, today=TODAY)
    days = list(result.days)
    days[-1] = HistoryDay(days[-1].day, Decimal(500), Decimal(500), Decimal(500), 100, 1)
    weekday = days[-1].day.strftime("%A")
    assert analyze(days, None, None, today=TODAY).best_weekday == weekday


def test_sparkline_and_embed_layout():
    assert sparkline([Decimal(1), Decimal(2), Decimal(3)]) == "▁▅█"
    assert sparkline([Decimal(5)] * 3) == "▄▄▄"
    assert sparkline([]) == ""
    embed = analysis_embed("Veles <@1>", run(range(100, 130), buy=Decimal(128))).to_dict()
    assert embed["title"].startswith("🟢 Good time to sell · Veles ‹@1›")
    assert embed["color"] == 0x22C55E
    names = [field["name"] for field in embed["fields"]]
    assert names[:3] == ["Price now", "7d average", "30d average"]
    assert names[-1] == "Price, last 30 days"
    assert len(embed["fields"][-1]["value"]) == 32
    empty = analysis_embed("Item", None).to_dict()
    assert "fewer than 7 days" in empty["description"]


async def test_fetch_history_validates_and_sorts_days():
    payload = [
        {
            "date": "2026-09-19",
            "average": 5.5,
            "highest": 6,
            "lowest": 5,
            "volume": 10,
            "order_count": 2,
        },
        {
            "date": "2026-09-18",
            "average": 4.25,
            "highest": 5,
            "lowest": 4,
            "volume": 3,
            "order_count": 1,
        },
    ]
    client, session = client_with(Response(payload))
    days = await client.fetch_history(10000002, 52244)
    assert [day.day for day in days] == [date(2026, 9, 18), date(2026, 9, 19)]
    assert days[0].average == Decimal("4.25") and days[1].volume == 10
    call = session.get.call_args
    assert call.args[0].endswith("/markets/10000002/history/")
    assert call.kwargs["params"]["type_id"] == "52244"


@pytest.mark.parametrize(
    "raw",
    [
        '{"date": "2026-09-19"}',
        '[{"date": "yesterday", "average": 1, "highest": 1, "lowest": 1, "volume": 1, "order_count": 1}]',
        json.dumps(
            [
                {
                    "date": "2026-09-19",
                    "average": -1,
                    "highest": 1,
                    "lowest": 1,
                    "volume": 1,
                    "order_count": 1,
                }
            ]
        ),
        json.dumps(
            [
                {
                    "date": "2026-09-19",
                    "average": 1,
                    "highest": 1,
                    "lowest": 1,
                    "volume": 1.5,
                    "order_count": 1,
                }
            ]
        ),
    ],
)
async def test_invalid_history_is_unavailable(raw):
    client, _ = client_with(Response(raw=raw))
    with pytest.raises(ESIError):
        await client.fetch_history(10000002, 52244)


def test_tax_settings_are_validated():
    settings = MarketSettings.from_config({"sales_tax_percent": "4,5", "broker_fee_percent": 1})
    assert settings.sales_tax_percent == Decimal("4.5") and settings.broker_fee_percent == 1
    assert MarketSettings().sales_tax_percent == Decimal("3.375")
    for value in ("-1", "100", "abc", True):
        with pytest.raises(ValueError, match="Sales tax"):
            MarketSettings(sales_tax_percent=value)
    with pytest.raises(ValueError, match="together"):
        MarketSettings(sales_tax_percent=60, broker_fee_percent=40)


def with_history(esi, prices=range(100, 130)):
    esi.fetch_history = AsyncMock(return_value=history(prices))
    esi.fetch_orders.return_value = FetchedOrders(
        [order(128, buy=True), order(130, buy=False)], NOW
    )


async def test_report_has_verdict_line_and_analysis_buttons(market):  # noqa: F811
    monitor, channel, esi, _ = market
    with_history(esi)
    assert (await monitor.run_once(guild_id=1)).sent
    text = "\n".join(
        str(embed.to_dict())
        for call in channel.send.call_args_list
        for embed in call.kwargs["embeds"]
    )
    assert "🟢 Good time to sell · 30d position 100% · trend +10.0%" in text
    first, last = channel.send.call_args_list
    assert "view" not in first.kwargs
    view = last.kwargs["view"]
    assert view.timeout is None
    ids = [item.item.custom_id for item in view.children]
    assert ids == [f"{BUTTON_PREFIX}{item.type_id}" for item in monitor.config.enabled_items]
    assert view.children[0].item.label == "Triglavian Transconduit Datacaster"


async def test_history_failure_keeps_report_without_verdict(market):  # noqa: F811
    monitor, channel, esi, _ = market
    with_history(esi)
    esi.fetch_history.side_effect = ESIError("down")
    assert (await monitor.run_once(guild_id=1)).sent
    text = str([call.kwargs["embeds"][0].to_dict() for call in channel.send.call_args_list])
    assert "time to sell" not in text and "Neutral" not in text


def test_view_is_limited_to_discord_maximum():
    items = [SimpleNamespace(type_id=index, display_name="x" * 100) for index in range(1, 30)]
    view = analysis_view(items)
    assert len(view.children) == 25
    assert len(view.children[0].item.label) == 80
    assert analysis_view([]) is None


async def test_command_is_public_and_uses_cached_orders(market):  # noqa: F811
    monitor, _, esi, _ = market
    with_history(esi)
    await monitor.run_once()
    esi.fetch_orders.reset_mock()
    handlers = MarketCommands(monitor, CommandHandlers(None, monitor.settings))
    interaction = admin_interaction(administrator=False)
    await handlers.market_analysis(interaction, "veles entropic radiation sink")
    assert interaction.response.defer.call_args.kwargs.get("ephemeral") is not True
    embed = interaction.edit_original_response.call_args.kwargs["embed"].to_dict()
    assert embed["title"] == "🟢 Good time to sell · Veles Entropic Radiation Sink"
    esi.fetch_orders.assert_not_called()


async def test_command_overview_unknown_item_and_esi_failure(market):  # noqa: F811
    monitor, _, esi, _ = market
    with_history(esi)
    handlers = MarketCommands(monitor, CommandHandlers(None, monitor.settings))
    interaction = admin_interaction(administrator=False)
    await handlers.market_analysis(interaction)
    embed = interaction.edit_original_response.call_args.kwargs["embed"].to_dict()
    assert embed["description"].count("Good time to sell") == 6
    await handlers.market_analysis(interaction, "Plex")
    assert interaction.edit_original_response.call_args.kwargs["content"] == UNKNOWN_ITEM
    esi.fetch_history.side_effect = ESIError("down")
    await handlers.market_analysis(interaction, "52244")
    assert interaction.edit_original_response.call_args.kwargs["content"] == UNAVAILABLE


async def test_autocomplete_lists_watched_items(market):  # noqa: F811
    monitor, _, _, _ = market
    handlers = MarketCommands(monitor, CommandHandlers(None, monitor.settings))
    choices = await handlers.item_autocomplete(Mock(), "zorya")
    assert [choice.value for choice in choices] == ["52240", "52238", "52236"]


async def test_button_answers_privately(market):  # noqa: F811
    monitor, _, esi, _ = market
    with_history(esi)
    handlers = MarketCommands(monitor, CommandHandlers(None, monitor.settings))
    interaction = admin_interaction(administrator=False)
    interaction.client = SimpleNamespace(market_handlers=handlers)
    button = await AnalysisButton.from_custom_id(interaction, None, {"type_id": "52244"})
    await button.callback(interaction)
    assert interaction.response.defer.call_args.kwargs["ephemeral"] is True
    embed = interaction.edit_original_response.call_args.kwargs["embed"]
    assert "Veles" in embed.title


async def test_button_without_market_explains():
    interaction = Mock(spec=discord.Interaction)
    interaction.client = SimpleNamespace()
    interaction.response = Mock(send_message=AsyncMock())
    await AnalysisButton(1).callback(interaction)
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True
