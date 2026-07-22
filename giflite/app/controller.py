"""AppController -- the entire surface a frontend talks to.

This is the frontend seam (ARCHITECTURE.md 9). The rule that makes it hold:
the controller owns *session* state, not just the document. Playhead and
playback live here, not in the frontend, because otherwise every frontend
independently reimplements clamp-on-delete, clamp-on-undo and timeline/canvas
sync -- precisely the duplication the seam exists to prevent.

What stays with the frontend: widgets, the timer tick, zoom and pan, toolkit
bitmap caches, file pickers, and dialog policy.

M0 scope: open, seek, select, render. Playback arrives at M1, operations and
history at M2; the read-only members they will drive are stubbed here so the
frontend wiring doesn't have to change shape later.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from giflite.app import events as ev
from giflite.app.events import EventBus
from giflite.core.io import reader_for
from giflite.core.io.gif_read import probe_gif
from giflite.core.model import Document, Selection

# Above this, a load is worth mentioning before it happens. 640x480x120 frames
# is 147MB of RGBA (measured -- ARCHITECTURE.md 12.5), so this is roughly
# "twice a big GIF" rather than an arbitrary round number.
MEMORY_WARN_BYTES = 250 * 1024 * 1024


def _format_size(nbytes: int) -> str:
    return f"{nbytes / (1024 * 1024):.0f} MB"


class AppController:
    def __init__(self, events: EventBus | None = None) -> None:
        self.events = events or EventBus()
        self._doc: Document | None = None
        self._selection = Selection.empty()
        self._index = 0
        self._path: Path | None = None

    # ---- readable state --------------------------------------------------

    @property
    def doc(self) -> Document | None:
        """None means nothing is loaded -- a real state, not a zero-frame doc."""
        return self._doc

    @property
    def selection(self) -> Selection:
        return self._selection

    @property
    def index(self) -> int:
        """Playhead. Always in range for the current document."""
        return self._index

    @property
    def path(self) -> Path | None:
        """Single source of truth for where this came from (not on Document)."""
        return self._path

    @property
    def dirty(self) -> bool:
        return False  # M2: derived from History's saved-marker

    @property
    def frame_count(self) -> int:
        return len(self._doc) if self._doc else 0

    # ---- menu / toolbar state (so frontends don't re-derive it) ----------

    @property
    def can_undo(self) -> bool:
        return False  # M2

    @property
    def can_redo(self) -> bool:
        return False  # M2

    def can_run(self, op_id: str) -> bool:
        return False  # M2

    # ---- documents -------------------------------------------------------

    def open(self, path: Path) -> bool:
        """Load a file, replacing the current document. Returns success.

        Failures are reported on the ERROR event rather than raised: a bad
        file is a normal thing for a user to pick, not an exceptional one, and
        every frontend would otherwise wrap this call identically.
        """
        path = Path(path)
        read = reader_for(path)
        if read is None:
            self.events.emit(
                ev.ERROR,
                exception=ValueError(f"No reader for {path.suffix or 'this file'}"),
                context=str(path),
            )
            return False

        try:
            if path.suffix.lower() == ".gif":
                probe = probe_gif(path)
                if probe.nbytes_estimate > MEMORY_WARN_BYTES:
                    self.events.emit(
                        ev.STATUS,
                        message=(
                            f"Large animation: {probe.frame_count} frames will use "
                            f"about {_format_size(probe.nbytes_estimate)}"
                        ),
                    )
            self.events.emit(ev.STATUS, message=f"Loading {path.name}...")
            doc = read(path)
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user verbatim
            self.events.emit(ev.ERROR, exception=exc, context=str(path))
            return False

        self._doc = doc
        self._path = path
        self._index = 0
        self._selection = Selection.single(0)
        self._emit_doc_changed("open")
        self.events.emit(ev.TITLE_CHANGED, path=path, dirty=self.dirty)
        # No summary message here on purpose: "12 frames, 80x40, 1.15s" is a
        # view of current state, so the frontend derives it from `doc` rather
        # than caching a string it received once. STATUS carries only
        # transient things -- progress, warnings -- that aren't recoverable
        # from state.
        return True

    def close(self) -> None:
        self._doc = None
        self._path = None
        self._index = 0
        self._selection = Selection.empty()
        self._emit_doc_changed("close")
        self.events.emit(ev.TITLE_CHANGED, path=None, dirty=False)

    # ---- session ---------------------------------------------------------

    def seek(self, index: int) -> None:
        clamped = self._clamp(index)
        if clamped == self._index:
            return
        self._index = clamped
        self.events.emit(ev.PLAYHEAD_MOVED, index=clamped)

    def set_selection(self, selection: Selection) -> None:
        selection = selection.clamped(self.frame_count)
        if selection == self._selection:
            return
        self._selection = selection
        self.events.emit(ev.SELECTION_CHANGED, selection=selection)

    def frame_image(self, index: int | None = None) -> Image.Image | None:
        """Full-resolution pixels for a frame.

        Scaling is the frontend's job -- it owns zoom and pan, so it also owns
        the scaled-bitmap cache (ARCHITECTURE.md 9).
        """
        if self._doc is None:
            return None
        return self._doc[self._clamp(self._index if index is None else index)].image

    # ---- internals -------------------------------------------------------

    def _clamp(self, index: int) -> int:
        if not self.frame_count:
            return 0
        return max(0, min(int(index), self.frame_count - 1))

    def _emit_doc_changed(self, reason: str) -> None:
        """The one place DOC_CHANGED is emitted, so the contract can't drift.

        Clamping happens here too: every path that changes the frame count
        funnels through this method, which is what stops "park on the last
        frame, delete it" from indexing off the end.
        """
        self._index = self._clamp(self._index)
        self._selection = self._selection.clamped(self.frame_count)
        self.events.emit(
            ev.DOC_CHANGED,
            doc=self._doc,
            selection=self._selection,
            index=self._index,
            reason=reason,
        )
