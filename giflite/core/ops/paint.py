"""Painting operations: strokes, flood fill, shapes.

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

**Fill and shapes are the same op with a different mask.** That was the whole
bet of "the brush is a mask", and it paid: `paint.fill` and `paint.shape` add
mask *generators* and reuse `_composite` and `_apply_mask` unchanged. Every
property already established -- alpha compositing, immutability, fresh uids,
declining a no-op, the playhead staying put -- comes along for free rather than
being reimplemented twice more and getting one of them subtly wrong.
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


def _match_mask(image: Image.Image, seed: Color, tolerance: int) -> Image.Image:
    """255 wherever `image` is within `tolerance` of `seed`, per channel.

    Chebyshev distance (the largest single-channel difference), not Euclidean.
    Predictable is worth more than principled here: "tolerance 8" should mean
    "no channel differs by more than 8", which is a sentence a user can hold in
    their head, rather than a radius in a 4-space they cannot picture.

    Built out of whole-image operations so the per-pixel work happens in
    Pillow's C rather than in a Python loop -- `difference` is C, and `point`
    with a 256-entry lookup table is a C-applied LUT, not a callback per pixel.
    That matters because the connectivity walk below *is* Python, and doubling
    it with a Python colour comparison would make a fill on a large frame
    visibly slow.
    """
    lut = [255 if v <= tolerance else 0 for v in range(256)]
    within = None
    for channel, value in zip(image.split(), seed):
        flat = Image.new("L", image.size, int(value))
        near = ImageChops.difference(channel, flat).point(lut)
        within = near if within is None else ImageChops.multiply(within, near)
    return within


def _fill_mask(image: Image.Image, x: int, y: int, tolerance: int) -> Image.Image | None:
    """Coverage mask for a flood fill seeded at `(x, y)`. None if out of bounds.

    Two stages, deliberately: *which pixels match* is a colour question and is
    answered above in C, and *which of those are reachable* is a connectivity
    question, answered by `ImageDraw.floodfill` walking the match mask. Writing
    one combined flood in Python would be a per-pixel colour comparison inside a
    per-pixel walk, which is the slow way to get the same answer.

    Marking with 128 rather than a second image is what makes the second stage
    free: the flood repaints the reachable run of 255s, so the pixels left at
    255 are precisely the matching-but-unreachable ones, and a LUT separates
    them. That is the whole of "contiguous, not global" -- and it means the
    global variant, if it is ever wanted, is this function without the flood.
    """
    width, height = image.size
    if not (0 <= x < width and 0 <= y < height):
        return None
    seed = image.getpixel((x, y))
    match = _match_mask(image, seed, tolerance)
    ImageDraw.floodfill(match, (x, y), 128, thresh=0)
    return match.point([255 if v == 128 else 0 for v in range(256)])


def _shape_mask(canvas_size: tuple[int, int], kind: str,
                box: tuple[int, int, int, int], size: int, filled: bool) -> Image.Image | None:
    """Coverage mask for a line, rectangle or ellipse. None for an unknown kind.

    **The box is pixel-inclusive**, unlike a crop box. A rectangle from (2, 3)
    to (7, 9) covers those pixels and everything between them, because a shape
    tool addresses pixels the way a brush does -- which is exactly the
    `Tool.coords` distinction the frontend already declares, applied one level
    down. Pillow's `rectangle`/`ellipse` are inclusive of both corners too, so
    the convention needs no adjustment here; it needs one in the *preview*, and
    that is where it is made (see `ShapeTool.preview_box`).

    `filled` is ignored by `line`, which has no interior. Outline width is the
    brush size, so one Size control drives every tool.
    """
    x0, y0, x1, y1 = box
    x0, x1 = sorted((int(x0), int(x1)))
    y0, y1 = sorted((int(y0), int(y1)))
    width = max(1, int(round(size)))
    mask = Image.new("L", canvas_size, 0)
    draw = ImageDraw.Draw(mask)
    if kind == "line":
        # Round caps to match the brush, which stamps an ellipse per point. A
        # butt-capped line next to a round-capped freehand stroke of the same
        # Size reads as two different brushes.
        if width > 1:
            half = width / 2.0
            for px, py in ((box[0], box[1]), (box[2], box[3])):
                draw.ellipse([px - half, py - half, px + half, py + half], fill=255)
        draw.line([(box[0], box[1]), (box[2], box[3])], fill=255, width=width)
    elif kind == "rect":
        if filled:
            draw.rectangle([x0, y0, x1, y1], fill=255)
        else:
            draw.rectangle([x0, y0, x1, y1], outline=255, width=width)
    elif kind == "ellipse":
        if filled:
            draw.ellipse([x0, y0, x1, y1], fill=255)
        else:
            draw.ellipse([x0, y0, x1, y1], outline=255, width=width)
    else:
        return None
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


def _apply_mask(doc: Document, sel: Selection, index: int,
                mask: Image.Image | None, color: Color, mode: str) -> OpResult:
    """Composite `mask` into frame `index` and hand back a new document.

    The single commit path for every painting op. A stroke, a flood fill and a
    shape differ only in how their coverage mask is built; from here on they are
    indistinguishable, which is why immutability, fresh uids, the decline
    convention and the playhead rule are stated once rather than three times.
    """
    if mask is None or not (0 <= int(index) < len(doc.frames)):
        return OpResult(doc, sel)  # nothing to paint / no such frame -> decline
    index = int(index)
    frame = doc.frames[index]
    out = _composite(frame.image, mask, color, mode)
    if out.tobytes() == frame.image.tobytes():
        return OpResult(doc, sel)  # missed / painted what was already there -> decline
    frames = list(doc.frames)
    frames[index] = Frame.new(out, frame.duration_ms)  # fresh uid, same timing
    # Keep the playhead on the frame just painted (and select it). The op must
    # own this: run_op moves the index to result.selection.first, so passing the
    # old selection through would jump the playhead off the frame we just edited.
    return OpResult(replace(doc, frames=tuple(frames)), Selection.single(index))


def _apply_stroke(doc: Document, sel: Selection, index: int,
                  points, size: int, color: Color, mode: str) -> OpResult:
    if not points:
        return OpResult(doc, sel)
    return _apply_mask(doc, sel, index, _brush_mask(doc.size, points, size), color, mode)


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


@register_op
class FloodFill:
    """Paint the connected run of similar pixels under a click.

    Registered `in_menu=False` and carrying no `Param`s for the same reason crop
    and the strokes are: the interesting argument is a point on the image, and
    typing two numbers is a poor way to choose one.
    """

    id = "paint.fill"
    label = "Fill"  # feeds "Undo Fill"
    accel = None
    needs_selection = False
    in_menu = False
    params = ()

    def apply(self, doc: Document, sel: Selection, index: int = 0,
              x: int = 0, y: int = 0, color: Color = (0, 0, 0, 255),
              tolerance: int = 0, **_) -> OpResult:
        if not (0 <= int(index) < len(doc.frames)):
            return OpResult(doc, sel)
        image = doc.frames[int(index)].image
        mask = _fill_mask(image, int(x), int(y), max(0, int(tolerance)))
        return _apply_mask(doc, sel, index, mask, _rgba(color), "paint")


@register_op
class DrawShape:
    """Line, rectangle or ellipse from one drag.

    One op with a `kind` rather than three ops, because the three differ by a
    single `ImageDraw` call and nothing else -- the same reasoning that makes
    Pencil and Eraser one `StrokeTool` in the frontend. An unknown kind declines
    rather than guessing, so a typo in a future tool surfaces as "nothing to do"
    instead of quietly drawing a rectangle.
    """

    id = "paint.shape"
    label = "Shape"
    accel = None
    needs_selection = False
    in_menu = False
    params = ()

    def apply(self, doc: Document, sel: Selection, index: int = 0, kind: str = "line",
              x0: int = 0, y0: int = 0, x1: int = 0, y1: int = 0,
              size: int = 1, color: Color = (0, 0, 0, 255),
              filled: bool = False, **_) -> OpResult:
        mask = _shape_mask(doc.size, kind, (x0, y0, x1, y1), size, bool(filled))
        return _apply_mask(doc, sel, index, mask, _rgba(color), "paint")
