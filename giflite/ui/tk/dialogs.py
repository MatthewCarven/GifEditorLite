"""A dialog generated from an operation's `Param` list.

This is what the M2 hand-written duplicate dialog was always heading towards.
The op declares its parameters as data (core/params.py); this walks that data,
builds one widget per param, and hands back coerced values. Add a param to any
op and its dialog exists with no code here changing.

Widget per type: Bool -> Checkbutton, Choice -> read-only Combobox, Int ->
Spinbox with the declared range, Float -> Entry. Values come back through
`Param.coerce`, so the op never parses a raw string.

Construction is non-blocking (built hidden); `ask_params` handles the modal
show/grab/wait, which also keeps the dialog unit-testable without a mainloop.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from giflite.core.model import Document, Selection
from giflite.core.ops import Operation, op_defaults, op_params
from giflite.core.params import BoolParam, ChoiceParam, IntParam, Param


class ParamDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str,
                 params: tuple[Param, ...], defaults: dict[str, Any]) -> None:
        super().__init__(parent)
        self.withdraw()  # build hidden; ask_params reveals it
        self.title(title)
        self.resizable(False, False)
        self.result: dict[str, Any] | None = None
        self._params = params
        self._getters: dict[str, Callable[[], Any]] = {}

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        for row, param in enumerate(params):
            label = param.label
            unit = getattr(param, "unit", "")
            if unit:
                label += f" ({unit})"
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
            widget, getter = self._build_widget(body, param, defaults.get(param.name, None))
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            self._getters[param.name] = getter

        buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="right", padx=(0, 6))
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())

    def _build_widget(self, parent, param: Param, value):
        if isinstance(param, BoolParam):
            var = tk.BooleanVar(value=bool(param.default if value is None else value))
            return ttk.Checkbutton(parent, variable=var), var.get
        if isinstance(param, ChoiceParam):
            current = param.default if value is None else value
            var = tk.StringVar(value=param.label_for(current))
            combo = ttk.Combobox(parent, values=list(param.labels),
                                 textvariable=var, state="readonly", width=22)
            return combo, var.get
        # Int / Float
        var = tk.StringVar(value=str(param.default if value is None else value))
        if isinstance(param, IntParam):
            lo = param.min if param.min is not None else 0
            hi = param.max if param.max is not None else 999999
            widget = ttk.Spinbox(parent, from_=lo, to=hi, textvariable=var, width=12)
        else:
            widget = ttk.Entry(parent, textvariable=var, width=12)
        return widget, var.get

    def _ok(self) -> None:
        self.result = {
            p.name: p.coerce(self._getters[p.name]()) for p in self._params
        }
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


def ask_values(parent: tk.Misc, title: str, params: tuple[Param, ...],
               defaults: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Prompt for any `Param` list. Returns coerced values, `{}` when there are
    no params, or None if the user cancels.

    Decoupled from operations because operations stopped being the only thing
    with parameters: a format's reader declares what the source can't tell it
    (a delay for a folder of stills, an fps for a video later). The schema was
    always general; only this function's signature was not.
    """
    if not params:
        return {}
    dialog = ParamDialog(parent, title, params, defaults or {})
    dialog.transient(parent)
    dialog.deiconify()
    dialog.grab_set()
    dialog.wait_window()
    return dialog.result


def ask_params(parent: tk.Misc, op: Operation,
               doc: Document, sel: Selection) -> dict[str, Any] | None:
    """Prompt for an op's params -- `ask_values` with the op's own defaults,
    which may be seeded from the document (`default_params`)."""
    return ask_values(parent, op.label, op_params(op), op_defaults(op, doc, sel))
