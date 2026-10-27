"""PvP controls: Pochven system channels and the alliance killmail feed, one card each."""

import tkinter as tk
from dataclasses import asdict
from tkinter import ttk

from wabot import desktop_theme as theme
from wabot.pvp_config import ROUTE_SYSTEMS, PvpSettings

PING_CHOICES = (
    ("none", "No ping"),
    ("everyone", "@everyone"),
    ("role", "Selected role"),
)
CHANNEL_HINT = "Channel ID: in Discord, right-click the channel and choose “Copy Channel ID”."


def _isk_text(value):
    return f"{value:,}".replace(",", " ")


def _isk_value(text, label):
    digits = text.replace(" ", "").replace(" ", "").replace(",", "").replace(".", "")
    if not digits.isdigit():
        raise ValueError(f"{label}: enter an ISK amount, e.g. 1 000 000 000.")
    return int(digits)


def _alliance_ids(text):
    return [item.strip() for item in text.replace(";", ",").split(",") if item.strip()]


class PvpPanel(ttk.Frame):
    def __init__(self, parent, launcher):
        super().__init__(parent, padding=(0, 0, 18, 18))
        self.launcher = launcher
        self.ping = tk.StringVar()
        self.role = tk.StringVar()
        self.notice = tk.StringVar()
        self.channels = {system: tk.StringVar() for system in ROUTE_SYSTEMS}
        self.pochven_channel = tk.StringVar()
        self.alliances = tk.StringVar()
        self.alliance_channel = tk.StringVar()
        self.here_min = tk.StringVar()
        self.here_loss = tk.StringVar()
        self.pochven_state = tk.StringVar()
        self.alliance_state = tk.StringVar()
        self.controls = []
        self.columnconfigure(0, weight=1)

        pochven = theme.card(
            self,
            "Pochven killmails",
            "Every kill and loss in a chosen system goes to its channel, "
            "checked every 30 seconds. All Pochven gets kills from every Pochven system. "
            "An empty field turns it off. Several fields can share one channel.",
        )
        pochven.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(pochven, textvariable=self.pochven_state, style="CardMuted.TLabel").pack(
            anchor="w", pady=(0, 8)
        )
        systems = ttk.Frame(pochven, style="Card.TFrame", borderwidth=0)
        systems.pack(fill="x")
        systems.columnconfigure(1, weight=1)
        for row, system in enumerate(ROUTE_SYSTEMS):
            ttk.Label(
                systems, text=system, style="Card.TLabel", font=(theme.FONT, 10, "bold")
            ).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=4)
            field = ttk.Entry(systems, textvariable=self.channels[system])
            field.grid(row=row, column=1, sticky="ew", pady=4)
            self.controls.append(field)
        ttk.Label(
            systems, text="All Pochven", style="Card.TLabel", font=(theme.FONT, 10, "bold")
        ).grid(row=len(ROUTE_SYSTEMS), column=0, sticky="w", padx=(0, 18), pady=4)
        field = ttk.Entry(systems, textvariable=self.pochven_channel)
        field.grid(row=len(ROUTE_SYSTEMS), column=1, sticky="ew", pady=4)
        self.controls.append(field)
        ttk.Label(pochven, text=CHANNEL_HINT, style="CardMuted.TLabel", wraplength=760).pack(
            anchor="w", pady=(6, 14)
        )
        ttk.Label(pochven, text="Ping on Pochven killmails", style="Card.TLabel").pack(anchor="w")
        pings = ttk.Frame(pochven, style="Card.TFrame", borderwidth=0)
        pings.pack(fill="x", pady=(4, 0))
        self.ping_buttons = []
        for value, label in PING_CHOICES:
            button = ttk.Radiobutton(
                pings,
                text=label,
                value=value,
                variable=self.ping,
                style="Card.TRadiobutton",
                command=self._ping_changed,
            )
            button.pack(side="left", padx=(0, 18))
            self.ping_buttons.append(button)
        self.role_entry = ttk.Entry(pings, textvariable=self.role, width=24)
        self.role_entry.pack(side="left")
        self.role_label = ttk.Label(pings, text="Role ID", style="CardMuted.TLabel")
        self.role_label.pack(side="left", padx=8)

        alliances = theme.card(
            self,
            "Alliance kills and losses",
            "Killmails of chosen alliances from all of EVE on a separate channel, as a card with the victim, "
            "ship, value and attackers. Empty alliance IDs turn this off.",
        )
        alliances.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(alliances, textvariable=self.alliance_state, style="CardMuted.TLabel").pack(
            anchor="w", pady=(0, 8)
        )
        form = ttk.Frame(alliances, style="Card.TFrame", borderwidth=0)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        for row, label, variable, hint in (
            (0, "Alliance IDs", self.alliances, "separate with commas, e.g. 99003581, 1354830081"),
            (2, "Discord channel", self.alliance_channel, None),
        ):
            ttk.Label(form, text=label, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 18), pady=(4, 0)
            )
            field = ttk.Entry(form, textvariable=variable)
            field.grid(row=row, column=1, sticky="ew", pady=(4, 0))
            self.controls.append(field)
            if hint:
                ttk.Label(form, text=hint, style="CardMuted.TLabel").grid(
                    row=row + 1, column=1, sticky="w", pady=(2, 4)
                )
        ttk.Label(alliances, text="Ping @here from value (ISK)", style="Card.TLabel").pack(
            anchor="w", pady=(14, 4)
        )
        thresholds = ttk.Frame(alliances, style="Card.TFrame", borderwidth=0)
        thresholds.pack(fill="x")
        for column, label, style, variable in (
            (0, "Kill", "Kill.TLabel", self.here_min),
            (2, "Loss", "Loss.TLabel", self.here_loss),
        ):
            ttk.Label(thresholds, text=label, style=style).grid(
                row=0, column=column, sticky="w", padx=(0 if column == 0 else 24, 10)
            )
            field = ttk.Entry(thresholds, textvariable=variable, width=18)
            field.grid(row=0, column=column + 1, sticky="w")
            self.controls.append(field)

        actions = ttk.Frame(self)
        actions.grid(row=2, column=0, sticky="ew")
        self.save_button = ttk.Button(
            actions, text="Save PvP settings", style="Accent.TButton", command=self.save
        )
        self.save_button.pack(side="left")
        self.test_button = ttk.Button(
            actions,
            text="Send test to saved channels",
            command=lambda: self.launcher.test_channels(pvp_only=True),
        )
        self.test_button.pack(side="left", padx=10)
        self.controls.append(self.save_button)
        ttk.Label(self, textvariable=self.notice, foreground=theme.WARNING, wraplength=820).grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Label(
            self,
            textvariable=self.launcher.channel_test_notice,
            wraplength=820,
            style="Muted.TLabel",
        ).grid(row=4, column=0, sticky="w", pady=(4, 0))
        self.editable = True
        self.reload()

    def _ping_changed(self):
        role = self.editable and self.ping.get() == "role"
        self.role_entry.configure(state="normal" if role else "disabled")

    def _system_channels(self):
        return {
            system: variable.get().strip()
            for system, variable in self.channels.items()
            if variable.get().strip()
        }

    def _refresh_state(self, config):
        systems = ", ".join(
            [*config.system_channels, *(["All Pochven"] if config.pochven_channel else [])]
        )
        systems = systems or "none"
        self.pochven_state.set(f"Saved systems: {systems}")
        if config.alliance_feed_active:
            count = len(config.alliance_ids)
            self.alliance_state.set(f"On: {count} alliance(s) on channel {config.alliance_channel}")
        else:
            self.alliance_state.set("Off")

    def reload(self):
        server = self.launcher.current_server()
        config = PvpSettings.from_config(server["pvp"] if server else {})
        self.ping.set(config.ping)
        self.role.set(config.role_id or "")
        for system, variable in self.channels.items():
            variable.set(config.system_channels.get(system, ""))
        self.pochven_channel.set(config.pochven_channel or "")
        self.alliances.set(", ".join(config.alliance_ids))
        self.alliance_channel.set(config.alliance_channel or "")
        self.here_min.set(_isk_text(config.here_min_value))
        self.here_loss.set(_isk_text(config.alliance_here_loss_value))
        self.notice.set("")
        self._refresh_state(config)
        self._ping_changed()

    def set_editable(self, editable):
        self.editable = editable
        for control in self.controls:
            control.configure(state="normal" if editable else "disabled")
        for button in self.ping_buttons:
            button.configure(state="normal" if editable else "disabled")
        self._ping_changed()

    def save(self):
        if self.launcher.process is not None or self.launcher.busy or self.launcher.closing:
            self.notice.set("Stop the bot before changing PvP settings.")
            return
        try:
            pvp = PvpSettings(
                ping=self.ping.get(),
                role_id=self.role.get().strip() or None,
                system_channels=self._system_channels(),
                pochven_channel=self.pochven_channel.get().strip() or None,
                alliance_ids=_alliance_ids(self.alliances.get()),
                alliance_channel=self.alliance_channel.get().strip() or None,
                here_min_value=_isk_value(self.here_min.get(), "@here on kill"),
                alliance_here_loss_value=_isk_value(self.here_loss.get(), "@here on loss"),
            )
            if pvp.ping == "role" and pvp.role_id is None:
                raise ValueError("Enter a role ID or choose another ping.")
            values = asdict(pvp)
            values.pop("enabled")
            self.launcher.update_server(lambda server: server.update(pvp=values))
            self._refresh_state(pvp)
            self.notice.set("Saved. The PvP settings apply when the bot starts.")
        except Exception as exc:
            self.launcher._error(exc)
