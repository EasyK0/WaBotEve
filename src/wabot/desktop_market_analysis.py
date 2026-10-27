"""Live market analysis page: price chart and sell timing straight from public ESI."""

import asyncio
import copy
import queue
import threading
import tkinter as tk
from datetime import UTC, datetime, timedelta
from tkinter import ttk

from wabot import desktop_theme as theme
from wabot.market_analysis import analyze, isk
from wabot.market_config import MarketSettings
from wabot.market_esi import ESIClient
from wabot.market_report import analyze_orders, group_orders

REFRESH_MS = 5 * 60 * 1000
POLL_MS = 150
PERIODS = (("7d", 7), ("30d", 30), ("90d", 90), ("1y", 365))
CHART_WIDTH = 820
CHART_HEIGHT = 300
BAND = "#3a1a1d"
VOLUME = "#34343b"
VERDICT_COLOURS = {"good": theme.SUCCESS, "neutral": theme.WARNING, "weak": theme.DANGER}


def fetch_market(settings: MarketSettings, type_id: int):
    """History plus the best Jita 4-4 / Perimeter quotes; runs in a worker thread."""

    async def run():
        esi = ESIClient(timeout_seconds=settings.request_timeout_seconds)
        try:
            history = await esi.fetch_history(settings.region_id, type_id)
            fetched = await esi.fetch_orders(settings.region_id, type_id)
        finally:
            await esi.close()
        groups = group_orders(fetched.orders, settings)
        quotes = analyze_orders([order for local in groups.values() for order in local])
        return history, quotes.best_buy, quotes.best_sell

    return asyncio.run(run())


def _short(value) -> str:
    value = float(value)
    for limit, suffix in ((1e9, "b"), (1e6, "m"), (1e3, "k")):
        if abs(value) >= limit:
            return f"{value / limit:.2f}{suffix}"
    return f"{value:.2f}"


class MarketAnalysisPanel(ttk.Frame):
    def __init__(self, parent, launcher, *, fetch=fetch_market, clock=lambda: datetime.now(UTC)):
        super().__init__(parent, padding=(0, 0, 18, 18))
        self.launcher = launcher
        self.fetch = fetch
        self.clock = clock
        self.item = tk.StringVar()
        self.period = tk.StringVar(value="30d")
        self.status = tk.StringVar(value="Open this page to load the analysis.")
        self.verdict = tk.StringVar()
        self.notes = tk.StringVar()
        self.tax = tk.StringVar()
        self.broker = tk.StringVar()
        self.metrics = {}
        self.data = {}
        self.results = queue.Queue()
        self.loading = False
        self.started = False
        self.items = ()
        self.columnconfigure(0, weight=1)

        top = theme.card(
            self,
            "Price chart",
            "Daily average price with the daily low–high band, traded volume and the price now. "
            "Refreshes every 5 minutes while the launcher is open; the bot does not need to run.",
        )
        top.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        controls = ttk.Frame(top, style="Card.TFrame", borderwidth=0)
        controls.pack(fill="x", pady=(0, 10))
        ttk.Label(controls, text="Item", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.item_choice = ttk.Combobox(
            controls, textvariable=self.item, state="readonly", width=42
        )
        self.item_choice.pack(side="left")
        self.item_choice.bind("<<ComboboxSelected>>", lambda _: self._item_changed())
        for label, _ in PERIODS:
            ttk.Radiobutton(
                controls,
                text=label,
                value=label,
                variable=self.period,
                style="Card.TRadiobutton",
                command=self.render,
            ).pack(side="left", padx=(14, 0))
        self.refresh_button = ttk.Button(controls, text="Refresh", command=self.refresh)
        self.refresh_button.pack(side="right")
        self.canvas = tk.Canvas(
            top,
            width=CHART_WIDTH,
            height=CHART_HEIGHT,
            bg=theme.CARD,
            highlightthickness=0,
        )
        self.canvas.pack(fill="x")
        ttk.Label(top, textvariable=self.status, style="CardMuted.TLabel").pack(
            anchor="w", pady=(8, 0)
        )

        summary = theme.card(self, "Sell timing (last 30 days)")
        summary.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        self.verdict_label = ttk.Label(
            summary, textvariable=self.verdict, style="Card.TLabel", font=(theme.FONT, 14, "bold")
        )
        self.verdict_label.pack(anchor="w")
        ttk.Label(summary, textvariable=self.notes, style="CardMuted.TLabel").pack(
            anchor="w", pady=(0, 10)
        )
        grid = ttk.Frame(summary, style="Card.TFrame", borderwidth=0)
        grid.pack(fill="x")
        names = (
            "Price now",
            "Position in range",
            "Best buy",
            "Best sell",
            "7d average",
            "30d average",
            "30d range",
            "Trend 7d vs 30d",
            "Volume per day (7d / 30d)",
            "Best weekday",
            "Instant sell, net per unit",
            "Listing, net per unit",
        )
        for index, name in enumerate(names):
            row, column = divmod(index, 2)
            ttk.Label(grid, text=name, style="CardMuted.TLabel").grid(
                row=row, column=column * 2, sticky="w", padx=(0 if column == 0 else 28, 12), pady=2
            )
            variable = tk.StringVar(value="–")
            ttk.Label(grid, textvariable=variable, style="Card.TLabel").grid(
                row=row, column=column * 2 + 1, sticky="w", pady=2
            )
            self.metrics[name] = variable
        ttk.Label(
            summary,
            text="🟢 Good time to sell: price in the top 20% of the 30-day range and not falling. "
            "🔴 Weak: bottom 20%. Statistics from history, not a guarantee.",
            style="CardMuted.TLabel",
            wraplength=820,
        ).pack(anchor="w", pady=(10, 0))

        fees = theme.card(
            self,
            "Fees",
            "Used for the net prices here, in /market-analysis and in the report buttons. "
            "Check your own rates in game (wallet or market window).",
        )
        fees.grid(row=2, column=0, sticky="ew")
        form = ttk.Frame(fees, style="Card.TFrame", borderwidth=0)
        form.pack(fill="x")
        for column, label, variable in (
            (0, "Sales tax %", self.tax),
            (2, "Broker fee %", self.broker),
        ):
            ttk.Label(form, text=label, style="Card.TLabel").grid(
                row=0, column=column, sticky="w", padx=(0 if column == 0 else 24, 10)
            )
            ttk.Entry(form, textvariable=variable, width=10).grid(row=0, column=column + 1)
        self.save_button = ttk.Button(
            form, text="Save fees", style="Accent.TButton", command=self.save_fees
        )
        self.save_button.grid(row=0, column=4, padx=(24, 0))
        self.reload()

    def settings(self) -> MarketSettings:
        return MarketSettings.from_config(self.launcher.config.get("market"), environ={})

    def _selected(self):
        return next((item for item in self.items if item.display_name == self.item.get()), None)

    def reload(self):
        settings = self.settings()
        self.items = settings.enabled_items
        names = [item.display_name for item in self.items]
        self.item_choice.configure(values=names)
        if self.item.get() not in names:
            self.item.set(names[0] if names else "")
        self.tax.set(f"{settings.sales_tax_percent:g}")
        self.broker.set(f"{settings.broker_fee_percent:g}")
        self.render()

    def set_editable(self, editable):
        self.save_button.configure(state="normal" if editable else "disabled")

    def start(self):
        """First visit loads data, then it refreshes on its own every 5 minutes."""
        if self.started:
            return
        self.started = True
        self.refresh()
        self.after(REFRESH_MS, self._auto)

    def _auto(self):
        self.refresh()
        self.after(REFRESH_MS, self._auto)

    def _item_changed(self):
        self.render()
        if self._selected() is not None and self._selected().type_id not in self.data:
            self.refresh()

    def refresh(self):
        item = self._selected()
        if item is None:
            self.status.set("Add an item on the Market page to see its analysis.")
            return
        if self.loading:
            return
        self.loading = True
        self.refresh_button.configure(state="disabled")
        self.status.set(f"Loading {item.display_name} from ESI…")
        settings = self.settings()

        def work():
            try:
                self.results.put((item.type_id, self.fetch(settings, item.type_id), None))
            except Exception as exc:
                self.results.put((item.type_id, None, exc))

        threading.Thread(target=work, daemon=True).start()
        self.after(POLL_MS, self._poll)

    def _poll(self):
        try:
            type_id, result, error = self.results.get_nowait()
        except queue.Empty:
            self.after(POLL_MS, self._poll)
            return
        self.loading = False
        self.refresh_button.configure(state="normal")
        if error is not None:
            # Network errors may carry arbitrary text; show only their type.
            self.status.set(
                f"ESI is unavailable right now ({type(error).__name__}). "
                "It will try again at the next refresh."
            )
            return
        self.data[type_id] = (*result, self.clock())
        self.render()

    def analysis(self):
        item = self._selected()
        if item is None or item.type_id not in self.data:
            return None, None
        history, buy, sell, fetched_at = self.data[item.type_id]
        settings = self.settings()
        result = analyze(
            history,
            buy,
            sell,
            today=fetched_at.date(),
            sales_tax=settings.sales_tax_percent,
            broker_fee=settings.broker_fee_percent,
        )
        return history, result

    def render(self):
        history, result = self.analysis()
        item = self._selected()
        self._draw(history or [], result)
        if item is None or history is None:
            self.verdict.set("")
            self.notes.set("")
            for variable in self.metrics.values():
                variable.set("–")
            return
        fetched_at = self.data[item.type_id][3]
        self.status.set(
            f"Updated {fetched_at.astimezone():%H:%M:%S} · next refresh in 5 minutes · "
            "Jita 4-4 + Perimeter orders, The Forge history"
        )
        if result is None:
            self.verdict.set("⚪ Not enough market history")
            self.verdict_label.configure(foreground=theme.MUTED)
            self.notes.set("An analysis needs at least 7 recent days of trading.")
            for variable in self.metrics.values():
                variable.set("–")
            return
        self.verdict.set(f"{result.icon} {result.label}")
        self.verdict_label.configure(foreground=VERDICT_COLOURS[result.verdict])
        self.notes.set(", ".join(result.notes).capitalize())
        values = {
            "Price now": isk(result.price_now),
            "Best buy": isk(result.best_buy),
            "Best sell": isk(result.best_sell),
            "7d average": isk(result.avg7),
            "30d average": isk(result.avg30),
            "30d range": f"{isk(result.low30)} – {isk(result.high30)}",
            "Position in range": f"{result.position:.0f}%",
            "Trend 7d vs 30d": f"{result.trend:+.1f}%",
            "Volume per day (7d / 30d)": f"{result.volume7:,.0f} / {result.volume30:,.0f}".replace(
                ",", " "
            ),
            "Best weekday": result.best_weekday or "Not enough data",
            "Instant sell, net per unit": isk(result.instant_net),
            "Listing, net per unit": isk(result.listing_net),
        }
        for name, value in values.items():
            self.metrics[name].set(value)

    def _draw(self, history, result):
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), CHART_WIDTH) if canvas.winfo_ismapped() else CHART_WIDTH
        height = CHART_HEIGHT
        days = dict(PERIODS)[self.period.get()]
        if history:
            start = history[-1].day - timedelta(days=days - 1)
            history = [day for day in history if day.day >= start]
        if len(history) < 2:
            canvas.create_text(
                width / 2,
                height / 2,
                text="No chart yet" if not self.loading else "Loading…",
                fill=theme.MUTED,
                font=(theme.FONT, 11),
            )
            return
        left, right, top, bottom = 78, 16, 12, 26
        plot_bottom = height - bottom
        volume_top = plot_bottom - (plot_bottom - top) * 0.22
        price_bottom = volume_top - 8
        prices = [day.average for day in history]
        if result is not None:
            prices.append(result.price_now)
        low, high = float(min(prices)), float(max(prices))
        pad = (high - low) * 0.08 or max(high * 0.05, 1)
        low, high = max(0.0, low - pad), high + pad

        def x(index):
            return left + (width - left - right) * index / (len(history) - 1)

        def y(price):
            price = min(max(float(price), low), high)
            return price_bottom - (price_bottom - top) * (price - low) / (high - low)

        for fraction in (0, 0.5, 1):
            price = low + (high - low) * fraction
            canvas.create_line(left, y(price), width - right, y(price), fill=theme.CARD_BORDER)
            canvas.create_text(
                left - 8,
                y(price),
                text=_short(price),
                anchor="e",
                fill=theme.MUTED,
                font=(theme.FONT, 8),
            )
        band = [(x(i), y(day.highest)) for i, day in enumerate(history)]
        band += [(x(i), y(day.lowest)) for i, day in reversed(list(enumerate(history)))]
        canvas.create_polygon(*[value for point in band for value in point], fill=BAND, outline="")
        most = max(day.volume for day in history) or 1
        bar = max(1.0, (width - left - right) / len(history) * 0.6)
        for index, day in enumerate(history):
            top_y = plot_bottom - (plot_bottom - volume_top) * day.volume / most
            canvas.create_rectangle(
                x(index) - bar / 2, top_y, x(index) + bar / 2, plot_bottom, fill=VOLUME, outline=""
            )
        canvas.create_line(
            *[value for i, day in enumerate(history) for value in (x(i), y(day.average))],
            fill=theme.ACCENT_HOVER,
            width=2,
        )
        if result is not None:
            now_y = y(result.price_now)
            canvas.create_line(left, now_y, width - right, now_y, fill=theme.SUCCESS, dash=(4, 3))
            canvas.create_text(
                width - right - 4,
                now_y - 8,
                text=f"now {_short(result.price_now)}",
                anchor="e",
                fill=theme.SUCCESS,
                font=(theme.FONT, 8, "bold"),
            )
        for index in (0, len(history) - 1):
            canvas.create_text(
                x(index),
                height - 10,
                text=f"{history[index].day:%d %b}",
                fill=theme.MUTED,
                anchor="w" if index == 0 else "e",
                font=(theme.FONT, 8),
            )
        canvas.create_text(
            left + 4, volume_top, text="volume", anchor="nw", fill=theme.MUTED, font=(theme.FONT, 7)
        )

    def save_fees(self):
        try:
            config = copy.deepcopy(self.launcher.config)
            market = dict(config.get("market", {}))
            market["sales_tax_percent"] = self.tax.get().strip().replace(",", ".")
            market["broker_fee_percent"] = self.broker.get().strip().replace(",", ".")
            MarketSettings.from_config(market, environ={})
            config["market"] = market
            self.launcher._save_config(config)
            self.render()
            self.status.set("Fees saved. The bot uses them after its next start.")
        except Exception as exc:
            self.launcher._error(exc)
