"""A headless frontend that records events.

This is a test fixture, not a shipped product -- which is why it lives under
tests/ rather than giflite/ui/. Its job is to be the second implementation of
the frontend contract, so the seam in ARCHITECTURE.md 9 is exercised by
something other than Tk, in CI, with no display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from giflite.app import events as ev
from giflite.app.controller import AppController
from giflite.ui.base import Frontend

ALL_EVENTS = (
    ev.DOC_CHANGED,
    ev.SELECTION_CHANGED,
    ev.PLAYHEAD_MOVED,
    ev.TITLE_CHANGED,
    ev.STATUS,
    ev.ERROR,
)


@dataclass
class Recorded:
    name: str
    payload: dict[str, Any]


@dataclass
class FakeFrontend(Frontend):
    """Subscribes to everything and remembers what arrived, in order."""

    events: list[Recorded] = field(default_factory=list)

    def attach(self, controller: AppController) -> "FakeFrontend":
        for name in ALL_EVENTS:
            controller.events.on(name, self._make_handler(name))
        return self

    def run(self, controller: AppController, initial_path: Path | None = None) -> None:
        self.attach(controller)
        if initial_path is not None:
            controller.open(initial_path)

    # ---- inspection ------------------------------------------------------

    @property
    def names(self) -> list[str]:
        return [e.name for e in self.events]

    def of(self, name: str) -> list[Recorded]:
        return [e for e in self.events if e.name == name]

    def last(self, name: str) -> Recorded | None:
        matches = self.of(name)
        return matches[-1] if matches else None

    def count(self, name: str) -> int:
        return len(self.of(name))

    def clear(self) -> None:
        self.events.clear()

    # ---- internals -------------------------------------------------------

    def _make_handler(self, name: str):
        def handler(**payload: Any) -> None:
            self.events.append(Recorded(name, payload))

        return handler
