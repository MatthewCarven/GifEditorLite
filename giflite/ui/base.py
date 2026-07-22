"""The frontend contract.

Deliberately four lines. There is no widget abstraction layer and no frontend
registry -- a registry with one entry is a lookup table for a decision that
isn't being made yet. `__main__` constructs the frontend directly; that grows
a switch when a second frontend exists (ARCHITECTURE.md 9).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from giflite.app.controller import AppController


class Frontend(ABC):
    @abstractmethod
    def run(self, controller: AppController, initial_path: Path | None = None) -> None:
        """Take over the main thread and drive the controller until quit.

        `initial_path` is opened by the frontend once its window exists, not
        by the caller beforehand. Opening earlier would fire STATUS and ERROR
        into a bus nobody has subscribed to yet, so `giflite missing.gif`
        would fail in total silence.
        """
