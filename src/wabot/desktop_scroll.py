"""Scrollable settings pages so controls remain reachable on smaller displays."""

import tkinter as tk
from tkinter import ttk


class ScrollablePage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg="#101827", highlightthickness=0, width=1, height=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.body = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._layout)
        self.canvas.bind("<Configure>", self._layout)
        self.top = self.winfo_toplevel()
        self.wheel_binding = self.top.bind("<MouseWheel>", self._wheel, add=True)
        self.bind("<Destroy>", self._destroy, add=True)

    def _layout(self, _event=None):
        self.canvas.itemconfigure(
            self.window,
            width=max(self.canvas.winfo_width(), self.body.winfo_reqwidth()),
            height=max(self.canvas.winfo_height(), self.body.winfo_reqheight()),
        )
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _wheel(self, event):
        if str(event.widget).startswith(str(self) + ".") and event.widget.winfo_class() not in (
            "Text",
            "Treeview",
        ):
            self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def _destroy(self, event):
        if event.widget is self:
            self.top.unbind("<MouseWheel>", self.wheel_binding)
