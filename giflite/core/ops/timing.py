"""Timing operations: set per-frame delay, scale playback speed.

Pure and pixel-free -- they only re-time existing frames (via
`Frame.with_duration`, which keeps the image and its uid, so caches still
hit). Both apply to the selection when there is one, or the whole animation
when there isn't, so `needs_selection` is False.

These are the first ops to carry `Param`, so they double as the schema's first
real customers.
"""

from __future__ import annotations

from dataclasses import replace

from giflite.core.model import MIN_DURATION_MS, Document, Selection
from giflite.core.ops.registry import OpResult, register_op
from giflite.core.params import FloatParam, IntParam


def _targets(doc: Document, sel: Selection) -> frozenset[int]:
    return sel.indices if sel else frozenset(range(len(doc.frames)))


def _retimed(doc: Document, sel: Selection, frames) -> Document:
    """The new document, or the *same* one if the retiming changed nothing.

    The decline convention every other op family already follows (crop, delete,
    the paint ops): returning the same object makes `run_op` report "nothing to
    do" instead of pushing an identity snapshot onto the undo stack.

    These two ops were the exception, and it went unnoticed while the only way
    in was a menu and a dialog -- you rarely open a dialog to retype the value
    that is already there. An inline delay box asks the question on every commit,
    which is how this surfaced. Durations are quantised on the way in, so this
    also catches "typed 103ms, already 100ms", where the *request* differs but
    the result does not.
    """
    if all(new is old or new.duration_ms == old.duration_ms
           for new, old in zip(frames, doc.frames)):
        return doc
    return replace(doc, frames=frames)


@register_op
class SetDelay:
    id = "timing.set_delay"
    label = "Set Frame Delay"  # the UI adds "..." for ops that have params
    accel = None
    needs_selection = False
    in_menu = True
    params = (
        IntParam("delay_ms", "Delay per frame", default=100, min=MIN_DURATION_MS, max=60000, unit="ms"),
    )

    def default_params(self, doc: Document, sel: Selection) -> dict:
        """Seed the dialog with what the targets already hold, so the user edits
        from reality rather than from a static 100ms.

        When they disagree, the shortest wins -- not the first, and not the
        average. Retiming a mixed run is nearly always about *slowing part of it
        down*, so the smallest value is the one you are most likely adjusting
        away from, and it is the only choice that is a real delay some frame
        actually has rather than a number invented for the box.
        """
        targets = _targets(doc, sel)
        delays = [f.duration_ms for i, f in enumerate(doc.frames) if i in targets]
        return {"delay_ms": min(delays)} if delays else {}

    def apply(self, doc: Document, sel: Selection, delay_ms: int = 100, **_) -> OpResult:
        targets = _targets(doc, sel)
        frames = tuple(
            f.with_duration(delay_ms) if i in targets else f
            for i, f in enumerate(doc.frames)
        )
        return OpResult(_retimed(doc, sel, frames), sel)


@register_op
class ScaleSpeed:
    id = "timing.scale_speed"
    label = "Scale Speed"
    accel = None
    needs_selection = False
    in_menu = True
    params = (
        FloatParam("factor", "Speed multiplier (2 = twice as fast)",
                   default=2.0, min=0.1, max=20.0),
    )

    def apply(self, doc: Document, sel: Selection, factor: float = 2.0, **_) -> OpResult:
        factor = max(0.01, float(factor))
        targets = _targets(doc, sel)
        # Faster playback = shorter frames, so divide. with_duration floors at
        # MIN_DURATION_MS, so a big speed-up bottoms out at 20ms rather than
        # jumping back up (the quantiser fix this milestone depends on).
        frames = tuple(
            f.with_duration(f.duration_ms / factor) if i in targets else f
            for i, f in enumerate(doc.frames)
        )
        return OpResult(_retimed(doc, sel, frames), sel)
