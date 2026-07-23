"""File format readers and writers.

For now a plain dict, not a registry: there is exactly one reader. It becomes
a registry at M3/M4 when the second and third formats arrive (ARCHITECTURE.md 8).

The one thing to preserve through that promotion: a format whose optional
dependency is missing must not break startup. Its module guards the import and
simply doesn't register itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from giflite.core.io.gif_read import read_gif
from giflite.core.io.gif_write import write_gif
from giflite.core.model import Document

Reader = Callable[[Path], Document]
Writer = Callable[[Document, Path], None]

READERS: dict[str, Reader] = {".gif": read_gif}
WRITERS: dict[str, Writer] = {".gif": write_gif}


def reader_for(path: Path) -> Reader | None:
    return READERS.get(Path(path).suffix.lower())


def writer_for(path: Path) -> Writer | None:
    return WRITERS.get(Path(path).suffix.lower())


def open_filter() -> list[tuple[str, str]]:
    """Tk-style (label, pattern) pairs, generated rather than hardcoded."""
    patterns = " ".join(f"*{ext}" for ext in sorted(READERS))
    return [("Animations", patterns), ("All files", "*.*")]


def save_filter() -> list[tuple[str, str]]:
    patterns = " ".join(f"*{ext}" for ext in sorted(WRITERS))
    return [("GIF", patterns), ("All files", "*.*")]
