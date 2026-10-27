"""Polish Windows launcher: start/stop, configuration, logs and migration."""

from __future__ import annotations

import asyncio
import copy
import ctypes
import hashlib
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from filelock import FileLock, Timeout

from wabot import desktop_theme as theme
from wabot.channel_test import saved_channels, send_test_messages
from wabot.desktop_items import MarketItemsPanel
from wabot.desktop_market_analysis import MarketAnalysisPanel
from wabot.desktop_messages import MessagesPanel
from wabot.desktop_pvp import PvpPanel
from wabot.desktop_scroll import ScrollablePage
from wabot.desktop_statistics import StatisticsPanel
from wabot.desktop_support import (
    APP_VERSION,
    DesktopProfile,
    atomic_write,
    json_bytes,
    read_config,
    validate_config,
)
from wabot.servers import (
    FEATURE_DESCRIPTIONS,
    FEATURE_LABELS,
    FEATURE_TABS,
    FEATURES,
    ServerSettings,
    migrate_config,
)


def worker_command(profile: DesktopProfile, command: str) -> list[str]:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).with_name("WabotWorker.exe")
        prefix = [str(executable)]
    else:
        prefix = [sys.executable, "-m", "wabot.desktop_entry"]
    return [*prefix, "--data-dir", str(profile.directory), "--worker", command]


class StatusPill(tk.Frame):
    """Bot state in the header; `configure(foreground=...)` colors the dot and text."""

    def __init__(self, parent, variable):
        super().__init__(parent, bg="#1a1a1f", padx=14, pady=7)
        self.dot = tk.Label(self, text="●", bg="#1a1a1f", fg=theme.MUTED, font=(theme.FONT, 11))
        self.dot.pack(side="left")
        self.text = tk.Label(
            self, textvariable=variable, bg="#1a1a1f", fg=theme.MUTED, font=(theme.FONT, 10, "bold")
        )
        self.text.pack(side="left", padx=(6, 0))

    def configure(self, cnf=None, **kwargs):
        color = kwargs.pop("foreground", None)
        if color is not None:
            self.dot.configure(fg=color)
            self.text.configure(fg=color)
        if cnf or kwargs:
            return super().configure(cnf, **kwargs)
        return None

    config = configure


class Launcher:
    def __init__(self, root: tk.Tk, profile: DesktopProfile):
        self.root, self.profile = root, profile
        self.process: subprocess.Popen | None = None
        self.events: queue.Queue = queue.Queue()
        self.phase: str | None = None
        self.busy = False
        self.testing_channels = False
        self.channel_test_notice = tk.StringVar(
            value="The test uses saved channels. Save changes before testing."
        )
        self.stopping = False
        self.closing = False
        self.start_after_sync = False
        self.config = (
            read_config(profile.config_path) if profile.config_path.exists() else {"guild_ids": []}
        )
        if "servers" not in self.config and self.config.get("guild_ids"):
            # Older single-server profiles are shown per server; saved on the next change.
            self.config = migrate_config(self.config)
        servers = self.config.get("servers", [])
        self.current_guild = servers[0]["id"] if servers else None
        self.root.title(f"Wabot {APP_VERSION}")
        self.root.geometry("1220x800")
        self.root.minsize(980, 680)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        theme.apply(root)
        header = ttk.Frame(root, style="Header.TFrame", padding=(22, 14))
        header.pack(fill="x")
        brand = ttk.Frame(header, style="Header.TFrame")
        brand.pack(side="left")
        ttk.Label(brand, text="WABOT", style="Logo.TLabel").pack(anchor="w")
        ttk.Label(brand, text="EVE Online · Discord", style="HeaderMuted.TLabel").pack(anchor="w")
        self.status = tk.StringVar(value="Stopped")
        self.status_label = StatusPill(header, self.status)
        self.status_label.pack(side="right")
        self._server_bar(header)
        tk.Frame(root, height=2, bg=theme.ACCENT).pack(fill="x")
        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)
        self.sidebar = ttk.Frame(body, style="Sidebar.TFrame", width=220)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.nav_frame = ttk.Frame(self.sidebar, style="Sidebar.TFrame", padding=(0, 14))
        self.nav_frame.pack(fill="x")
        ttk.Label(
            self.sidebar,
            text=f"Version {APP_VERSION}\nData: {profile.directory}",
            style="Section.TLabel",
            wraplength=190,
            font=(theme.FONT, 8),
        ).pack(side="bottom", anchor="w", padx=18, pady=14)
        content = ttk.Frame(body, padding=(28, 20, 28, 18))
        content.pack(side="left", fill="both", expand=True)
        self.page_title = tk.StringVar()
        self.page_subtitle = tk.StringVar()
        ttk.Label(content, textvariable=self.page_title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(content, textvariable=self.page_subtitle, style="Muted.TLabel").pack(
            anchor="w", pady=(2, 14)
        )
        self.tabs = ttk.Notebook(content, style="Pages.TNotebook")
        self.tabs.pack(fill="both", expand=True)
        self.bot_tab, self.server_tab, self.settings_tab, self.transfer_tab = (
            ttk.Frame(self.tabs) for _ in range(4)
        )
        self.timers_tab = ttk.Frame(self.tabs)
        self.market_tab = ttk.Frame(self.tabs)
        self.pvp_tab = ScrollablePage(self.tabs)
        self.analysis_tab = ScrollablePage(self.tabs)
        # Pages shown and hidden together with a feature tab.
        self.feature_extras = {"market": (self.analysis_tab,)}
        # Feature tabs keep their position; disabled features are hidden, not destroyed.
        self.feature_tabs = {
            "timers": self.timers_tab,
            "market": self.market_tab,
            "pvp": self.pvp_tab,
        }
        self.pages = [
            ("BOT", self.bot_tab, "Bot & logs", "Start the bot and view the event log"),
            (
                "SERVER",
                self.server_tab,
                "Server features",
                "Turn bot features on for the selected server",
            ),
            (
                "SERVER",
                self.timers_tab,
                FEATURE_TABS["timers"],
                "Reminder messages and /stats, /rank statistics",
            ),
            (
                "SERVER",
                self.market_tab,
                FEATURE_TABS["market"],
                "Jita + Perimeter price report on a server channel",
            ),
            (
                "SERVER",
                self.analysis_tab,
                "Market analysis",
                "Price chart and sell timing for watched items, live from ESI",
            ),
            (
                "SERVER",
                self.pvp_tab,
                FEATURE_TABS["pvp"],
                "Pochven killmails and alliance kills on server channels",
            ),
            ("SETTINGS", self.settings_tab, "Bot token", "Connection to the Discord application"),
            (
                "SETTINGS",
                self.transfer_tab,
                "Move bot",
                "Export and import data to another computer",
            ),
        ]
        self.nav_buttons = {}
        for _, frame, title, _ in self.pages:
            self.tabs.add(frame, text=title)
            self.nav_buttons[str(frame)] = ttk.Button(
                self.nav_frame,
                text=title,
                style="Nav.TButton",
                command=lambda frame=frame: self.tabs.select(frame),
            )
        self.tabs.bind("<<NotebookTabChanged>>", lambda _: self._refresh_nav(), add=True)
        self.tabs.bind("<<NotebookTabChanged>>", lambda _: self._page_opened(), add=True)
        self._bot_tab()
        self._server_tab()
        self._timers_tab()
        self._market_tab()
        self.analysis_panel = MarketAnalysisPanel(self.analysis_tab.body, self)
        self.analysis_panel.pack(fill="both", expand=True)
        self.pvp_panel = PvpPanel(self.pvp_tab.body, self)
        self.pvp_panel.pack(fill="both", expand=True)
        self._settings_tab()
        self._transfer_tab()
        self._load_fields()
        self._controls()
        self.root.after(100, self._poll)

    def _page_opened(self):
        # The analysis reads ESI only once someone actually opens its page.
        if self.tabs.select() == str(self.analysis_tab):
            self.analysis_panel.start()

    def _refresh_nav(self):
        for child in self.nav_frame.winfo_children():
            child.pack_forget()
        selected = self.tabs.select()
        section = None
        for name, frame, title, subtitle in self.pages:
            if self.tabs.tab(frame, "state") == "hidden":
                continue
            if name != section:
                section = name
                label = ttk.Label(self.nav_frame, text=name, style="Section.TLabel")
                label.pack(anchor="w", padx=18, pady=(14, 4))
            button = self.nav_buttons[str(frame)]
            button.configure(style="NavActive.TButton" if str(frame) == selected else "Nav.TButton")
            button.pack(fill="x")
            if str(frame) == selected:
                self.page_title.set(title)
                self.page_subtitle.set(subtitle)

    # Servers -------------------------------------------------------------

    def _server_bar(self, header):
        bar = ttk.Frame(header, style="Header.TFrame")
        bar.pack(side="left", padx=(48, 0))
        ttk.Label(bar, text="DISCORD SERVER", style="Section.TLabel").pack(anchor="w")
        row = ttk.Frame(bar, style="Header.TFrame")
        row.pack(anchor="w", pady=(3, 0))
        self.server_label = tk.StringVar()
        self.server_choice = ttk.Combobox(
            row, textvariable=self.server_label, state="readonly", width=40
        )
        self.server_choice.pack(side="left")
        self.server_choice.bind("<<ComboboxSelected>>", self.select_server)
        self.add_server_button = ttk.Button(
            row, text="+ Add server", style="Accent.TButton", command=self.add_server
        )
        self.add_server_button.pack(side="left", padx=(10, 4))
        self.rename_server_button = ttk.Button(row, text="Rename", command=self.rename_server)
        self.rename_server_button.pack(side="left", padx=4)
        self.remove_server_button = ttk.Button(
            row, text="Remove", style="Danger.TButton", command=self.remove_server
        )
        self.remove_server_button.pack(side="left", padx=4)
        self.server_buttons = [
            self.add_server_button,
            self.rename_server_button,
            self.remove_server_button,
        ]

    def servers(self) -> list[dict]:
        return self.config.get("servers", [])

    def current_server(self) -> dict | None:
        return next((s for s in self.servers() if s["id"] == self.current_guild), None)

    @staticmethod
    def _server_label(server: dict) -> str:
        return f"{server['name']} ({server['id']})" if server.get("name") else server["id"]

    def _idle(self) -> bool:
        return (
            self.process is None
            and not self.busy
            and not self.testing_channels
            and not self.closing
        )

    def _save_config(self, config: dict, token: str | None = None) -> None:
        if not self._idle():
            raise ValueError("Stop the bot before changing settings.")
        self.profile.save(config, token)
        self.config = validate_config(config)

    def update_server(self, change) -> None:
        """Apply `change(server_dict)` to the selected server and save the configuration."""
        if self.current_server() is None:
            raise ValueError("Add a Discord server first.")
        config = copy.deepcopy(self.config)
        for server in config["servers"]:
            if server["id"] == self.current_guild:
                change(server)
        self._save_config(config)

    def select_server(self, _event=None):
        label = self.server_label.get()
        for server in self.servers():
            if self._server_label(server) == label:
                self.current_guild = server["id"]
        self._load_fields()

    def _refresh_servers(self):
        labels = [self._server_label(server) for server in self.servers()]
        self.server_choice.configure(values=labels)
        server = self.current_server()
        if server is None and self.servers():
            self.current_guild = self.servers()[0]["id"]
            server = self.current_server()
        self.server_label.set(self._server_label(server) if server else "")
        features = server["features"] if server else {}
        for feature in FEATURES:
            self.feature_vars[feature].set(features.get(feature, False))
            for tab in (self.feature_tabs[feature], *self.feature_extras.get(feature, ())):
                if features.get(feature, False):
                    if self.tabs.tab(tab, "state") == "hidden":
                        self.tabs.add(tab)
                else:
                    self.tabs.hide(tab)
        self.server_title.set(
            server.get("name") or server["id"]
            if server
            else "No server yet. Click “+ Add server” at the top of the window."
        )
        self.server_id.set(f"Server ID: {server['id']}" if server else "")
        self._refresh_nav()

    def add_server(self):
        if not self._idle():
            self._error(ValueError("Stop the bot before adding a server."))
            return
        guild = simpledialog.askstring(
            "Add server",
            "Discord server ID\n(in Discord: right-click the server icon → Copy Server ID)",
            parent=self.root,
        )
        if not guild:
            return
        name = simpledialog.askstring(
            "Add server", "Server name shown in the launcher:", parent=self.root
        )
        try:
            server = ServerSettings(guild.strip(), (name or "").strip())
            if any(item["id"] == server.guild_id for item in self.servers()):
                raise ValueError("This server is already on the list.")
            config = copy.deepcopy(self.config)
            config.setdefault("servers", []).append(server.to_json())
            self._save_config(config)
        except Exception as exc:
            self._error(exc)
            return
        self.current_guild = server.guild_id
        self._load_fields()
        self.tabs.select(self.server_tab)
        self._log(f"Added server {server.label}. Choose the features it should use.")

    def rename_server(self):
        server = self.current_server()
        if server is None or not self._idle():
            return
        name = simpledialog.askstring(
            "Rename", "New server name:", initialvalue=server["name"], parent=self.root
        )
        if name is None:
            return
        try:
            self.update_server(lambda item: item.update(name=name.strip()))
        except Exception as exc:
            self._error(exc)
            return
        self._load_fields(reload_editors=False)

    def remove_server(self):
        server = self.current_server()
        if server is None or not self._idle():
            return
        if len(self.servers()) == 1:
            self._error(ValueError("The bot needs at least one server."))
            return
        if not messagebox.askyesno(
            "Remove server",
            f"Remove {self._server_label(server)} from the bot? Its statistics stay in the database.",
            parent=self.root,
        ):
            return
        try:
            config = copy.deepcopy(self.config)
            config["servers"] = [item for item in config["servers"] if item["id"] != server["id"]]
            self._save_config(config)
        except Exception as exc:
            self._error(exc)
            return
        self.current_guild = self.servers()[0]["id"]
        self._load_fields()
        self._log(f"Removed server {self._server_label(server)}.")

    def toggle_feature(self, feature: str):
        enabled = self.feature_vars[feature].get()
        try:
            self.update_server(lambda server: server["features"].__setitem__(feature, enabled))
        except Exception as exc:
            self.feature_vars[feature].set(not enabled)
            self._error(exc)
            return
        self._load_fields(reload_editors=False)
        if enabled:
            self.tabs.select(self.feature_tabs[feature])
        self._log(
            f"{FEATURE_TABS[feature]}: {'enabled' if enabled else 'disabled'} on server "
            f"{self.server_label.get()}. The change applies after the bot restarts."
        )

    def _server_tab(self):
        self.server_title = tk.StringVar()
        self.server_id = tk.StringVar()
        top = theme.card(self.server_tab)
        top.pack(fill="x")
        ttk.Label(
            top,
            textvariable=self.server_title,
            style="CardTitle.TLabel",
            font=(theme.FONT, 15, "bold"),
        ).pack(anchor="w")
        ttk.Label(top, textvariable=self.server_id, style="CardMuted.TLabel").pack(
            anchor="w", pady=(2, 0)
        )
        ttk.Label(
            self.server_tab,
            text="BOT FEATURES",
            style="Muted.TLabel",
            font=(theme.FONT, 9, "bold"),
        ).pack(anchor="w", pady=(18, 6))
        self.feature_vars = {feature: tk.BooleanVar() for feature in FEATURES}
        self.feature_checks = []
        for feature in FEATURES:
            row = theme.card(self.server_tab, padding=(18, 14))
            row.pack(fill="x", pady=4)
            switch = theme.ToggleSwitch(
                row,
                self.feature_vars[feature],
                command=lambda feature=feature: self.toggle_feature(feature),
            )
            switch.pack(side="right", padx=(18, 0))
            ttk.Label(
                row,
                text=FEATURE_LABELS[feature],
                style="CardTitle.TLabel",
                font=(theme.FONT, 11, "bold"),
            ).pack(anchor="w")
            ttk.Label(
                row, text=FEATURE_DESCRIPTIONS[feature], style="CardMuted.TLabel", wraplength=700
            ).pack(anchor="w", pady=(2, 0))
            self.feature_checks.append(switch)
        ttk.Label(
            self.server_tab,
            text="An enabled feature appears in the menu on the left. Changes apply after the bot "
            "restarts, and the bot must be stopped before changing them.",
            style="Muted.TLabel",
            wraplength=820,
        ).pack(anchor="w", pady=(10, 0))
        test = theme.card(
            self.server_tab,
            "Channel test",
            "Sends a test message to every saved channel of this server, without pings.",
        )
        test.pack(fill="x", pady=(16, 0))
        self.test_channels_button = ttk.Button(
            test, text="Send test", style="Accent.TButton", command=self.test_channels
        )
        self.test_channels_button.pack(anchor="w")
        ttk.Label(
            test, textvariable=self.channel_test_notice, style="CardMuted.TLabel", wraplength=780
        ).pack(anchor="w", pady=(10, 0))

    def _timers_tab(self):
        ttk.Label(
            self.timers_tab,
            text="Message texts are shared by all servers, "
            "statistics are kept separately for each server.",
            style="Muted.TLabel",
            wraplength=850,
        ).pack(anchor="w", pady=(0, 10))
        pages = ttk.Notebook(self.timers_tab)
        pages.pack(fill="both", expand=True)
        self.messages_tab = ScrollablePage(pages)
        self.messages_panel = MessagesPanel(self.messages_tab.body, self)
        self.messages_panel.pack(fill="both", expand=True)
        pages.add(self.messages_tab, text="Messages / pings")
        self.statistics_tab = ScrollablePage(pages)
        self.statistics_panel = StatisticsPanel(self.statistics_tab.body, self)
        self.statistics_panel.pack(fill="both", expand=True)
        pages.add(self.statistics_tab, text="Statistics / leaderboard")
        pages.bind(
            "<<NotebookTabChanged>>",
            lambda _: (
                self.statistics_panel.refresh()
                if pages.select() == str(self.statistics_tab)
                else None
            ),
        )

    def _market_tab(self):
        self.channel = tk.StringVar()
        self.interval = tk.StringVar(value="60")
        form = theme.card(self.market_tab, padding=(18, 14))
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        for row, label, variable in (
            (0, "Report channel on this server (ID)", self.channel),
            (1, "Report interval in minutes (all servers)", self.interval),
        ):
            ttk.Label(form, text=label, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 18), pady=5
            )
            ttk.Entry(form, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        self.market_save_button = ttk.Button(
            form, text="Save market settings", style="Accent.TButton", command=self.save_market
        )
        self.market_save_button.grid(row=2, column=1, sticky="w", pady=(8, 0))
        ttk.Label(
            self.market_tab,
            text="The watched item list is shared by all servers with the market enabled.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(12, 0))
        self.items_panel = MarketItemsPanel(self.market_tab, self)
        self.items_panel.pack(fill="both", expand=True)

    def save_market(self):
        try:
            channel = self.channel.get().strip() or None
            config = copy.deepcopy(self.config)
            market = dict(config.get("market", {}))
            market["interval_minutes"] = float(self.interval.get())
            config["market"] = market
            for server in config.get("servers", []):
                if server["id"] == self.current_guild:
                    server["market"]["channel_id"] = channel
            self._save_config(config)
            self._load_fields(reload_editors=False)
            self._log("Market settings saved.")
        except Exception as exc:
            self._error(exc)

    def _bot_tab(self):
        self.summary = tk.StringVar()
        self.token_notice = tk.StringVar()
        top = ttk.Frame(self.bot_tab)
        top.pack(fill="x")
        top.columnconfigure(0, weight=3, uniform="top")
        top.columnconfigure(1, weight=2, uniform="top")
        control = theme.card(top, "Controls")
        control.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(
            control, textvariable=self.token_notice, style="CardMuted.TLabel", wraplength=520
        ).pack(anchor="w", pady=(4, 12))
        buttons = ttk.Frame(control, style="Card.TFrame", borderwidth=0)
        buttons.pack(fill="x")
        self.start_button = ttk.Button(
            buttons, text="▶  Start bot", style="Start.TButton", command=self.start
        )
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(
            buttons, text="■  Stop", style="Danger.TButton", command=self.stop
        )
        self.stop_button.pack(side="left", padx=(0, 8))
        tools = ttk.Frame(control, style="Card.TFrame", borderwidth=0)
        tools.pack(fill="x", pady=(8, 0))
        self.sync_button = ttk.Button(
            tools, text="Sync commands", command=lambda: self.action("sync")
        )
        self.sync_button.pack(side="left", padx=(0, 8))
        self.check_button = ttk.Button(
            tools, text="Check installation", command=lambda: self.action("check")
        )
        self.check_button.pack(side="left")
        servers = theme.card(top, "Servers")
        servers.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ttk.Label(
            servers,
            textvariable=self.summary,
            style="CardMuted.TLabel",
            wraplength=330,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            self.bot_tab, text="LOG", style="Muted.TLabel", font=(theme.FONT, 9, "bold")
        ).pack(anchor="w", pady=(18, 6))
        self.logs = scrolledtext.ScrolledText(
            self.bot_tab,
            wrap="word",
            height=18,
            bg=theme.FIELD,
            fg="#d4d4d8",
            insertbackground="white",
            font=("Consolas", 10),
            relief="flat",
            padx=14,
            pady=12,
            highlightthickness=1,
            highlightbackground=theme.CARD_BORDER,
            highlightcolor=theme.CARD_BORDER,
        )
        self.logs.pack(fill="both", expand=True)
        self.logs.configure(state="disabled")
        self._log(
            "Ready. The Start button runs the bot. Commands register automatically on the first start."
        )

    def _settings_tab(self):
        self.token_entry = tk.StringVar()
        frame = theme.card(
            self.settings_tab,
            "Discord bot token",
            "One bot serves every server in the list at the top. The token is protected "
            "by your Windows account, and an empty field keeps the saved token.",
        )
        frame.pack(fill="x")
        ttk.Entry(frame, textvariable=self.token_entry, show="•").pack(fill="x", pady=(0, 12))
        buttons = ttk.Frame(frame, style="Card.TFrame", borderwidth=0)
        buttons.pack(fill="x")
        self.save_button = ttk.Button(
            buttons, text="Save token", style="Accent.TButton", command=self.save
        )
        self.save_button.pack(side="left")
        ttk.Button(
            buttons,
            text="Open Discord Developer Portal",
            command=lambda: webbrowser.open("https://discord.com/developers/applications"),
        ).pack(side="left", padx=8)

    def _transfer_tab(self):
        steps = theme.card(
            self.transfer_tab,
            "Moving to another computer",
            "1. Stop the bot on the old computer and export the data.\n"
            "2. On the new computer, run the WabotSetup.exe installer.\n"
            "3. Import the data package and click Start.\n\n"
            "The data package can contain the bot token, so keep it private.",
        )
        steps.pack(fill="x")
        buttons = ttk.Frame(steps, style="Card.TFrame", borderwidth=0)
        buttons.pack(fill="x", pady=(4, 0))
        self.transfer_buttons = []
        for text, callback, style in (
            ("Export data and settings", self.export, "Accent.TButton"),
            ("Import data package", self.import_archive, "TButton"),
            ("Import old bot folder", self.import_folder, "TButton"),
        ):
            button = ttk.Button(buttons, text=text, style=style, command=callback)
            button.pack(side="left", padx=(0, 8))
            self.transfer_buttons.append(button)
        ttk.Button(self.transfer_tab, text="Open data folder", command=self.open_data).pack(
            anchor="w", pady=(14, 0)
        )

    def _load_fields(self, *, reload_editors=True):
        self._refresh_servers()
        self.items_panel.reload()
        self.analysis_panel.reload()
        if reload_editors:
            self.pvp_panel.reload()
            self.messages_panel.reload()
        self.statistics_panel.reload()
        server = self.current_server()
        market = self.config.get("market", {})
        self.channel.set((server or {}).get("market", {}).get("channel_id") or "")
        self.interval.set(str(market.get("interval_minutes", 60)))
        self.token_entry.set("")
        lines = []
        for item in self.servers():
            enabled = [FEATURE_TABS[f] for f in FEATURES if item["features"].get(f)]
            lines.append(
                f"{item.get('name') or item['id']}\n   {', '.join(enabled) or 'no features enabled'}"
            )
        self.summary.set(
            "\n".join(lines) or "Add a Discord server with the button at the top of the window."
        )
        self.token_notice.set(
            "Token saved. You can start the bot."
            if self.profile.token_path.exists()
            else "No Discord token. Paste the bot token on the Bot token page."
        )

    def _log(self, line: str):
        self.logs.configure(state="normal")
        self.logs.insert("end", line.rstrip() + "\n")
        if int(self.logs.index("end-1c").split(".")[0]) > 1500:
            self.logs.delete("1.0", "200.0")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def _controls(self):
        idle = self._idle()
        self.items_panel.set_editable(idle)
        self.analysis_panel.set_editable(idle)
        self.pvp_panel.set_editable(idle)
        self.messages_panel.set_editable(idle)
        self.statistics_panel.set_editable(idle)
        for button in [
            self.start_button,
            self.sync_button,
            self.check_button,
            self.save_button,
            self.market_save_button,
            *self.server_buttons,
            *self.feature_checks,
            *self.transfer_buttons,
        ]:
            button.configure(state="normal" if idle else "disabled")
        self.stop_button.configure(
            state="normal" if self.process is not None and not self.stopping else "disabled"
        )
        can_test = not (self.busy or self.testing_channels or self.closing)
        for button in (self.test_channels_button, self.pvp_panel.test_button):
            button.configure(state="normal" if can_test else "disabled")

    def test_channels(self, *, pvp_only=False):
        if self.busy or self.testing_channels or self.closing:
            return
        try:
            config = read_config(self.profile.config_path)
            channels = saved_channels(
                config,
                pvp_only=pvp_only,
                guild_id=self.current_guild if "servers" in config else None,
            )
            if not channels:
                raise ValueError("Save at least one channel to test first.")
            token = self.profile.token()
            if not token:
                raise ValueError("Save the bot token on the Bot token page before sending a test.")
        except Exception as exc:
            self._error(exc)
            return
        self.testing_channels = True
        self.channel_test_notice.set(f"Sending a test to {len(channels)} saved channel(s)…")
        self._controls()

        def work():
            try:
                results = asyncio.run(
                    send_test_messages(
                        token,
                        tuple(config["guild_ids"]),
                        channels,
                        report=lambda result: self.events.put(("channel_test_result", result)),
                    )
                )
                self.events.put(("channel_test_done", results))
            except Exception:
                # Never expose tokens or arbitrary HTTP exception text in the GUI.
                self.events.put(("channel_test_done", None))

        threading.Thread(target=work, daemon=True).start()

    def _error(self, exc: Exception):
        if isinstance(exc, Timeout):
            text = "The database is in use. Stop the other copy of the bot before doing this."
        elif isinstance(exc, ValueError):
            text = str(exc)
        else:
            # Do not expose exception text containing credentials or arbitrary network data.
            text = f"The operation failed ({type(exc).__name__}). Check the selected file and access to the data folder."
        messagebox.showerror("Wabot", text, parent=self.root)

    def save(self):
        if not self._idle():
            return
        try:
            token = self.token_entry.get().strip()
            if not token:
                raise ValueError("Paste the bot token before saving.")
            self._save_config(self.config, token)
            self._load_fields(reload_editors=False)
            self._log("Token saved.")
        except Exception as exc:
            self._error(exc)

    def _sync_stamp(self) -> bytes:
        return hashlib.sha256(json_bytes(self.config) + APP_VERSION.encode()).hexdigest().encode()

    def start(self):
        try:
            if not self.profile.token():
                self.tabs.select(self.settings_tab)
                messagebox.showinfo(
                    "Discord token",
                    "Paste the bot token and click Save token.",
                    parent=self.root,
                )
                return
            marker = self.profile.directory / "commands.sha256"
            self.start_after_sync = not marker.exists() or marker.read_bytes() != self._sync_stamp()
            self.action("sync" if self.start_after_sync else "run")
        except Exception as exc:
            self._error(exc)

    def action(self, command: str):
        if self.process is not None or self.busy or self.testing_channels:
            return
        try:
            validate_config(self.config)
            environment = dict(os.environ)
            environment.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1")
            self.process = subprocess.Popen(
                worker_command(self.profile, command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.phase = command
            self.stopping = False
            self.status.set(
                {
                    "sync": "Registering commands…",
                    "check": "Checking installation…",
                    "run": "Connecting to Discord…",
                }.get(command, "Working…")
            )
            self.status_label.configure(foreground="#fbbf24")
            self._controls()
            threading.Thread(target=self._read_process, args=(self.process,), daemon=True).start()
        except Exception as exc:
            self._error(exc)

    def _read_process(self, process: subprocess.Popen):
        if process.stdout is not None:
            for line in process.stdout:
                self.events.put(("log", line))
        self.events.put(("exit", process.wait()))

    def stop(self):
        self.start_after_sync = False
        if self.process is None or self.stopping:
            return
        self.stopping = True
        self.status.set("Stopping…")
        self.status_label.configure(foreground="#fbbf24")
        if self.phase == "run":
            try:
                self.process.stdin.write("STOP\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass
        # Read-only check/sync finish naturally. They cannot publish reminders.
        self._controls()

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._log(value)
                    if "Connected to Discord as bot ID" in value and not self.stopping:
                        self.status.set("Bot running")
                        self.status_label.configure(foreground="#4ade80")
                elif kind == "exit":
                    phase = self.phase
                    start_after_sync = self.start_after_sync
                    self.start_after_sync = False
                    if self.process:
                        for stream in (self.process.stdin, self.process.stdout):
                            if stream:
                                try:
                                    stream.close()
                                except OSError:
                                    pass
                    self.process = None
                    self.phase = None
                    self.status.set("Stopped" if value == 0 else f"Start failed (code {value})")
                    self.status_label.configure(foreground="#a1a1aa" if value == 0 else "#f87171")
                    self._log(f"Process ended. Code: {value}.")
                    if phase == "sync" and value == 0:
                        atomic_write(self.profile.directory / "commands.sha256", self._sync_stamp())
                        if start_after_sync and not self.closing:
                            self.action("run")
                    self._controls()
                elif kind == "job_done":
                    self.busy = False
                    if value is not None:
                        self._error(value)
                    else:
                        self.config = read_config(self.profile.config_path)
                        if "servers" not in self.config and self.config.get("guild_ids"):
                            self.config = migrate_config(self.config)
                        self.current_guild = None
                        self._load_fields()
                        self._log("Data transfer finished.")
                    self._controls()
                elif kind == "channel_test_result":
                    line = f"Channel {value.channel_id}: {value.detail}"
                    self.channel_test_notice.set(line)
                    self._log(line)
                elif kind == "channel_test_done":
                    self.testing_channels = False
                    if value is None:
                        self.channel_test_notice.set(
                            "The test failed. Check the token and the connection to Discord."
                        )
                    else:
                        sent = sum(result.success for result in value)
                        details = "\n".join(
                            f"{result.channel_id}: {result.detail}" for result in value
                        )
                        self.channel_test_notice.set(
                            f"Test finished: sent {sent}/{len(value)}.\n{details}"
                        )
                    self._controls()
        except queue.Empty:
            pass
        if self.closing and self.process is None and not self.busy and not self.testing_channels:
            self.root.destroy()
            return
        self.root.after(100, self._poll)

    def _job(self, callback):
        if self.process is not None or self.busy or self.testing_channels:
            return
        self.busy = True
        self._controls()

        def work():
            try:
                callback()
            except Exception as exc:
                self.events.put(("job_done", exc))
            else:
                self.events.put(("job_done", None))

        threading.Thread(target=work, daemon=True).start()

    def export(self):
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Wabot data",
            defaultextension=".wabot.zip",
            initialfile="Wabot-data.wabot.zip",
            filetypes=[("Wabot package", "*.zip")],
        )
        if path:
            self._job(lambda: self.profile.export(Path(path)))

    def _confirm_import(self) -> bool:
        return messagebox.askyesno(
            "Import data",
            "Replace the settings and database with the selected package? The current data is backed up before the import.",
            parent=self.root,
        )

    def import_archive(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Wabot data package", filetypes=[("Wabot package", "*.zip")]
        )
        if path and self._confirm_import():
            self._job(lambda: self.profile.import_archive(Path(path)))

    def import_folder(self):
        path = filedialog.askdirectory(
            parent=self.root, title="Folder with config.json and the bot database"
        )
        if path and self._confirm_import():
            self._job(lambda: self.profile.import_folder(Path(path)))

    def open_data(self):
        self.profile.directory.mkdir(parents=True, exist_ok=True)
        os.startfile(self.profile.directory)

    def on_close(self):
        self.closing = True
        self.stop()
        self._controls()


def launch(profile: DesktopProfile, *, smoke: bool = False) -> None:
    profile.directory.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    if smoke:
        root.withdraw()
    lock = FileLock(str(profile.directory / "launcher.lock"), timeout=0)
    mutex = None
    try:
        lock.acquire()
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel.CreateMutexW.restype = ctypes.c_void_p
            mutex = kernel.CreateMutexW(None, False, "Local\\WabotDesktop")
        Launcher(root, profile)
        if smoke:
            root.after(200, root.destroy)
        root.mainloop()
    except Timeout:
        messagebox.showinfo(
            "Wabot", "The launcher is already running. Check your open windows.", parent=root
        )
        root.destroy()
    finally:
        lock.release()
        if mutex:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(mutex)
