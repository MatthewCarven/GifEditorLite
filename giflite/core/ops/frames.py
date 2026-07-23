"""The five frame operations: delete, duplicate, move, reverse, trim.

Each is pure and returns the selection that should exist afterwards. None of
them touch pixels -- they only rearrange, drop, or share existing frames -- so
none allocate images, and undo of any of them is a handful of pointers.
"""

from __future__ import annotations

from dataclasses import replace

from giflite.core.model import Document, Frame, Selection
from giflite.core.ops.registry import OpResult, register_op


@register_op
class DeleteFrames:
    id = "frames.delete"
    label = "Delete Frames"
    accel = "Delete"
    needs_selection = True
    in_menu = True

    def apply(self, doc: Document, sel: Selection, **_) -> OpResult:
        kept = tuple(f for i, f in enumerate(doc.frames) if i not in sel.indices)
        if not kept:
            # Emptying the document is invalid; the controller guards this and
            # surfaces a message. Returning the doc unchanged keeps the op pure
            # and side-effect-free.
            return OpResult(doc, sel)
        # Select whatever slid into the first deleted slot, clamped to the end.
        landing = min(min(sel.indices), len(kept) - 1)
        return OpResult(replace(doc, frames=kept), Selection.single(landing))


@register_op
class DuplicateFrames:
    id = "frames.duplicate"
    label = "Duplicate Frames"
    accel = "Ctrl+D"
    needs_selection = True
    in_menu = True

    def apply(self, doc: Document, sel: Selection, copies: int = 1, **_) -> OpResult:
        copies = max(1, int(copies))
        out: list[Frame] = []
        new_positions: list[int] = []
        for i, frame in enumerate(doc.frames):
            out.append(frame)
            if i in sel.indices:
                for _ in range(copies):
                    new_positions.append(len(out))
                    # Share pixels: a duplicate is the same image, so it reuses
                    # the uid and hits the thumbnail/preview caches for free.
                    out.append(frame.sharing_pixels())
        selection = Selection(frozenset(new_positions), anchor=new_positions[0])
        return OpResult(replace(doc, frames=tuple(out)), selection)


@register_op
class MoveFrames:
    id = "frames.move"
    label = "Move Frames"
    accel = None
    needs_selection = True
    in_menu = False  # driven by drag-to-reorder, not a menu command

    def apply(self, doc: Document, sel: Selection, to: int = 0, **_) -> OpResult:
        """Lift the selected frames out and reinsert them as one block before
        original index `to` (0..len; len == drop at the end)."""
        selected = sel.indices
        moved = [doc.frames[i] for i in sorted(selected)]
        remaining = [(i, f) for i, f in enumerate(doc.frames) if i not in selected]
        # `to` is an original-frame anchor; translate it to a position among
        # the frames that are staying put.
        insert_at = sum(1 for i, _ in remaining if i < to)
        head = [f for _, f in remaining[:insert_at]]
        tail = [f for _, f in remaining[insert_at:]]
        new_frames = tuple(head + moved + tail)
        selection = Selection(
            frozenset(range(insert_at, insert_at + len(moved))), anchor=insert_at
        )
        return OpResult(replace(doc, frames=new_frames), selection)


@register_op
class ReverseFrames:
    id = "frames.reverse"
    label = "Reverse"
    accel = None
    needs_selection = False  # no selection -> reverse the whole animation
    in_menu = True

    def apply(self, doc: Document, sel: Selection, **_) -> OpResult:
        if len(sel.indices) >= 2:
            # Reverse just the selected frames, in their own positions.
            positions = sorted(sel.indices)
            frames = list(doc.frames)
            for pos, frame in zip(positions, [frames[p] for p in reversed(positions)]):
                frames[pos] = frame
            return OpResult(replace(doc, frames=tuple(frames)), sel)
        # Whole-document reverse; keep whatever was selected pointing at the
        # same frame in its new position.
        n = len(doc.frames)
        flipped = tuple(reversed(doc.frames))
        selection = Selection(frozenset(n - 1 - i for i in sel.indices))
        return OpResult(replace(doc, frames=flipped), selection)


@register_op
class TrimToSelection:
    id = "frames.trim"
    label = "Trim to Selection"
    accel = None
    needs_selection = True
    in_menu = True

    def apply(self, doc: Document, sel: Selection, **_) -> OpResult:
        """Keep only the selected frames, discard the rest."""
        kept = tuple(doc.frames[i] for i in sorted(sel.indices))
        selection = Selection(frozenset(range(len(kept))), anchor=0)
        return OpResult(replace(doc, frames=kept), selection)
