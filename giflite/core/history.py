"""Undo/redo as a stack of snapshots.

A snapshot is the whole session state -- document, selection, playhead, and the
label of the op that produced it. This is only affordable because frames are
immutable and shared by reference: a snapshot after reordering 200 frames is
200 pointers, about 1.6 KB (ARCHITECTURE.md 7). Storing selection and index
alongside the document is the fix for rev 1's mistake of snapshotting the
document alone, which restored frames but left a stale, possibly out-of-range
selection on undo.

Dirty tracking is a saved-marker, not a boolean: History remembers the stack
position at the last save, so undoing back to that exact state correctly clears
the asterisk -- something a one-way flag can never do.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from giflite.core.model import Document, Selection


@dataclass(frozen=True, slots=True)
class Snapshot:
    doc: Document
    selection: Selection
    index: int
    label: str  # the op that produced this state ("Open", "Delete Frames", ...)


class History:
    def __init__(self, limit: int = 64) -> None:
        self._limit = limit
        self._stack: list[Snapshot] = []
        self._pos = -1
        # Position matching the on-disk state. None means "the saved state has
        # fallen off the end of the bounded history", so we can no longer get
        # back to clean -> dirty stays True.
        self._saved_pos: int | None = -1

    def reset(self, snapshot: Snapshot) -> None:
        """Start fresh from a just-opened document (which is a saved state)."""
        self._stack = [snapshot]
        self._pos = 0
        self._saved_pos = 0

    def clear(self) -> None:
        self._stack = []
        self._pos = -1
        self._saved_pos = None

    def amend_current(self, selection: Selection, index: int) -> None:
        """Refresh the top snapshot's selection and playhead in place.

        Selection and scrubbing between ops don't push snapshots -- they aren't
        undoable steps -- but they *are* the view you should return to when the
        next op is undone. Calling this just before `push` captures "where the
        user was" at the moment they invoked the op, so undo restores that
        rather than a stale selection frozen at the previous op.
        """
        if self._pos >= 0:
            self._stack[self._pos] = replace(
                self._stack[self._pos], selection=selection, index=index
            )

    def push(self, snapshot: Snapshot) -> None:
        """Record a new state after an op, discarding any redo branch."""
        del self._stack[self._pos + 1:]
        self._stack.append(snapshot)
        self._pos = len(self._stack) - 1
        if len(self._stack) > self._limit:
            overflow = len(self._stack) - self._limit
            del self._stack[:overflow]
            self._pos -= overflow
            if self._saved_pos is not None:
                self._saved_pos -= overflow
                if self._saved_pos < 0:
                    self._saved_pos = None  # saved state is no longer reachable

    def undo(self) -> Snapshot | None:
        if not self.can_undo:
            return None
        self._pos -= 1
        return self._stack[self._pos]

    def redo(self) -> Snapshot | None:
        if not self.can_redo:
            return None
        self._pos += 1
        return self._stack[self._pos]

    def mark_saved(self) -> None:
        self._saved_pos = self._pos

    # ---- queries ---------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return self._pos > 0

    @property
    def can_redo(self) -> bool:
        return -1 < self._pos < len(self._stack) - 1

    @property
    def undo_label(self) -> str | None:
        """Label of the op that would be undone (the current state's producer)."""
        return self._stack[self._pos].label if self.can_undo else None

    @property
    def redo_label(self) -> str | None:
        return self._stack[self._pos + 1].label if self.can_redo else None

    @property
    def dirty(self) -> bool:
        return self._pos != self._saved_pos
