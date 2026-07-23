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

    def apply(self, doc: Document, sel: Selection, delay_ms: int = 100, **_) -> OpResult:
        targets = _targets(doc, sel)
        frames = tuple(
            f.with_duration(delay_ms) if i in targets else f
            for i, f in enumerate(doc.frames)
        )
        return OpResult(replace(doc, frames=frames), sel)


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
        return OpResult(replace(doc, frames=frames), sel)
