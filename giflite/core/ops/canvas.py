"""Canvas operations: resize, rotate, flip.

The first ops that allocate pixels. Frame ops (M2) and timing ops (M4 s1) only
rearrange or re-time existing frames; these produce genuinely new images, so:

- Each output frame is `Frame.new(...)` -- a fresh uid, because the pixels are
  new (ARCHITECTURE.md 5). Reusing a uid here would serve stale cached
  thumbnails.
- History now holds real image memory, not just pointers (ARCHITECTURE.md 7).
  For a lite tool the 64-snapshot cap bounds it; the `FrameStore` escalation
  path (risk 1) is where this goes if a large GIF ever makes it hurt.
- They're global by nature -- every frame must stay the same canvas size -- so
  `needs_selection` is False and the selection is passed through untouched.

Rotation directions were checked against Pillow, not guessed: `ROTATE_270` is
90° clockwise and `ROTATE_90` is counter-clockwise.
"""

from __future__ import annotations

from dataclasses import replace

from PIL import Image

from giflite.core.model import Document, Frame, Selection
from giflite.core.ops.registry import OpResult, register_op
from giflite.core.params import BoolParam, ChoiceParam, IntParam

_ROTATIONS = {
    "cw": Image.ROTATE_270,   # 90 clockwise
    "180": Image.ROTATE_180,
    "ccw": Image.ROTATE_90,   # 90 counter-clockwise
}
_FLIPS = {
    "horizontal": Image.FLIP_LEFT_RIGHT,
    "vertical": Image.FLIP_TOP_BOTTOM,
}


def _resample(old_size, new_size):
    old_area = old_size[0] * old_size[1]
    new_area = new_size[0] * new_size[1]
    # Enlarging pixel art reads better crisp (NEAREST); shrinking reads better
    # smooth (LANCZOS). Same rule as the preview's fit.
    return Image.NEAREST if new_area >= old_area else Image.LANCZOS


@register_op
class ResizeCanvas:
    id = "canvas.resize"
    label = "Resize"  # the UI adds "..." for ops that have params
    accel = None
    needs_selection = False
    in_menu = True
    params = (
        IntParam("width", "Width", default=1, min=1, max=4096, unit="px"),
        IntParam("height", "Height", default=1, min=1, max=4096, unit="px"),
        BoolParam("keep_aspect", "Keep aspect ratio", default=True),
    )

    def default_params(self, doc: Document, sel: Selection) -> dict:
        # Seed the dialog with the current size so the user edits from reality,
        # not from an arbitrary 1x1 static default.
        w, h = doc.size
        return {"width": w, "height": h, "keep_aspect": True}

    def apply(self, doc: Document, sel: Selection,
              width: int = 0, height: int = 0, keep_aspect: bool = True, **_) -> OpResult:
        sw, sh = doc.size
        width = int(width) if width else sw
        height = int(height) if height else sh
        if keep_aspect:
            height = max(1, round(width * sh / sw))
        new_size = (max(1, width), max(1, height))
        resample = _resample(doc.size, new_size)
        frames = tuple(
            Frame.new(f.image.resize(new_size, resample), f.duration_ms)
            for f in doc.frames
        )
        return OpResult(replace(doc, frames=frames, size=new_size), sel)


@register_op
class RotateCanvas:
    id = "canvas.rotate"
    label = "Rotate"
    accel = None
    needs_selection = False
    in_menu = True
    params = (
        ChoiceParam(
            "angle", "Rotate",
            choices=(("90° clockwise", "cw"), ("180°", "180"),
                     ("90° counter-clockwise", "ccw")),
            default="cw",
        ),
    )

    def apply(self, doc: Document, sel: Selection, angle: str = "cw", **_) -> OpResult:
        const = _ROTATIONS.get(angle, Image.ROTATE_270)
        frames = tuple(
            Frame.new(f.image.transpose(const), f.duration_ms) for f in doc.frames
        )
        new_size = frames[0].image.size  # 90/270 swap w and h
        return OpResult(replace(doc, frames=frames, size=new_size), sel)


@register_op
class FlipCanvas:
    id = "canvas.flip"
    label = "Flip"
    accel = None
    needs_selection = False
    in_menu = True
    params = (
        ChoiceParam(
            "direction", "Flip",
            choices=(("Horizontal", "horizontal"), ("Vertical", "vertical")),
            default="horizontal",
        ),
    )

    def apply(self, doc: Document, sel: Selection, direction: str = "horizontal", **_) -> OpResult:
        const = _FLIPS.get(direction, Image.FLIP_LEFT_RIGHT)
        frames = tuple(
            Frame.new(f.image.transpose(const), f.duration_ms) for f in doc.frames
        )
        return OpResult(replace(doc, frames=frames), sel)
