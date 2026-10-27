"""Tk item picker; network work stays outside the GUI thread."""

import queue
import threading
import tkinter as tk
from tkinter import ttk

from wabot.item_catalog import ItemCatalog, change_watchlist, watchlist


class MarketItemsPanel(ttk.Frame):
    def __init__(self, parent, launcher):
        super().__init__(parent, padding=18)
        self.launcher = launcher
        self.catalog = None
        self.loading = False
        self.editable = False
        self.events = queue.Queue()
        self.cache = launcher.profile.directory / "eve-items.json.gz"
        self.query = tk.StringVar()
        self.catalog_status = tk.StringVar(value="Loading EVE catalog…")
        self.result_status = tk.StringVar()
        self.notice = tk.StringVar()
        search = ttk.Frame(self)
        search.pack(fill="x")
        ttk.Label(search, text="Item name or ID:").pack(side="left", padx=(0, 10))
        self.search_entry = ttk.Entry(search, textvariable=self.query)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.refresh_button = ttk.Button(search, text="Update catalog", command=self.refresh)
        self.refresh_button.pack(side="left", padx=(10, 0))
        ttk.Label(self, textvariable=self.catalog_status, foreground="#a6b5cc").pack(
            anchor="w", pady=6
        )
        columns = ttk.Frame(self)
        columns.pack(fill="both", expand=True)
        columns.columnconfigure(0, weight=1, uniform="items")
        columns.columnconfigure(1, weight=1, uniform="items")
        columns.rowconfigure(1, weight=1)
        ttk.Label(columns, text="All published EVE items").grid(row=0, column=0, sticky="w")
        ttk.Label(columns, text="Watched — analysis and reports").grid(row=0, column=1, sticky="w")
        self.results = self._tree(
            columns, 0, ("name", "type_id", "state"), ("Item", "ID", "Market")
        )
        self.monitored = self._tree(
            columns, 1, ("name", "type_id", "state"), ("Item", "ID", "State")
        )
        self.add_button = ttk.Button(columns, text="Add to watched →", command=self.add)
        self.add_button.grid(row=2, column=0, sticky="w", pady=8)
        self.remove_button = ttk.Button(columns, text="Remove from watched", command=self.remove)
        self.remove_button.grid(row=2, column=1, sticky="w", pady=8)
        ttk.Label(self, textvariable=self.result_status, foreground="#a6b5cc").pack(anchor="w")
        ttk.Label(self, textvariable=self.notice, foreground="#fbbf24", wraplength=850).pack(
            anchor="w", pady=6
        )
        ttk.Label(
            self,
            text="Items without a market are listed in the catalog but have no orders to analyse.\n"
            "New items use the current Jita + Perimeter settings, channel and report interval.",
            foreground="#a6b5cc",
            wraplength=850,
        ).pack(anchor="w")
        self.query.trace_add("write", lambda *_: self.search())
        self.results.bind("<<TreeviewSelect>>", lambda _: self._buttons())
        self.monitored.bind("<<TreeviewSelect>>", lambda _: self._buttons())
        self.results.bind("<Double-1>", lambda _: self.add())
        self.reload()
        self._load(False)
        self._poll_id = self.after(100, self._poll)
        self.bind("<Destroy>", self._destroyed, add=True)

    def _destroyed(self, event):
        if event.widget is self:
            self.after_cancel(self._poll_id)

    @staticmethod
    def _tree(parent, column, names, headings):
        frame = ttk.Frame(parent)
        frame.grid(row=1, column=column, sticky="nsew", padx=(0, 10) if column == 0 else 0, pady=8)
        tree = ttk.Treeview(frame, columns=names, show="headings", selectmode="browse", height=13)
        for name, heading in zip(names, headings, strict=True):
            tree.heading(name, text=heading)
            tree.column(
                name, width=245 if name == "name" else 70, minwidth=45, stretch=name == "name"
            )
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        return tree

    def reload(self):
        self.monitored.delete(*self.monitored.get_children())
        for item in watchlist(self.launcher.config):
            self.monitored.insert(
                "",
                "end",
                iid=str(item["type_id"]),
                values=(
                    item["display_name"],
                    item["type_id"],
                    "Enabled" if item["enabled"] else "Disabled",
                ),
            )
        self._buttons()

    def set_editable(self, editable):
        self.editable = editable
        self.notice.set(
            "Changes save immediately. Start the bot after adding items."
            if editable
            else "Stop the bot and wait for the current operation to finish to change the list."
        )
        self._buttons()

    def _buttons(self):
        selected = self.results.selection()
        item = self.catalog.by_id.get(int(selected[0])) if selected and self.catalog else None
        active = {entry["type_id"] for entry in watchlist(self.launcher.config) if entry["enabled"]}
        can_add = self.editable and item and item.marketable and item.type_id not in active
        self.add_button.configure(state="normal" if can_add else "disabled")
        self.remove_button.configure(
            state="normal" if self.editable and self.monitored.selection() else "disabled"
        )

    def search(self):
        self.results.delete(*self.results.get_children())
        if self.catalog is None:
            return
        items, count = self.catalog.search(self.query.get())
        for item in items:
            self.results.insert(
                "",
                "end",
                iid=str(item.type_id),
                values=(item.name, item.type_id, "Yes" if item.marketable else "None"),
            )
        self.result_status.set(
            f"Results: {count}. Shown: {len(items)}."
            + (" Refine the name to narrow the list." if count > len(items) else "")
        )
        self._buttons()

    def add(self):
        selected = self.results.selection()
        if not self.editable or not selected or self.catalog is None:
            return
        item = self.catalog.by_id[int(selected[0])]
        self._change(item.type_id, item=item)

    def remove(self):
        selected = self.monitored.selection()
        if self.editable and selected:
            self._change(int(selected[0]))

    def _change(self, type_id, **kwargs):
        try:
            config = change_watchlist(self.launcher.config, type_id, **kwargs)
            self.launcher.profile.save(config)
            self.launcher.config = config
            self.reload()
            self.launcher._log("Watched item list saved. Changes apply from the next bot start.")
        except Exception as exc:
            self.launcher._error(exc)

    def refresh(self):
        self._load(True)

    def _load(self, refresh):
        if self.loading:
            return
        self.loading = True
        self.refresh_button.configure(state="disabled")
        self.catalog_status.set("Downloading CCP catalog…" if refresh else "Loading EVE catalog…")

        def work():
            try:
                catalog = (
                    ItemCatalog.download(
                        self.cache, lambda message: self.events.put(("progress", message))
                    )
                    if refresh
                    else ItemCatalog.load(self.cache)
                )
                self.events.put(("loaded", catalog))
            except Exception:
                self.events.put(("error", None))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.catalog_status.set(value)
                    continue
                self.loading = False
                self.refresh_button.configure(state="normal")
                if kind == "loaded":
                    self.catalog = value
                    self.catalog_status.set(
                        f"CCP catalog: {len(value.items)} items · downloaded {value.updated_at[:10]}"
                    )
                    self.search()
                else:
                    self.catalog_status.set(
                        "Could not load the catalog. Check your internet connection and click “Update catalog”."
                        if self.catalog is None
                        else "The update failed. The previous catalog is still in use."
                    )
        except queue.Empty:
            pass
        self._poll_id = self.after(100, self._poll)
