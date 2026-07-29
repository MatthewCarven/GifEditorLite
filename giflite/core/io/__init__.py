"""File format readers and writers.

Was a plain dict keyed by file extension, with a note saying it becomes a
registry when the second and third formats arrive. Image sequences are what
forced it, and not because there are simply more formats now -- the dict
would have taken `.webp` and `.apng` without complaint. A *folder* is what it
cannot express: `READERS[path.suffix]` has nothing to look up when the source
has no suffix, and no amount of extra keys fixes that.

So dispatch moves from "index by extension" to "ask each format whether it
handles this path" (ARCHITECTURE.md 25.1). Formats are data, and the questions a
frontend actually needs -- what can I open, is this one a folder, is it
available at all -- are answered from that data rather than hardcoded beside it.

The guarantee carried over from the dict: **a format whose optional dependency
is missing must not break startup.** It registers with an `available` callable
and simply reports False; nothing imports its dependency at module scope. Video
import at M5 is the first real customer, but the promise predates it and the
filters already honour it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from giflite.core.io.gif_read import read_gif
from giflite.core.io.gif_write import write_gif
from giflite.core.io.sequence import read_sequence, write_sequence
from giflite.core.model import MIN_DURATION_MS, Document
from giflite.core.params import IntParam, Param

Reader = Callable[..., Document]
Writer = Callable[..., None]


def _always() -> bool:
    return True


@dataclass(frozen=True)
class Format:
    """One thing we can read and/or write.

    `extensions` is empty for a folder format -- which is precisely why the
    old dict couldn't hold one, and why `is_folder` is a field rather than
    something inferred from an empty tuple. Inferring it would make "a format
    with no extensions" mean "folder" by accident, and the next format with an
    unusual shape would inherit that meaning by surprise.
    """

    id: str
    label: str
    extensions: tuple[str, ...] = ()
    is_folder: bool = False
    read: Reader | None = None
    write: Writer | None = None
    # Options the reader accepts, declared as data so a frontend can generate a
    # dialog for them without knowing what format it is talking to. A folder of
    # stills carries no timing, and a video importer will want an fps -- both
    # are "the reader needs to be told something the source doesn't say", which
    # is a property of the format, not of the UI.
    read_params: tuple[Param, ...] = ()
    # Called, not stored as a bool: an optional dependency has to be checked
    # when it matters, not at import time, or a missing one takes the app down
    # on startup -- the whole point of the guarantee.
    available: Callable[[], bool] = field(default=_always)

    def matches(self, path: Path) -> bool:
        path = Path(path)
        if self.is_folder:
            # A folder format claims a path that *is* a directory, or one that
            # doesn't exist yet and carries no suffix (an export target the user
            # is about to create).
            return path.is_dir() or (not path.exists() and not path.suffix)
        return path.suffix.lower() in self.extensions


FORMATS: tuple[Format, ...] = (
    Format(
        id="gif",
        label="GIF",
        extensions=(".gif",),
        read=read_gif,
        write=write_gif,
    ),
    Format(
        id="sequence",
        label="Image sequence (folder)",
        is_folder=True,
        read=read_sequence,
        write=write_sequence,
        read_params=(
            IntParam("delay_ms", "Delay per frame", default=100,
                     min=MIN_DURATION_MS, max=60000, unit="ms"),
            IntParam("loop", "Loop count (0 = forever)", default=0, min=0, max=1000),
        ),
    ),
)


def formats(*, readable: bool = False, writable: bool = False,
            folders: bool | None = None) -> tuple[Format, ...]:
    """Available formats, filtered. The one place that knows what exists."""
    out = []
    for fmt in FORMATS:
        if not fmt.available():
            continue
        if readable and fmt.read is None:
            continue
        if writable and fmt.write is None:
            continue
        if folders is not None and fmt.is_folder != folders:
            continue
        out.append(fmt)
    return tuple(out)


def format_for(path: Path, *, readable: bool = False,
               writable: bool = False) -> Format | None:
    """The format that claims `path`, or None.

    File formats are asked first. A path that exists as a directory can only be
    claimed by a folder format anyway, but an export target that does *not* yet
    exist is ambiguous -- `frames` could be a folder to create, and `out.gif` a
    file -- and preferring the extension match keeps the suffix meaningful.
    """
    path = Path(path)
    candidates = formats(readable=readable, writable=writable)
    for fmt in candidates:
        if not fmt.is_folder and fmt.matches(path):
            return fmt
    for fmt in candidates:
        if fmt.is_folder and fmt.matches(path):
            return fmt
    return None


def reader_for(path: Path) -> Reader | None:
    fmt = format_for(path, readable=True)
    return fmt.read if fmt else None


def writer_for(path: Path) -> Writer | None:
    fmt = format_for(path, writable=True)
    return fmt.write if fmt else None


def open_filter() -> list[tuple[str, str]]:
    """Tk-style (label, pattern) pairs, generated rather than hardcoded.

    File formats only: a folder is chosen through a directory picker, which has
    no use for patterns. That the filters *can't* describe a folder is the same
    fact as the dict not being able to key one.
    """
    patterns = " ".join(
        f"*{ext}" for fmt in formats(readable=True, folders=False)
        for ext in sorted(fmt.extensions)
    )
    return [("Animations", patterns), ("All files", "*.*")]


def save_filter() -> list[tuple[str, str]]:
    out = [(fmt.label, " ".join(f"*{ext}" for ext in sorted(fmt.extensions)))
           for fmt in formats(writable=True, folders=False)]
    return out + [("All files", "*.*")]
