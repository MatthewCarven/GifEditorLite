"""AppController -- the entire surface a frontend talks to.

This is the frontend seam (ARCHITECTURE.md 9). The rule that makes it hold:
the controller owns *session* state, not just the document. Playhead and
playback live here, not in the frontend, because otherwise every frontend
independently reimplements clamp-on-delete, clamp-on-undo, timeline/canvas
sync and play-pause semantics -- precisely the duplication the seam exists to
prevent.

What stays with the frontend: widgets, the timer tick (it calls `tick(dt)`),
zoom and pan, toolkit bitmap caches, file pickers, and dialog policy.

M0 scope: open, seek, select, render.
M1 scope (now): playback -- play/pause/tick/seek/set_speed, driven by the
pure PlaybackClock in core.
History and operations arrive at M2; the read-only members they will drive
are stubbed here so the frontend wiring doesn't change shape later.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image

from giflite.app import events as ev
from giflite.app import sysclip
from giflite.app.events import EventBus
from giflite.core.history import History, Snapshot
from giflite.core.io import format_for, reader_for, writer_for
from giflite.core.io.gif_read import probe_gif
from giflite.core.io.gif_write import count_merges
from giflite.core.model import Document, Region, Selection
from giflite.core.ops import get_op, op_label  # importing this also registers the ops
from giflite.core.playback import MAX_TICK_MS, PlaybackClock

# Above this, a load is worth mentioning before it happens. 640x480x120 frames
# is 147MB of RGBA (measured -- ARCHITECTURE.md 12.5), so this is roughly
# "twice a big GIF" rather than an arbitrary round number.
MEMORY_WARN_BYTES = 250 * 1024 * 1024

# Appended to the stem when offering a Save As name for a freshly opened file,
# so the default action never overwrites the user's original.
EDITED_SUFFIX = "_edited"


def _format_size(nbytes: int) -> str:
    return f"{nbytes / (1024 * 1024):.0f} MB"


@dataclass(frozen=True)
class FloatingEdit:
    """Pixels shown but not committed -- a move or a paste being placed.

    **The third state.** Everything in this editor has been either committed to
    the document or a gesture in flight, and that binary is load-bearing: it is
    why Esc has a clean ladder, and why a window resize can safely cancel
    anything outstanding (ARCHITECTURE.md 20.4). A float is neither. It outlives
    the drag that made it, survives a resize -- its offset is in image pixels,
    so nothing about the view can invalidate it -- and yet the document does not
    know it exists.

    It lives here rather than in the frontend for the usual reason (§9): a
    second frontend would otherwise reinvent placement, commit and cancel
    semantics, which is most of the interaction.

    `image` is the clipboard for a paste and None for a move, and that None is
    the whole difference between them: a paste stamps *one* image onto every
    target frame, while a move shifts whatever each frame has in that rectangle.
    """

    kind: str            # "move" | "paste"
    region: Region       # where it sits at zero offset
    image: Image.Image | None = None
    dx: int = 0
    dy: int = 0

    @property
    def op_id(self) -> str:
        return "paint.move" if self.kind == "move" else "paint.paste"

    @property
    def placed(self) -> bool:
        """Whether it has actually been moved anywhere yet."""
        return bool(self.dx or self.dy)

    def op_params(self, index: int, frames: tuple[int, ...]) -> dict:
        """The arguments its commit op wants. One place, so the preview and the
        commit cannot drift -- they are literally the same call."""
        if self.kind == "move":
            return dict(index=index, frames=frames,
                        x=self.region.x, y=self.region.y,
                        width=self.region.width, height=self.region.height,
                        dx=self.dx, dy=self.dy)
        return dict(index=index, frames=frames, image=self.image,
                    x=self.region.x + self.dx, y=self.region.y + self.dy)


class AppController:
    def __init__(self, events: EventBus | None = None) -> None:
        self.events = events or EventBus()
        self._doc: Document | None = None
        self._selection = Selection.empty()
        self._index = 0
        self._path: Path | None = None
        # True while `_path` is still the file we *read*, i.e. nothing has been
        # written over it yet. Saving to it would re-encode the user's original
        # in place, which for GIF is lossy and irreversible, so the frontend gets
        # to warn once. Cleared by the first successful write to any path.
        self._path_is_source = False
        # Where this came from when it has no file of its own -- an imported
        # folder's name. The title bar has nothing else to show for a document
        # that was never opened from a path, and "Untitled" throws away the one
        # piece of context the user has.
        self._source_label: str | None = None
        self._clock = PlaybackClock()
        self._playing = False
        self._history = History()
        # The rectangular pixel selection, beside the frame Selection. Session
        # state, not document state: it is not undoable, it is not saved, and
        # it survives every edit that doesn't invalidate it.
        self._region: Region | None = None
        # The clipboard, and where its pixels came from. Deliberately *not*
        # cleared by `open`: copying a sprite out of one GIF and stamping it
        # into another is a thing people do, and a clipboard that emptied
        # whenever the document changed would be one nobody could plan around.
        self._clipboard: Image.Image | None = None
        self._clipboard_origin: tuple[int, int] = (0, 0)
        # The move or paste currently being placed, or None. See FloatingEdit.
        self._float: FloatingEdit | None = None
        # Guards `_settle_float`, which commits the float from inside the very
        # methods committing it goes on to call.
        self._settling = False

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
    def playing(self) -> bool:
        return self._playing

    @property
    def speed(self) -> float:
        return self._clock.speed

    @property
    def path(self) -> Path | None:
        """Single source of truth for where this came from (not on Document)."""
        return self._path

    @property
    def source_label(self) -> str | None:
        """A display name for a document with no path (an imported folder), or
        None. The frontend prefers `path.name` when there is a path."""
        return self._source_label

    @property
    def region(self) -> Region | None:
        """The selected rectangle of canvas, or None. See core.model.Region."""
        return self._region

    @property
    def dirty(self) -> bool:
        # No document -> nothing to be dirty about, regardless of stack state.
        return self._doc is not None and self._history.dirty

    @property
    def frame_count(self) -> int:
        return len(self._doc) if self._doc else 0

    @property
    def can_play(self) -> bool:
        """A single frame has nowhere to advance to, so there's nothing to play."""
        return self.frame_count > 1

    # ---- menu / toolbar state (so frontends don't re-derive it) ----------

    @property
    def can_undo(self) -> bool:
        return self._history.can_undo

    @property
    def can_redo(self) -> bool:
        return self._history.can_redo

    @property
    def undo_label(self) -> str | None:
        return self._history.undo_label

    @property
    def redo_label(self) -> str | None:
        return self._history.redo_label

    def can_run(self, op_id: str) -> bool:
        """Whether an op could run right now -- drives menu enable/disable so
        each frontend doesn't re-derive it from needs_selection + selection."""
        op = get_op(op_id)
        if op is None or self._doc is None:
            return False
        return not (op.needs_selection and not self._selection)

    # ---- documents -------------------------------------------------------

    def open(self, path: Path) -> bool:
        """Load a file, replacing the current document. Returns success.

        Failures are reported on the ERROR event rather than raised: a bad
        file is a normal thing for a user to pick, not an exceptional one, and
        every frontend would otherwise wrap this call identically.
        """
        self._settle_float()
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

        self._stop_playback()
        self._doc = doc
        self._path = path
        self._path_is_source = True  # untouched original until something writes
        self._source_label = None    # it has a real path now
        self._index = 0
        self._selection = Selection.single(0)
        self.set_region(None)  # a region names pixels in the document it left with
        self._clock.loop = doc.loop
        # A freshly opened file is the baseline saved state.
        self._history.reset(Snapshot(doc, self._selection, 0, "Open"))
        self._emit_doc_changed("open")
        self.events.emit(ev.TITLE_CHANGED, path=path, dirty=self.dirty)
        # No summary message here on purpose: "12 frames, 80x40, 1.15s" is a
        # view of current state, so the frontend derives it from `doc` rather
        # than caching a string it received once. STATUS carries only
        # transient things -- progress, warnings -- that aren't recoverable
        # from state.
        return True

    def import_frames(self, folder: Path, **options) -> bool:
        """Load a folder of stills as a new document.

        **Import is not open, and the difference is one field.** An opened file
        is a document's home: Save writes back to it. An imported folder is a
        *source* -- the document it produces is a GIF-shaped thing that has
        never been saved anywhere, and pointing `_path` at the folder would aim
        Ctrl+S at writing a GIF over somebody's PNGs. So `_path` stays None and
        Save falls through to Save As, which is exactly what "this has no file
        yet" already means everywhere else in here.

        `_source_label` carries the folder's name for the title bar, because
        "Untitled" after importing a named folder throws away the one piece of
        context the user has.
        """
        self._settle_float()
        folder = Path(folder)
        fmt = format_for(folder, readable=True)
        if fmt is None or fmt.read is None:
            self.events.emit(
                ev.ERROR,
                exception=ValueError(f"Nothing here can read {folder.name}"),
                context=str(folder),
            )
            return False
        try:
            self.events.emit(ev.STATUS, message=f"Importing {folder.name}...")
            doc = fmt.read(folder, **options)
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user verbatim
            self.events.emit(ev.ERROR, exception=exc, context=str(folder))
            return False

        self._stop_playback()
        self._doc = doc
        self._path = None                # no file to save back to -- see above
        self._path_is_source = False
        self._source_label = folder.name
        self._index = 0
        self._selection = Selection.single(0)
        self.set_region(None)  # a region names pixels in the document it left with
        self._clock.loop = doc.loop
        self._history.reset(Snapshot(doc, self._selection, 0, "Import"))
        self._emit_doc_changed("open")   # same shape of change as opening a file
        self.events.emit(ev.TITLE_CHANGED, path=None, dirty=self.dirty,
                         name=self._source_label)
        self.events.emit(
            ev.STATUS,
            message=f"Imported {len(doc)} frames from {folder.name}",
        )
        return True

    def export_frames(self, folder: Path) -> bool:
        """Write every frame into `folder` as a numbered PNG, plus a manifest.

        Export is not save: it leaves `_path`, the dirty flag and the history
        alone. Writing a copy of your frames somewhere is not the same claim as
        "this document now lives here", and conflating them would clear the
        unsaved marker on a document that still has no file.
        """
        if self._doc is None:
            return False
        folder = Path(folder)
        fmt = format_for(folder, writable=True)
        if fmt is None or fmt.write is None:
            self.events.emit(
                ev.ERROR,
                exception=ValueError(f"Nothing here can write to {folder.name}"),
                context=str(folder),
            )
            return False
        try:
            written = fmt.write(self._doc, folder)
        except Exception as exc:  # noqa: BLE001
            self.events.emit(ev.ERROR, exception=exc, context=str(folder))
            return False
        count = len(written) if written is not None else len(self._doc)
        self.events.emit(
            ev.STATUS, message=f"Exported {count} frames to {folder.name}")
        return True

    def close(self) -> None:
        self._settle_float()
        self._stop_playback()
        self._doc = None
        self._path = None
        self._path_is_source = False
        self._source_label = None
        self._index = 0
        self._selection = Selection.empty()
        self.set_region(None)
        self._history.clear()
        self._emit_doc_changed("close")
        self.events.emit(ev.TITLE_CHANGED, path=None, dirty=False)

    # ---- saving ----------------------------------------------------------

    @property
    def has_path(self) -> bool:
        """Whether Save can write in place, or must fall back to Save As."""
        return self._path is not None

    @property
    def overwrites_source(self) -> bool:
        """Whether a plain Save would write over the file that was opened.

        Worth knowing because saving is *not* a round trip: writing a GIF rebuilds
        the palette and merges identical consecutive frames into longer holds
        (ARCHITECTURE.md 12, 18). Do that in place and the original is gone. The
        judgement of whether to warn, and how, is frontend policy -- this is just
        the fact it needs, so a second frontend doesn't have to re-derive it.
        """
        return self._path is not None and self._path_is_source

    @property
    def suggested_save_name(self) -> str:
        """A filename to offer in Save As, steered away from the original.

        Suffixes `_edited` while the path is still the untouched source, and does
        so idempotently -- saving twice must not produce `a_edited_edited.gif`.
        """
        if self._path is None:
            return "untitled.gif"
        if not self._path_is_source:
            return self._path.name
        stem = self._path.stem
        if not stem.endswith(EDITED_SUFFIX):
            stem += EDITED_SUFFIX
        return stem + self._path.suffix

    @property
    def save_would_change_nothing(self) -> bool:
        """Whether a plain Save has nothing to write.

        True when there is a path and no unsaved edits, so disk already holds
        this state. Writing anyway is never an improvement and is sometimes a
        loss: over an untouched source it re-encodes the original -- rebuilt
        palette, merged holds -- in exchange for nothing (ARCHITECTURE.md 19.2).
        Exposed as a fact so a frontend can word it; `save` also acts on it, so
        a frontend that forgets cannot destroy an original by accident.
        """
        return self._doc is not None and self._path is not None and not self.dirty

    def save(self) -> bool:
        """Write to the current path. False if there's nowhere to write yet
        (the frontend should open Save As in that case).

        A save with nothing to save is skipped, not performed -- see
        `save_would_change_nothing`. It still reports success: the caller asked
        for disk to match the document, and it does.
        """
        if self._doc is None or self._path is None:
            return False
        if self.save_would_change_nothing:
            self.events.emit(ev.STATUS, message="No changes to save")
            return True
        return self._write(self._path)

    def save_as(self, path: Path) -> bool:
        return self._write(Path(path))

    def _write(self, path: Path) -> bool:
        self._settle_float()  # save what is on screen, not what is underneath
        if self._doc is None:
            return False
        writer = writer_for(path)
        if writer is None:
            self.events.emit(
                ev.ERROR,
                exception=ValueError(f"No writer for {path.suffix or 'this file'}"),
                context=str(path),
            )
            return False

        # Count merges before writing, while we still have the authored frames.
        merges = count_merges(self._doc)
        try:
            writer(self._doc, path)
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user
            self.events.emit(ev.ERROR, exception=exc, context=str(path))
            return False

        self._path = path
        # Whatever this path was before, it now holds *our* output, so a further
        # Save can't destroy anything the user didn't already replace.
        self._path_is_source = False
        self._history.mark_saved()  # this state now matches disk -> not dirty
        self.events.emit(ev.TITLE_CHANGED, path=path, dirty=self.dirty)
        message = f"Saved {path.name}"
        if merges:
            plural = "s" if merges > 1 else ""
            message += f"  ({merges} identical frame{plural} merged into longer holds)"
        self.events.emit(ev.STATUS, message=message)
        return True

    # ---- editing ---------------------------------------------------------

    def run_op(self, op_id: str, **params) -> None:
        """Apply an operation, record it for undo, and announce the change.

        Failures and refusals go to STATUS/ERROR rather than raising: running
        an op that can't apply (nothing selected, would empty the document) is
        a normal user action, not an exception.
        """
        if self._doc is None:
            return
        self._settle_float()  # never edit around an uncommitted move
        op = get_op(op_id)
        if op is None:
            self.events.emit(ev.ERROR, exception=ValueError(f"Unknown operation {op_id!r}"), context="")
            return
        if op.needs_selection and not self._selection:
            self.events.emit(ev.STATUS, message="Select one or more frames first")
            return
        # What this *run* is called, which is not always the op's static label:
        # one op can do materially different things depending on its arguments
        # (paint.fill fills or clears). Resolved once, here, so the undo entry
        # and the two status messages cannot disagree about what just happened.
        label = op_label(op, **params)

        try:
            result = op.apply(self._doc, self._selection, **params)
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user
            self.events.emit(ev.ERROR, exception=exc, context=label)
            return

        if result.doc is self._doc:
            # The op declined (e.g. delete-everything). Say why, change nothing.
            self.events.emit(ev.STATUS, message=f"{label}: nothing to do")
            return
        try:
            result.doc.validate()
        except ValueError as exc:
            self.events.emit(ev.STATUS, message=f"{label}: {exc}")
            return

        self._stop_playback()
        # Capture where the user was (selection + playhead) so undo returns
        # here, not to a selection frozen at the previous op.
        self._history.amend_current(self._selection, self._index)
        self._doc = result.doc
        self._selection = result.selection
        # An op may name the playhead itself; otherwise it goes to the start of
        # whatever is now selected. See OpResult.index for why the second rule
        # is not enough on its own.
        if result.index is not None:
            self._index = self._clamp(result.index)
        else:
            self._index = self._clamp(result.selection.first if result.selection else self._index)
        self._history.push(Snapshot(self._doc, self._selection, self._index, label))
        self._emit_doc_changed(f"op:{op_id}")
        self.events.emit(ev.TITLE_CHANGED, path=self._path, dirty=self.dirty)

    def undo(self) -> None:
        self._settle_float()
        snap = self._history.undo()
        if snap is not None:
            self._restore(snap, "undo")

    def redo(self) -> None:
        self._settle_float()
        snap = self._history.redo()
        if snap is not None:
            self._restore(snap, "redo")

    def _restore(self, snap: Snapshot, reason: str) -> None:
        self._stop_playback()
        self._doc = snap.doc
        self._selection = snap.selection
        self._index = snap.index
        self._emit_doc_changed(reason)
        self.events.emit(ev.TITLE_CHANGED, path=self._path, dirty=self.dirty)

    # ---- region and clipboard --------------------------------------------
    #
    # Both are *session* state, which is why they are here and not in the
    # frontend and not in the Document (ARCHITECTURE.md 9). A region is not
    # undoable -- undoing a paste should give you back your pixels, not
    # rearrange what you had selected -- and a clipboard is not part of any
    # document, or copying between two files would be impossible to express.
    #
    # The split with the frontend is the same one crop and save already use:
    # this owns the fact and the arithmetic, the frontend owns the gesture that
    # produces it and the marquee that shows it.

    def set_region(self, region: Region | None) -> None:
        """Select a rectangle of canvas, or None to clear it.

        Clamped on the way in, so a region can never name pixels outside the
        document -- a marquee dragged past the edge of a zoomed-out canvas is
        the ordinary case, not an error.
        """
        if region is not None and self._doc is not None:
            region = region.clamped(self._doc.size)
        elif self._doc is None:
            region = None
        if region == self._region:
            return
        self._region = region
        self.events.emit(ev.REGION_CHANGED, region=region)

    @property
    def can_copy(self) -> bool:
        return self._doc is not None and self._region is not None

    @property
    def can_paste(self) -> bool:
        return self._doc is not None and self._clipboard is not None

    @property
    def clipboard_size(self) -> tuple[int, int] | None:
        """The clipboard's dimensions, for a status line or a menu label."""
        return None if self._clipboard is None else self._clipboard.size

    def copy_region(self) -> bool:
        """Take the region's pixels from the frame the playhead is on.

        One frame, because there is only one set of pixels a clipboard can
        hold. `.copy()` rather than the bare `crop` result: Pillow's crop is
        lazy about materialising, and a clipboard is exactly the sort of thing
        that outlives the document it was taken from.
        """
        if not self.can_copy:
            return False
        image = self._doc[self._clamp(self._index)].image
        self._clipboard = image.crop(self._region.box).copy()
        self._clipboard_origin = (self._region.x, self._region.y)
        w, h = self._clipboard.size
        self.events.emit(ev.STATUS,
                         message=f"Copied {w}x{h}{self._to_system_clipboard()}")
        return True

    def _to_system_clipboard(self) -> str:
        """Mirror the internal clipboard out to the OS. Returns a status suffix.

        Every copy goes out, not just Copy Frame, because there is **one
        clipboard** (Matthew's call) and the last thing you copied should be the
        thing another application gets. A sprite is as worth having in Discord
        as a frame is.

        The *internal* slot is still the one Ctrl+V reads, and that is not
        redundancy: it carries `_clipboard_origin`, which is what makes paste
        land back where it was copied from. No system clipboard format has
        anywhere to put that, so a round trip through the OS would silently
        turn paste-in-place into paste-at-the-corner.

        Failing to reach the OS clipboard is **not** a failure of the copy. The
        pixels are in hand either way, so this reports in the status line and
        returns normally; raising would turn "another app has the clipboard
        open for a moment" into a copy that didn't happen.
        """
        if not sysclip.can_copy():
            return ""
        try:
            sysclip.put_image(self._clipboard)
        except Exception as exc:  # noqa: BLE001 -- OS refusals are varied
            return f" (not to the system clipboard: {exc})"
        return ""

    def cut_region(self) -> bool:
        """Copy the region, then clear it -- on the playhead frame only.

        The copy happens first and unconditionally. If the region was already
        empty the op declines and says "nothing to do", but the clipboard still
        holds what was there, which is the honest outcome: cutting nothing
        copies nothing, and that is not a failure.
        """
        if not self.copy_region():
            return False
        region = self._region
        self.run_op("paint.cut", index=self._index, x=region.x, y=region.y,
                    width=region.width, height=region.height)
        return True

    # ---- whole frames, and the system clipboard --------------------------
    #
    # Copy Area answers "these pixels"; Copy Frame answers "this picture". They
    # share one clipboard slot, so copying either replaces the other -- which is
    # what every editor does and the only version that has a single answer to
    # "what does Ctrl+V paste?".

    @property
    def can_copy_frame(self) -> bool:
        return self._doc is not None

    def copy_frame(self) -> bool:
        """Put the whole playhead frame on the clipboard, internal and system.

        Origin (0, 0), because a whole frame *is* the canvas -- which means a
        Copy Frame followed by Ctrl+V is a paste-in-place of the entire picture,
        and lands exactly where it came from with no special case.
        """
        if self._doc is None:
            return False
        frame = self._doc[self._clamp(self._index)]
        self._clipboard = frame.image.copy()
        self._clipboard_origin = (0, 0)
        w, h = self._clipboard.size
        self.events.emit(ev.STATUS,
                         message=f"Copied frame {self._clamp(self._index) + 1}"
                                 f" ({w}x{h}){self._to_system_clipboard()}")
        return True

    def paste_frame(self) -> bool:
        """Replace the playhead frame with the image on the system clipboard.

        **The system clipboard, not the internal one**, and that asymmetry is
        the feature rather than an oversight: this is the door *into* the
        editor. A screenshot, a frame exported from something else, a drawing
        from a paint program -- Pillow will hand any of them over, and pasting
        one in as a frame is the thing Ctrl+V could never do.

        Replaces rather than composites (Matthew's call): a frame with
        transparent corners should arrive with transparent corners, not with
        the old frame showing through them. The frame keeps its own duration.

        Refuses a size mismatch and names both sizes. Nothing happens and
        nothing lands on the undo stack -- the numbers are what tell you what
        to do about it, where a bare "wrong size" would not.
        """
        if self._doc is None:
            return False
        image, problem = sysclip.grab_image()
        if image is None:
            self.events.emit(ev.STATUS, message=problem)
            return False
        complaint = sysclip.size_complaint(image.size, self._doc.size)
        if complaint:
            self.events.emit(ev.STATUS, message=complaint)
            return False
        self.run_op("paint.replace_frame", index=self._clamp(self._index),
                    image=image)
        return True

    def crop_to_region(self) -> bool:
        """Shrink the canvas to the selected rectangle.

        The whole of it is `run_op("canvas.crop", **the region's four numbers)`,
        and that is not a coincidence: `Region` is held in edge coordinates
        precisely so it *is* `canvas.crop`'s argument list (ARCHITECTURE.md 26).
        Written out here rather than in a frontend because a second frontend
        would otherwise have to know that correspondence too, and it is the
        controller that owns the region.

        The region is dropped afterwards, not kept. The rectangle you named has
        become the whole canvas, so a marquee is either the entire image -- which
        says nothing -- or, if left where it was, a smaller rectangle in the
        wrong place: `_emit_doc_changed` re-clamps a region to the new canvas
        size, which for this edit means trimming it against a canvas whose origin
        just moved. Neither is what anyone meant, and neither is worth keeping.

        Undo restores the pixels but not the marquee, because the region is
        session state and session state is not on the undo stack (26.3).
        """
        if self._doc is None or self._region is None:
            return False
        region = self._region
        before = self._doc
        self.run_op("canvas.crop", x=region.x, y=region.y,
                    width=region.width, height=region.height)
        # A region covering the whole canvas makes this the identity, and the op
        # declines rather than stacking a no-op undo entry. Taking the marquee
        # away as a reward for that would be the one case where the command
        # changes nothing *except* something you didn't ask it to change.
        if self._doc is not before:
            self.set_region(None)
        return True

    def paste(self) -> bool:
        """Paste the clipboard where it was copied from, into `frame_targets`.

        In place, so a cut and an immediate paste is an exact undo of the cut
        by hand, and so stamping across frames puts the sprite in the same spot
        on every one of them -- which is the entire point of stamping across
        frames. Moving it is slice 2's job (a floating paste), and until that
        exists the honest thing is to land it somewhere predictable rather than
        somewhere convenient.
        """
        if not self.can_paste:
            return False
        x, y = self._clipboard_origin
        targets = self.frame_targets
        self.run_op("paint.paste", index=self._index, frames=targets,
                    image=self._clipboard, x=x, y=y)
        return True

    # ---- the floating edit ------------------------------------------------
    #
    # See FloatingEdit for why this is a third state rather than a gesture. The
    # rules it needs, all of which fall out of that:
    #
    #   * the preview is the op *run and not committed*, which is free because
    #     ops are pure -- and means the preview cannot disagree with the result;
    #   * committing is one op, so one Ctrl+Z undoes the whole move rather than
    #     handing back the hole while you are still holding the sprite;
    #   * cancelling is free, because the document was never touched;
    #   * anything else you do commits it first (`_settle_float`), because a
    #     commit you did not want is one undo away and a discard is not.

    @property
    def floating(self) -> FloatingEdit | None:
        return self._float

    @property
    def float_offset(self) -> tuple[int, int]:
        return (0, 0) if self._float is None else (self._float.dx, self._float.dy)

    def begin_move(self) -> bool:
        """Lift the current region off the frame, ready to be placed."""
        if self._doc is None or self._region is None:
            return False
        self._settle_float()
        self._set_float(FloatingEdit("move", self._region))
        return True

    def begin_paste(self) -> bool:
        """Float the clipboard where it was copied from, ready to be placed.

        Ctrl+V starts here rather than landing straight away. It costs one
        keystroke -- Enter -- and buys the ability to put it somewhere else,
        which is the thing paste-in-place could not do at all. Pressing Enter
        immediately is exactly the old behaviour.
        """
        if not self.can_paste:
            return False
        self._settle_float()
        x, y = self._clipboard_origin
        width, height = self._clipboard.size
        self._set_float(FloatingEdit("paste", Region(x, y, width, height),
                                     image=self._clipboard))
        return True

    def move_float(self, dx: int, dy: int) -> bool:
        """Place the float at an absolute offset from where it started."""
        if self._float is None:
            return False
        dx, dy = int(dx), int(dy)
        if (dx, dy) == (self._float.dx, self._float.dy):
            return False
        self._set_float(replace(self._float, dx=dx, dy=dy))
        return True

    def nudge_float(self, dx: int, dy: int) -> bool:
        """Move it by a step, for the arrow keys."""
        if self._float is None:
            return False
        return self.move_float(self._float.dx + int(dx), self._float.dy + int(dy))

    def float_preview(self, index: int | None = None) -> Image.Image | None:
        """What frame `index` looks like with the float in place, uncommitted.

        The op, run and thrown away. Purity is what makes this a two-line method
        instead of a second implementation of what a move looks like -- and it
        is why the preview is not merely *consistent* with the commit but is the
        same call. A move that lands wrong will look wrong first.
        """
        if self._doc is None:
            return None
        index = self._clamp(self._index if index is None else index)
        if self._float is None:
            return self._doc[index].image
        op = get_op(self._float.op_id)
        result = op.apply(self._doc, self._selection,
                          **self._float.op_params(index, (index,)))
        return result.doc[index].image

    def commit_float(self) -> bool:
        """Land the float as one undoable edit. False if there was nothing to do."""
        if self._doc is None or self._float is None:
            return False
        floating, self._float = self._float, None
        if floating.kind == "move" and not floating.placed:
            # Picked it up and put it back. Not an edit, and not worth a
            # "nothing to do" either -- nothing happened.
            self.events.emit(ev.FLOAT_CHANGED, floating=None)
            return False
        self.run_op(floating.op_id,
                    **floating.op_params(self._index, self.frame_targets))
        self.events.emit(ev.FLOAT_CHANGED, floating=None)
        return True

    def cancel_float(self) -> bool:
        """Drop it. Free, because the document was never touched."""
        if self._float is None:
            return False
        self._set_float(None)
        return True

    def _set_float(self, floating: FloatingEdit | None) -> None:
        self._float = floating
        self.events.emit(ev.FLOAT_CHANGED, floating=floating)

    def _settle_float(self) -> None:
        """Commit any float before doing something else.

        Called from every entry point that would otherwise strand one: editing,
        scrubbing, undo, opening, closing and saving. Committing rather than
        discarding because a commit you did not want is one Ctrl+Z away, while
        work discarded on your behalf is simply gone.

        Re-entrant by construction -- `commit_float` calls `run_op`, which calls
        this -- so the flag is not optional.
        """
        if self._float is None or self._settling:
            return
        self._settling = True
        try:
            self.commit_float()
        finally:
            self._settling = False

    # ---- playback --------------------------------------------------------

    def play(self) -> None:
        if self._doc is None or not self.can_play or self._playing:
            return
        # Pressing play at the end of a finished (non-looping) animation is a
        # request to watch it again, so rewind rather than sit inert.
        if self._clock.finished or self._index >= self.frame_count - 1:
            self._clock.restart()
            self._sync_from_clock()
        self._playing = True
        self.events.emit(ev.PLAYBACK_STATE, playing=True)

    def pause(self) -> None:
        if not self._playing:
            return
        self._playing = False
        self.events.emit(ev.PLAYBACK_STATE, playing=False)

    def toggle_play(self) -> None:
        self.pause() if self._playing else self.play()

    def set_speed(self, factor: float) -> None:
        self._clock.set_speed(factor)

    @property
    def pingpong(self) -> bool:
        return self._clock.pingpong

    def set_pingpong(self, enabled: bool) -> None:
        self._clock.set_pingpong(enabled)

    def tick(self, dt_ms: float) -> None:
        """Advance playback. The frontend's timer calls this every tick.

        A no-op when paused, so the frontend can run a single always-on timer
        rather than starting and stopping one -- fewer moving parts, no
        start/stop races.
        """
        if not self._playing or self._doc is None:
            return
        # After a stall dt can be huge; cap it so we don't fast-forward through
        # the whole animation in one frame.
        new_index = self._clock.tick(min(dt_ms, MAX_TICK_MS))
        if new_index != self._index:
            self._index = new_index
            self.events.emit(ev.PLAYHEAD_MOVED, index=new_index)
        if self._clock.finished:
            self._playing = False
            self.events.emit(ev.PLAYBACK_STATE, playing=False)

    # ---- session ---------------------------------------------------------

    def seek(self, index: int) -> None:
        # A float belongs to the frame you started it on; carrying it to
        # another one would be placing pixels you cannot see.
        self._settle_float()
        clamped = self._clamp(index)
        self._clock.seek(clamped)  # keep the clock's position in step with scrubs
        if clamped == self._index:
            return
        self._index = clamped
        self.events.emit(ev.PLAYHEAD_MOVED, index=clamped)

    def step(self, delta: int) -> None:
        """Nudge the playhead by whole frames (arrow keys). Pauses first."""
        self.pause()
        self.seek(self._index + delta)

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

    # ---- per-frame timing -------------------------------------------------
    #
    # `timing.set_delay` has existed since M4, reachable through a menu and a
    # dialog. What lives here is the *fast path* a frontend needs to put a delay
    # box on screen: what to show in it, how many frames it would retime, and a
    # set that is scoped safely. The op is untouched -- this is state derivation
    # and scope policy, which is exactly what the controller is for, and putting
    # it here means a second frontend gets the same answers rather than
    # re-deriving them (ARCHITECTURE.md 9).

    @property
    def current_delay_ms(self) -> int | None:
        """The playhead frame's own delay. None when nothing is open.

        The status line showed only `total_duration_ms`, which is a different
        number that happens to coincide on a one-frame GIF -- so until now
        nothing on screen reported a single frame's timing at all.
        """
        if self._doc is None:
            return None
        return self._doc[self._clamp(self._index)].duration_ms

    @property
    def frame_targets(self) -> tuple[int, ...]:
        """The frames an inline, non-dialog edit applies to, in order.

        Written for the delay box and named `frame_targets` for it, until paste
        turned out to want the identical rule -- "every selected frame" is what
        stamping a sprite across an animation means, and the qualification
        below is exactly as necessary there. A scope rule with two callers is a
        policy; renaming it says so.

        **The selection, or just the playhead frame -- never everything.** The
        menu op treats "no selection" as "the whole animation", which is right
        for a deliberate menu action with a dialog in front of it. An inline box
        sitting beside the frame counter reads as "this frame", and having it
        quietly retime all forty is the kind of surprise that costs someone an
        afternoon. Different affordance, different default.

        **And only a selection the playhead is actually in.** Opening a file
        selects frame 0, and `seek`/`step` deliberately leave the selection
        alone -- so arrowing to frame 3 leaves frame 0 selected, and a box keyed
        on the selection alone would report and edit frame 0's delay while the
        preview and the status line both showed frame 3. A selection you have
        stepped away from is not what you are working on; a selection you are
        standing inside is.
        """
        if self._doc is None:
            return ()
        index = self._clamp(self._index)
        if self._selection and index in self._selection.indices:
            return self._selection.ordered
        return (index,)

    @property
    def target_delay_ms(self) -> int | None:
        """The delay to show in a delay box: the shared value, or None if the
        targets disagree. None means "mixed", which the frontend should render
        as an empty box rather than as a number that is wrong for most of them.
        """
        if self._doc is None:
            return None
        delays = {self._doc[i].duration_ms for i in self.frame_targets}
        return delays.pop() if len(delays) == 1 else None

    def set_frame_delay(self, delay_ms: int) -> None:
        """Retime `frame_targets` to `delay_ms`, as one undoable edit.

        Runs the existing op rather than reimplementing it, by scoping the
        selection to `frame_targets` first -- so quantisation, the 20ms floor,
        validation, history and the events all behave exactly as they do from
        the menu, and the frames retimed are exactly the ones the box said it
        would retime. A decline restores the selection, because a no-op should
        not leave frames selected that the user never selected.
        """
        if self._doc is None:
            return
        before_sel, before_doc = self._selection, self._doc
        targets = Selection(frozenset(self.frame_targets))
        if targets != before_sel:
            self._selection = targets
        self.run_op("timing.set_delay", delay_ms=delay_ms)
        if self._doc is before_doc:
            self._selection = before_sel

    # ---- internals -------------------------------------------------------

    def _clamp(self, index: int) -> int:
        if not self.frame_count:
            return 0
        return max(0, min(int(index), self.frame_count - 1))

    def _stop_playback(self) -> None:
        if self._playing:
            self._playing = False
            self.events.emit(ev.PLAYBACK_STATE, playing=False)

    def _sync_from_clock(self) -> None:
        if self._clock.index != self._index:
            self._index = self._clock.index
            self.events.emit(ev.PLAYHEAD_MOVED, index=self._index)

    def _emit_doc_changed(self, reason: str) -> None:
        """The one place DOC_CHANGED is emitted, so the contract can't drift.

        Clamping and clock re-sync happen here too: every path that changes
        the frame count funnels through this method, which is what stops "park
        on the last frame, delete it" from indexing off the end, and keeps the
        clock's timing list matching the document.
        """
        self._index = self._clamp(self._index)
        self._selection = self._selection.clamped(self.frame_count)
        # A region names pixels, so it survives a frame-count change untouched
        # and has to be re-clamped whenever the *canvas* changes shape -- crop,
        # resize, rotate. Same funnel as the selection, for the same reason:
        # every path that can invalidate it passes through here, so there is one
        # place to get right rather than one per op. It is trimmed rather than
        # dropped when it still overlaps, because after a crop the part you were
        # working on is usually still on screen.
        was_region = self._region
        if self._region is not None:
            self._region = (self._region.clamped(self._doc.size)
                            if self._doc is not None else None)
        durations = [f.duration_ms for f in self._doc] if self._doc else []
        self._clock.set_durations(durations)
        self._clock.seek(self._index)
        self.events.emit(
            ev.DOC_CHANGED,
            doc=self._doc,
            selection=self._selection,
            index=self._index,
            reason=reason,
        )
        # After DOC_CHANGED, never before: a listener told the region shrank
        # while it still held the old document would redraw the marquee against
        # a canvas that no longer exists.
        if self._region != was_region:
            self.events.emit(ev.REGION_CHANGED, region=self._region)
