"""The one or two dialogs M2 needs.

Deliberately not a generic dialog builder. The declarative `Param` schema and
its auto-generated dialogs are deferred to M3, where writer options (dither,
quality, loop count) make the machinery pay for itself. Until then, the single
op that needs input gets a single hand-written prompt (ARCHITECTURE.md 6).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import simpledialog


def ask_duplicate_count(parent: tk.Misc) -> int | None:
    """How many copies of each selected frame? None if the user cancels."""
    return simpledialog.askinteger(
        "Duplicate Frames",
        "Number of copies:",
        parent=parent,
        minvalue=1,
        maxvalue=999,
        initialvalue=1,
    )
