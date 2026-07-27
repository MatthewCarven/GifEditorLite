"""Painting operations: paint a stroke, erase a stroke.

The first ops driven by a *tool* rather than a menu or a dialog (ARCHITECTURE.md
19). A stroke isn't param-shaped, so these carry no `Param`s and declare
`in_menu = False` like `frames.move` / `canvas.crop`; the frontend's tool maps
a drag to image pixels and passes the finished polyline straight to `apply`.

Two things make this future-proof and safe:

- **The brush is a mask.** `_brush_mask` stamps the stroke into an `L`-mode
  coverage image (0-255). Paint alpha-composites the colour *through* the mask;
  erase subtracts the mask *from* the frame's alpha. A hard brush is a 0/255
  mask; a soft/anti-aliased brush later is a feathered one and nothing else here
  changes -- same compositing, same op (ARCHITECTURE.md 19).
- **One frame, fresh pixels.** A stroke edits only the playhead frame (`index`)
  and hands back a `Frame.new` for it -- a fresh uid, because the pixels are new
  (stale-cache guard, ARCHITECTURE.md 5) -- while every other frame stays shared
  by reference. The source image is copied before drawing, never mutated
  (immutability invariant; `tests/test_immutability.py`).

A stroke that changes nothing (empty, entirely off-canvas, or erasing already-
transparent pixels) returns the *same* document, so `controller.run_op` reports
"nothing to do" instead of pushing an identity snapshot onto the undo stack --
the same decline convention crop and delete-everything use.
"""

from __future__ import annotations

from dataclasses import replace

from PIL import Image, ImageChops, ImageDraw

from giflite.core.model import Document, Frame, Selection
from giflite.core.ops.registry import OpResult, register_op

Point = tuple[float, float]
Color = tuple[int, int, int, int]


def _rgba(color) -> Color:
    """Tolerate a 3-tuple from a colour picker; force a 4-tuple of ints."""
    c = tuple(int(v) for v in color)
    if len(c) == 3:
        return (c[0], c[1], c[2], 255)
    return (c[0], c[1], c[2], c[3])


def _brush_mask(canvas_size: tuple[int, int], points, size: int) -> Image.Image:
    """Coverage mask for a round hard brush of diameter `size` along `points`.

    This is the one place the brush *shape* lives. A soft brush later swaps a
    feathered stamp in here and the ops above it don't change. Drawing clips to
    the image automatically, so off-canvas points are harmless.
    """
    mask = Image.new("L", canvas_size, 0)
    draw = ImageDraw.Draw(mask)
    r = max(1, int(round(size)))
    pts = [tuple(p) for p in points]
    if r == 1:
        draw.point(pts, fill=255)
        if len(pts) >= 2:
            draw.line(pts, fill=255, width=1)
    else:
        half = r / 2.0
        for x, y in pts:
            draw.ellipse([x - half, y - half, x + half, y + half], fill=255)
        if len(pts) >= 2:
            draw.line(pts, fill=255, width=r, joint="curve")
    return mask


def _composite(base: Image.Image, mask: Image.Image, color: Color, mode: str) -> Image.Image:
    """Apply the mask to a *copy* of `base`; the original is never touched."""
    out = base.copy()
    if mode == "erase":
        # Pull the frame's alpha down by the mask: hard mask clears to 0, a
        # future soft mask feathers the edge.
        out.putalpha(ImageChops.subtract(out.getchannel("A"), mask))
    else:
        # Lay the colour into a transparent layer through the mask, then
        # alpha-composite -- correct even when the colour or mask is partial.
        stroke = Image.new("RGBA", out.size, (0, 0, 0, 0))
        stroke.paste(Image.new("RGBA", out.size, color), (0, 0), mask)
        out.alpha_composite(stroke)
    return out


def _apply_stroke(doc: Document, sel: Selection, index: int,
                  points, size: int, color: Color, mode: str) -> OpResult:
    if not points or not (0 <= int(index) < len(doc.frames)):
        return OpResult(doc, sel)  # nothing to paint / no such frame -> decline
    index = int(index)
    frame = doc.frames[index]
    mask = _brush_mask(doc.size, points, size)
    out = _composite(frame.image, mask, color, mode)
    if out.tobytes() == frame.image.tobytes():
        return OpResult(doc, sel)  # stroke missed / erased nothing -> decline
    frames = list(doc.frames)
    frames[index] = Frame.new(out, frame.duration_ms)  # fresh uid, same timing
    # Keep the playhead on the frame just painted (and select it). The op must
    # own this: run_op moves the index to result.selection.first, so passing the
    # old selection through would jump the playhead off the frame we just edited.
    return OpResult(replace(doc, frames=tuple(frames)), Selection.single(index))


@register_op
class PaintStroke:
    id = "paint.stroke"
    label = "Paint"  # feeds "Undo Paint"
    accel = None
    needs_selection = False
    in_menu = False  # tool-driven, not a menu/dialog op
    params = ()

    def apply(self, doc: Document, sel: Selection, index: int = 0, points=(),
              size: int = 1, color: Color = (0, 0, 0, 255), **_) -> OpResult:
        return _apply_stroke(doc, sel, index, points, size, _rgba(color), "paint")


@register_op
class EraseStroke:
    id = "paint.erase"
    label = "Erase"
    accel = None
    needs_selection = False
    in_menu = False
    params = ()

    def apply(self, doc: Document, sel: Selection, index: int = 0, points=(),
              size: int = 1, **_) -> OpResult:
        return _apply_stroke(doc, sel, index, points, size, (0, 0, 0, 0), "erase")
