"""Settings editor with local previews; editing never sends a Discord message."""

import tkinter as tk
from tkinter import scrolledtext, ttk

from wabot import desktop_theme as theme
from wabot.messages import TEMPLATES, MessageSettings


class MessagesPanel(ttk.Frame):
    def __init__(self, parent, launcher):
        super().__init__(parent, padding=12)
        self.launcher = launcher
        self.key = "timer_reminder"
        self.drafts = {}
        self.editable = True
        self.choice = tk.StringVar(value=TEMPLATES[self.key].label)
        self.help = tk.StringVar()
        self.notice = tk.StringVar()
        self.selector = ttk.Combobox(
            self,
            textvariable=self.choice,
            values=[t.label for t in TEMPLATES.values()],
            state="readonly",
        )
        self.selector.pack(fill="x")
        self.selector.bind("<<ComboboxSelected>>", self.select)
        ttk.Label(self, textvariable=self.help, wraplength=770, foreground="#a6b5cc").pack(
            anchor="w", pady=6
        )
        self.editor = scrolledtext.ScrolledText(self, height=5, wrap="word", **theme.TEXT_AREA)
        self.editor.pack(fill="both", expand=True)
        self.editor.bind("<KeyRelease>", lambda _: self.preview())
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=7)
        self.save_button = ttk.Button(
            buttons, text="Save messages", style="Accent.TButton", command=self.save
        )
        self.save_button.pack(side="left")
        self.reset_button = ttk.Button(buttons, text="Restore default text", command=self.reset)
        self.reset_button.pack(side="left", padx=8)
        ttk.Label(self, text="Preview with sample data (nothing is sent):").pack(anchor="w")
        self.output = scrolledtext.ScrolledText(
            self, height=4, wrap="word", state="disabled", **theme.TEXT_AREA
        )
        self.output.pack(fill="both", expand=True, pady=(4, 0))
        ttk.Label(self, textvariable=self.notice, foreground="#fbbf24", wraplength=770).pack(
            anchor="w", pady=4
        )
        self.reload()

    def reload(self):
        messages = MessageSettings.from_config(self.launcher.config.get("messages"))
        self.drafts = {key: messages.text(key) for key in TEMPLATES}
        self._show()

    def _show(self):
        self.editor.configure(state="normal")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self.drafts[self.key])
        self.editor.configure(state="normal" if self.editable else "disabled")
        spec = TEMPLATES[self.key]
        fields = " ".join("{" + key + "}" for key in spec.examples) or "none"
        hint = (
            " Remove {mention} to send the timer without a ping."
            if self.key == "timer_reminder"
            else ""
        )
        self.help.set(f"Placeholders: {fields}. Max {spec.limit} characters.{hint}")
        self.preview()

    def select(self, _event=None):
        self.drafts[self.key] = self.editor.get("1.0", "end-1c")
        self.key = next(key for key, spec in TEMPLATES.items() if spec.label == self.choice.get())
        self._show()

    def preview(self):
        value = self.editor.get("1.0", "end-1c")
        try:
            settings = MessageSettings({self.key: value})
            text = settings.render(self.key, **TEMPLATES[self.key].examples)
            self.notice.set("Changes are used after you save and start the bot.")
        except ValueError as exc:
            text = ""
            self.notice.set(str(exc))
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.output.configure(state="disabled")

    def reset(self):
        if not self.editable:
            return
        self.drafts[self.key] = TEMPLATES[self.key].default
        self._show()

    def set_editable(self, editable):
        self.editable = editable
        self.editor.configure(state="normal" if editable else "disabled")
        self.selector.configure(state="readonly" if editable else "disabled")
        for button in (self.save_button, self.reset_button):
            button.configure(state="normal" if editable else "disabled")

    def save(self):
        if self.launcher.process is not None or self.launcher.busy or self.launcher.closing:
            self.notice.set("Stop the bot before changing messages.")
            return
        self.drafts[self.key] = self.editor.get("1.0", "end-1c")
        try:
            changes = {
                key: text for key, text in self.drafts.items() if text != TEMPLATES[key].default
            }
            MessageSettings(changes)
            config = dict(self.launcher.config, messages=changes)
            self.launcher.profile.save(config)
            self.launcher.config = config
            self.notice.set("Messages saved. Start the bot to use the new texts.")
        except Exception as exc:
            self.launcher._error(exc)
