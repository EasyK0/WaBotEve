"""Launcher look: one dark palette, ttk styles, cards and a toggle switch."""

import tkinter as tk
from tkinter import ttk

BG = "#0d0d0f"
SIDEBAR = "#070708"
CARD = "#16161a"
CARD_BORDER = "#2a2a30"
FIELD = "#0a0a0c"
TEXT = "#f1f1f3"
MUTED = "#a1a1aa"
ACCENT = "#dc2626"
ACCENT_HOVER = "#ef4444"
SECONDARY = "#26262b"
SECONDARY_HOVER = "#34343b"
SUCCESS = "#22c55e"
WARNING = "#f59e0b"
DANGER = "#ef4444"
DISABLED = "#5c5c66"
FONT = "Segoe UI"
TEXT_AREA = {
    "font": (FONT, 10),
    "bg": FIELD,
    "fg": TEXT,
    "insertbackground": TEXT,
    "relief": "flat",
    "padx": 10,
    "pady": 8,
    "highlightthickness": 1,
    "highlightbackground": CARD_BORDER,
    "highlightcolor": ACCENT,
}


def apply(root: tk.Misc) -> ttk.Style:
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".",
        background=BG,
        foreground=TEXT,
        font=(FONT, 10),
        bordercolor=CARD_BORDER,
        lightcolor=CARD_BORDER,
        darkcolor=CARD_BORDER,
        troughcolor=FIELD,
        focuscolor=ACCENT,
        selectbackground=ACCENT,
        selectforeground="white",
    )
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Title.TLabel", font=(FONT, 18, "bold"))
    style.configure("Sidebar.TFrame", background=SIDEBAR)
    style.configure("Header.TFrame", background=SIDEBAR)
    style.configure("Header.TLabel", background=SIDEBAR, foreground=TEXT)
    style.configure("Logo.TLabel", background=SIDEBAR, foreground=TEXT, font=(FONT, 20, "bold"))
    style.configure("HeaderMuted.TLabel", background=SIDEBAR, foreground=MUTED)
    style.configure(
        "Section.TLabel", background=SIDEBAR, foreground=DISABLED, font=(FONT, 8, "bold")
    )
    style.configure("Card.TFrame", background=CARD, relief="solid", borderwidth=1)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT)
    style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
    style.configure("CardTitle.TLabel", background=CARD, foreground=TEXT, font=(FONT, 12, "bold"))
    style.configure("Card.TCheckbutton", background=CARD, foreground=TEXT)
    style.configure(
        "TButton",
        padding=(14, 8),
        background=SECONDARY,
        foreground=TEXT,
        borderwidth=0,
        focusthickness=0,
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("disabled", "#18181c"), ("active", SECONDARY_HOVER)],
        foreground=[("disabled", DISABLED)],
    )
    for name, color, hover in (
        ("Accent.TButton", ACCENT, ACCENT_HOVER),
        ("Start.TButton", ACCENT, ACCENT_HOVER),
        ("Danger.TButton", "#2a1214", "#3d171a"),
    ):
        style.configure(name, background=color, foreground="white")
        style.map(
            name,
            background=[("disabled", "#18181c"), ("active", hover)],
            foreground=[("disabled", DISABLED)],
        )
    style.configure("Danger.TButton", foreground="#fca5a5")
    style.configure(
        "Nav.TButton",
        background=SIDEBAR,
        foreground=MUTED,
        anchor="w",
        padding=(18, 9),
        font=(FONT, 10),
    )
    style.map("Nav.TButton", background=[("active", "#1a0b0d")], foreground=[("active", TEXT)])
    style.configure(
        "NavActive.TButton",
        background="#2a0f12",
        foreground="white",
        anchor="w",
        padding=(18, 9),
        font=(FONT, 10, "bold"),
    )
    style.map("NavActive.TButton", background=[("active", "#2a0f12")])
    style.configure(
        "TEntry",
        fieldbackground=FIELD,
        foreground=TEXT,
        insertcolor=TEXT,
        padding=7,
        bordercolor=CARD_BORDER,
    )
    style.map("TEntry", bordercolor=[("focus", ACCENT)], fieldbackground=[("disabled", BG)])
    style.configure(
        "TCombobox",
        fieldbackground=FIELD,
        background=SECONDARY,
        foreground=TEXT,
        arrowcolor=TEXT,
        padding=6,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", FIELD), ("disabled", BG)],
        foreground=[("readonly", TEXT), ("disabled", DISABLED)],
        selectbackground=[("readonly", FIELD)],
        selectforeground=[("readonly", TEXT)],
        bordercolor=[("focus", ACCENT)],
    )
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))
    style.configure("TCheckbutton", background=BG, foreground=TEXT, indicatorcolor=FIELD)
    style.map(
        "TCheckbutton",
        indicatorcolor=[("selected", ACCENT)],
        background=[("active", BG)],
    )
    style.map("Card.TCheckbutton", background=[("active", CARD)])
    style.configure(
        "Card.TRadiobutton",
        background=CARD,
        foreground=TEXT,
        indicatorbackground=FIELD,
        indicatorforeground=FIELD,
        upperbordercolor=MUTED,
        lowerbordercolor=MUTED,
    )
    style.map(
        "Card.TRadiobutton",
        background=[("active", CARD)],
        indicatorbackground=[("selected", ACCENT)],
        indicatorforeground=[("selected", "white")],
        foreground=[("disabled", DISABLED)],
    )
    style.configure("Kill.TLabel", background=CARD, foreground=SUCCESS, font=(FONT, 10, "bold"))
    style.configure("Loss.TLabel", background=CARD, foreground=DANGER, font=(FONT, 10, "bold"))
    # The sidebar replaces visible notebook tabs.
    style.layout("Pages.TNotebook.Tab", [])
    style.configure(
        "Pages.TNotebook",
        background=BG,
        borderwidth=0,
        tabmargins=0,
        bordercolor=BG,
        lightcolor=BG,
        darkcolor=BG,
    )
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure(
        "TNotebook.Tab", padding=(16, 8), background=SECONDARY, foreground=MUTED, borderwidth=0
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", ACCENT)],
        foreground=[("selected", "white")],
    )
    style.configure(
        "Treeview",
        background=FIELD,
        fieldbackground=FIELD,
        foreground=TEXT,
        rowheight=28,
        borderwidth=0,
    )
    style.configure(
        "Treeview.Heading",
        background=SECONDARY,
        foreground=TEXT,
        relief="flat",
        font=(FONT, 10, "bold"),
    )
    style.map("Treeview.Heading", background=[("active", SECONDARY_HOVER)])
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar",
            background=SECONDARY,
            troughcolor=BG,
            arrowcolor=MUTED,
            borderwidth=0,
        )
        style.map(f"{orient}.TScrollbar", background=[("active", SECONDARY_HOVER)])
    return style


def card(parent, title=None, subtitle=None, *, padding=18):
    """A bordered panel; returns the frame to place content in."""
    frame = ttk.Frame(parent, style="Card.TFrame", padding=padding)
    if title:
        ttk.Label(frame, text=title, style="CardTitle.TLabel").pack(anchor="w")
    if subtitle:
        ttk.Label(frame, text=subtitle, style="CardMuted.TLabel", wraplength=760).pack(
            anchor="w", pady=(2, 10)
        )
    return frame


class ToggleSwitch(tk.Canvas):
    """A pill switch bound to a BooleanVar; `command` runs after a user click."""

    def __init__(self, parent, variable, command=None, *, background=CARD):
        super().__init__(
            parent, width=46, height=24, bg=background, highlightthickness=0, cursor="hand2"
        )
        self.variable = variable
        self.command = command
        self.state = "normal"
        self.bind("<Button-1>", lambda _: self.invoke())
        self._trace = variable.trace_add("write", lambda *_: self._draw())
        self.bind("<Destroy>", self._destroy, add=True)
        self._draw()

    def _destroy(self, event):
        if event.widget is self:
            try:
                self.variable.trace_remove("write", self._trace)
            except tk.TclError:
                pass

    def configure(self, cnf=None, **kwargs):
        if "state" in kwargs:
            self.state = kwargs.pop("state")
            self._draw()
        if cnf or kwargs:
            return super().configure(cnf, **kwargs)
        return None

    config = configure

    def invoke(self):
        if self.state == "disabled":
            return
        self.variable.set(not self.variable.get())
        if self.command is not None:
            self.command()

    def _draw(self):
        self.delete("all")
        on = bool(self.variable.get())
        color = (ACCENT if on else "#3f3f46") if self.state != "disabled" else "#26262b"
        self.create_oval(1, 1, 23, 23, fill=color, outline=color)
        self.create_oval(23, 1, 45, 23, fill=color, outline=color)
        self.create_rectangle(12, 1, 34, 23, fill=color, outline=color)
        x = 25 if on else 3
        knob = "white" if self.state != "disabled" else "#71717a"
        self.create_oval(x, 3, x + 18, 21, fill=knob, outline=knob)
