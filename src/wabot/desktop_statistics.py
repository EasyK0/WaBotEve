"""Offline statistics corrections with an audit history and no timer rescheduling."""

import asyncio
import queue
import threading
import tkinter as tk
from datetime import UTC, datetime
from tkinter import ttk

from wabot.models import SYSTEMS
from wabot.repository import Repository


class StatisticsPanel(ttk.Frame):
    def __init__(self, parent, launcher):
        super().__init__(parent, padding=10)
        self.launcher = launcher
        self.events = queue.Queue()
        self.loading = False
        self.players = {}
        self.loaded_guild = None
        self.guild = tk.StringVar()
        self.author = tk.StringVar()
        self.name = tk.StringVar()
        self.system = tk.StringVar(value="Kino")
        self.count = tk.StringVar(value="0")
        self.status = tk.StringVar(
            value="Refresh the data, choose a player and set the correct completion count."
        )
        self.totals = tk.StringVar()
        header = ttk.Frame(self)
        header.pack(fill="x")
        self.refresh_button = ttk.Button(header, text="Refresh", command=self.refresh)
        self.refresh_button.pack(side="left")
        ttk.Label(self, textvariable=self.totals).pack(anchor="w", pady=5)
        pages = ttk.Notebook(self)
        pages.pack(fill="both", expand=True)
        self.table = self._table(
            pages,
            "Player scores",
            ("nick", "id", "kino", "niarja", "archee"),
            ("Player", "Discord ID", "Kino", "Niarja", "Archee"),
        )
        self.history = self._table(
            pages,
            "Recent corrections",
            ("date", "nick", "system", "before", "after"),
            ("Time UTC", "Player", "System", "Before", "After"),
        )
        self.table.bind("<<TreeviewSelect>>", self.select)
        form = ttk.Frame(self)
        form.pack(fill="x", pady=7)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        self.controls = [self.refresh_button]
        for label, variable, row, column in (
            ("Player ID", self.author, 0, 0),
            ("Name", self.name, 0, 2),
            ("Completions", self.count, 1, 2),
        ):
            ttk.Label(form, text=label).grid(row=row, column=column, padx=5, pady=4, sticky="w")
            entry = ttk.Entry(form, textvariable=variable, width=18)
            entry.grid(row=row, column=column + 1, sticky="ew", padx=5, pady=4)
            self.controls.append(entry)
        ttk.Label(form, text="System").grid(row=1, column=0, padx=5, sticky="w")
        self.system_choice = ttk.Combobox(
            form,
            textvariable=self.system,
            values=tuple(SYSTEMS.values()),
            state="readonly",
            width=16,
        )
        self.system_choice.grid(row=1, column=1, sticky="ew", padx=5)
        self.system_choice.bind("<<ComboboxSelected>>", lambda _: self._count())
        buttons = ttk.Frame(self)
        buttons.pack(fill="x")
        self.save_button = ttk.Button(buttons, text="Save correction", command=self.save)
        self.save_button.pack(side="left")
        self.new_button = ttk.Button(buttons, text="Add player manually", command=self.new)
        self.new_button.pack(side="left", padx=8)
        self.controls.extend((self.save_button, self.new_button))
        ttk.Label(self, textvariable=self.status, foreground="#fbbf24", wraplength=740).pack(
            anchor="w", pady=5
        )
        ttk.Label(
            self,
            text="Corrections update /stats and /rank. A count of 0 resets the player's score in the chosen system.\n"
            "You edit the texts of these commands on the Messages / pings page.",
            wraplength=740,
        ).pack(anchor="w")
        self.reload()
        self._poll_id = self.after(100, self._poll)
        self.bind("<Destroy>", self._destroy, add=True)

    def _destroy(self, event):
        if event.widget is self:
            self.after_cancel(self._poll_id)

    @staticmethod
    def _table(parent, title, columns, labels):
        frame = ttk.Frame(parent)
        parent.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=5, selectmode="browse")
        for key, label in zip(columns, labels, strict=True):
            tree.heading(key, text=label)
            tree.column(key, width=170 if key in ("nick", "id", "date") else 65, minwidth=45)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        return tree

    def reload(self):
        self.guild.set(self.launcher.current_guild or "")
        self.loaded_guild = None
        self.players = {}
        self.table.delete(*self.table.get_children())
        self.history.delete(*self.history.get_children())
        self.totals.set("Click Refresh to load the current scores.")

    def set_editable(self, editable):
        for control in self.controls:
            control.configure(state="normal" if editable else "disabled")
        for choice in (self.system_choice,):
            choice.configure(state="readonly" if editable else "disabled")

    def select(self, _event=None):
        selected = self.table.selection()
        if selected and selected[0] in self.players:
            player = self.players[selected[0]]
            self.author.set(player["author_id"])
            self.name.set(player["author_name"])
            self._count()

    def _count(self):
        self.count.set(
            str(self.players.get(self.author.get(), {}).get(self.system.get().lower(), 0))
        )

    def new(self):
        self.table.selection_remove(*self.table.selection())
        self.author.set("")
        self.name.set("")
        self.count.set("0")

    def refresh(self):
        self._job()

    def save(self):
        try:
            if self.loaded_guild != self.guild.get():
                raise ValueError("Refresh the scores of the selected server first.")
            system = self.system.get().lower()
            author = self.author.get().strip()
            self._job(
                {
                    "author_id": author,
                    "author_name": self.name.get().strip(),
                    "system": system,
                    "count": int(self.count.get()),
                    "expected_count": self.players.get(author, {}).get(system, 0),
                }
            )
        except Exception as exc:
            self.launcher._error(exc)

    def _job(self, correction=None):
        if self.launcher.process is not None or self.launcher.busy or self.launcher.closing:
            self.status.set("Stop the bot before editing statistics.")
            return
        guild = self.guild.get()
        if guild not in self.launcher.config.get("guild_ids", []):
            self.status.set("Save the server ID in the settings first.")
            return
        self.loading = self.launcher.busy = True
        self.launcher._controls()
        self.status.set("Saving correction…" if correction is not None else "Loading statistics…")

        async def operation():
            repository = Repository(self.launcher.profile.database_path)
            try:
                await repository.initialize()
                if correction is not None:
                    await repository.set_statistic(guild_id=guild, **correction)
                return await repository.statistics_editor(guild)
            finally:
                await repository.close()

        def work():
            try:
                with self.launcher.profile.stopped():
                    result = asyncio.run(operation())
                self.events.put((guild, result, correction is not None, None))
            except Exception as exc:
                self.events.put((guild, None, False, exc))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            guild, result, changed, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.loading = self.launcher.busy = False
            self.launcher._controls()
            if error is not None:
                self.status.set("The operation failed. Refresh the data and try again.")
                self.launcher._error(error)
            else:
                rows, history = result
                self.loaded_guild = guild
                self.players = {row["author_id"]: row for row in rows}
                self.table.delete(*self.table.get_children())
                self.history.delete(*self.history.get_children())
                for row in rows:
                    self.table.insert(
                        "",
                        "end",
                        iid=row["author_id"],
                        values=(row["author_name"], row["author_id"], *(row[s] for s in SYSTEMS)),
                    )
                for row in history:
                    when = datetime.fromtimestamp(row["created_at"], UTC).strftime("%Y-%m-%d %H:%M")
                    self.history.insert(
                        "",
                        "end",
                        values=(
                            when,
                            row["author_name"],
                            SYSTEMS[row["system"]],
                            row["before_count"],
                            row["after_count"],
                        ),
                    )
                totals = {key: sum(row[key] for row in rows) for key in SYSTEMS}
                self.totals.set(
                    " · ".join(f"{SYSTEMS[key]}: {count}" for key, count in totals.items())
                    + f" · Total: {sum(totals.values())}"
                )
                self.status.set(
                    "Correction saved. The score will show in /stats and /rank."
                    if changed
                    else "Scores refreshed."
                )
        self._poll_id = self.after(100, self._poll)
