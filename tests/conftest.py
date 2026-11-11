"""Use one Tcl interpreter, as the real launcher does, with isolated test windows."""

import gc
import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def tk_application():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()
    gc.collect()


@pytest.fixture
def tk_window(tk_application):
    window = tk.Toplevel(tk_application)
    window.withdraw()
    yield window
    window.destroy()
    # Cancel callbacks owned by the destroyed test window before the next test.
    for callback in tk_application.tk.call("after", "info"):
        tk_application.after_cancel(callback)
    gc.collect()
