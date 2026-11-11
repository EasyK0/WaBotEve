import time
from decimal import Decimal

from tests.test_desktop_pvp_messages import launcher  # noqa: F401
from tests.test_market_analysis import NOW, history
from wabot.desktop_support import read_config


def wait(app, panel):
    deadline = time.monotonic() + 5
    while panel.loading and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.01)
    assert not panel.loading


def test_page_loads_only_when_opened_and_shows_verdict(launcher):  # noqa: F811
    panel = launcher.analysis_panel
    calls = []

    def fetch(settings, type_id):
        calls.append(type_id)
        return history(range(100, 130)), Decimal(128), Decimal(130)

    panel.fetch = fetch
    panel.clock = lambda: NOW
    launcher.root.update()
    assert calls == []
    launcher.tabs.select(launcher.analysis_tab)
    launcher.root.update()
    wait(launcher, panel)
    assert calls == [55831]
    assert panel.verdict.get() == "🟢 Good time to sell"
    assert panel.metrics["Price now"].get() == "129 ISK"
    assert panel.metrics["Position in range"].get() == "100%"
    assert panel.canvas.find_all()
    for period in ("7d", "90d", "1y"):
        panel.period.set(period)
        panel.render()
        assert panel.canvas.find_all()
    panel.start()  # A second visit does not start another refresh loop.
    assert calls == [55831]


def test_esi_failure_is_shown_without_details(launcher):  # noqa: F811
    panel = launcher.analysis_panel

    def fail(settings, type_id):
        raise RuntimeError("secret token in text")

    panel.fetch = fail
    panel.refresh()
    wait(launcher, panel)
    assert "RuntimeError" in panel.status.get() and "secret" not in panel.status.get()
    assert panel.verdict.get() == ""


def test_fees_are_validated_and_saved(launcher):  # noqa: F811
    panel = launcher.analysis_panel
    panel.tax.set("4,2")
    panel.broker.set("1")
    panel.save_fees()
    market = read_config(launcher.profile.config_path)["market"]
    assert market["sales_tax_percent"] == "4.2" and market["broker_fee_percent"] == "1"
    panel.reload()
    assert panel.tax.get() == "4.2"
    panel.tax.set("150")
    panel.save_fees()
    assert "Sales tax" in str(launcher._error.call_args.args[0])


def test_analysis_page_follows_market_feature(launcher):  # noqa: F811
    server = launcher.current_server()
    hidden = not server["features"].get("market")
    assert (launcher.tabs.tab(launcher.analysis_tab, "state") == "hidden") is hidden
    assert (launcher.tabs.tab(launcher.market_tab, "state") == "hidden") is hidden
