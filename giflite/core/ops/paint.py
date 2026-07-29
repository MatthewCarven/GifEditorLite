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

**Cut and paste are two more of them, and they cost the bet its first real
concession.** `paint.cut` is a solid rectangle erased, which is the existing
shape mask read in edge coordinates. `paint.paste` is the pasted image's own
alpha, plus the one genuinely new thing in here: a *colour layer*, because a
paste supplies a colour per pixel where every previous op supplied one for all
of them. That is a parameter on `_composite`, not a second pipeline.

The concession is frame count. Everything before this edited exactly one frame,
and pasting into every selected frame does not, so the commit path became
`_apply_mask_frames` and `_apply_mask` became the single-frame caller of it --
which is also what forced `OpResult.index` (see registry.py) to exist.
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


def _clear_mask(image: Image.Image) -> Image.Image:
    """255 wherever `image` is fully transparent, whatever colour is under it.

    Exists because "empty" is not one colour. A GIF's transparent pixels carry
    the RGB of the transparent palette index, and an *erased* pixel keeps
    whatever artwork used to be there -- `paint.erase` pulls the alpha down and
    deliberately leaves the RGB alone. So a frame can hold two runs of pixels
    that are pixel-identical on screen (both checkerboard) and numerically
    different, and a colour match that reads all four channels stops dead at the
    join.

    That is not hypothetical: erase part of a sprite, then bucket-fill the empty
    space around it, and the fill refuses to cross into the bit you just erased.
    Nothing on screen explains why, because on screen there is nothing there.

    Alpha zero means the other three channels describe a pixel you cannot see.
    Treating them as significant is the bug; ignoring them is the fix.
    """
    return image.getchannel("A").point([255 if v == 0 else 0 for v in range(256)])


def _match_mask(image: Image.Image, seed: Color, tolerance: int) -> Image.Image:
    """255 wherever `image` is within `tolerance` of `seed`, per channel.

    Seeded on a fully transparent pixel, this matches *emptiness* rather than a
    colour: every invisible pixel, whatever RGB happens to sit under it. See
    `_clear_mask`.

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
    if seed[3] == 0:
        return _clear_mask(image)
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


def _region_mask(canvas_size: tuple[int, int],
                 x: int, y: int, width: int, height: int) -> Image.Image | None:
    """Coverage mask for a `Region` -- a solid rectangle in *edge* coordinates.

    The one place the two rectangle conventions meet. A `Region` describes the
    lines between pixels (like a crop box); `_shape_mask` addresses the pixels
    themselves, so the far edge comes back in by one. Doing that conversion here
    rather than at each call site is the same discipline as `preview_box` doing
    it once for the shape preview -- ARCHITECTURE.md 19.1/23.3 is the record of
    what two derivations of the same coordinate cost.
    """
    if width < 1 or height < 1:
        return None
    return _shape_mask(canvas_size, "rect",
                       (x, y, x + width - 1, y + height - 1), 1, True)


def _paste_layer(canvas_size: tuple[int, int], image: Image.Image,
                 x: int, y: int) -> Image.Image | None:
    """Position a pasted image on the canvas, ready to composite.

    **Paste is one more mask generator** (ARCHITECTURE.md 23.1); the only thing
    it adds is that the colour varies per pixel instead of being one value. The
    mask is the pasted image's own alpha -- which is what makes the transparent
    corners of a copied sprite land as *nothing* rather than as a rectangular
    bite taken out of the frame -- and here the mask and the colour arrive
    already together, as the alpha channel of the positioned layer.

    Pillow's `paste` clips a box that hangs off the canvas (verified, including
    negative origins), so a paste half off the edge needs no arithmetic here.
    Pasting without a mask is a straight channel copy, so the layer comes out
    in straight alpha -- which is what `_composite` needs and what the version
    of this that used a mask here failed to produce; see there.
    """
    if image is None or image.size[0] < 1 or image.size[1] < 1:
        return None
    source = image if image.mode == "RGBA" else image.convert("RGBA")
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    layer.paste(source, (int(x), int(y)))
    return layer


def _composite(base: Image.Image, mask: Image.Image, color: Color, mode: str,
               layer: Image.Image | None = None) -> Image.Image:
    """Apply the mask to a *copy* of `base`; the original is never touched.

    `layer` is the paste case: a canvas-sized image carrying both the colour
    *and* the coverage, since a pasted sprite's alpha is its own mask. Erase and
    flat-colour painting are unchanged.

    **The stroke layer is built by setting its alpha, not by pasting through
    the mask**, and that is a bug fix rather than a preference. `Image.paste`
    with a mask *blends* -- `dst*(1-m) + src*m` on every channel -- so pasting
    an opaque colour into a transparent layer through a mask of 128 yields
    `(r/2, g/2, b/2, 128)`: premultiplied colour sitting in a straight-alpha
    image. `alpha_composite` then multiplies by the alpha a second time and the
    result comes out too dark, over a light background visibly so.

    It had been correct for two years by accident: every mask in here was hard,
    0 or 255, and at 255 the blend is an exact copy. §19 promised that a soft
    or anti-aliased brush would be "a feathered mask and nothing else changes",
    and this is the line that would have made that false. A pasted sprite with
    a soft edge is the first soft mask the codebase has ever seen, which is why
    it surfaced now.
    """
    out = base.copy()
    # Only the exact string "erase" erases; anything else paints. `mode` comes
    # from a frontend checkbox, so a stale or misspelt value is the failure to
    # defend against -- and of the two ways to be wrong, painting when erase was
    # meant is visible and one undo away, while erasing when paint was meant
    # destroys pixels that were there. This one line is the whole guard: an
    # earlier draft normalised `mode` in a helper first, which a mutation run
    # showed changed nothing, because the comparison here already decides it.
    if mode == "erase":
        # Pull the frame's alpha down by the mask: hard mask clears to 0, a
        # soft one feathers the edge.
        out.putalpha(ImageChops.subtract(out.getchannel("A"), mask))
    else:
        if layer is not None:
            stroke = layer  # already colour + coverage, in straight alpha
        else:
            stroke = Image.new("RGBA", out.size, (color[0], color[1], color[2], 255))
            alpha = mask
            if color[3] != 255:
                # A translucent colour and a soft mask are the same kind of
                # coverage, so they multiply rather than one winning.
                alpha = ImageChops.multiply(
                    mask, Image.new("L", out.size, color[3]))
            stroke.putalpha(alpha)
        out.alpha_composite(stroke)
    return out


def _apply_frames(doc: Document, sel: Selection, frames, index: int | None,
                  transform) -> OpResult:
    """Run `transform(image) -> image` over every frame in `frames`.

    **The single commit path**, and it is about *frames*, not about masks. That
    distinction was forced by `paint.move`, which is an erase and a composite on
    the same frame and so cannot be expressed as one mask at all -- but it was
    the right shape from the start: everything stated here is a fact about
    frames, and nothing about it depends on how the new pixels were arrived at.

    What is stated once here rather than once per op:

    - **Fresh uids for changed pixels** (stale-cache guard, ARCHITECTURE.md 5).
    - **Frames the transform does not change stay shared by reference**, not
      rewritten -- so stamping a sprite onto twenty frames where three already
      have it allocates seventeen images, and undo stays cheap.
    - **No change anywhere means the same document comes back**, so `run_op`
      reports "nothing to do" instead of pushing an identity snapshot onto undo.
    - **The playhead is named, not inferred** (see `OpResult.index`).

    `transform` returning None counts as no change, which lets a transform
    decline a frame without the caller checking first.
    """
    targets = sorted({int(i) for i in frames if 0 <= int(i) < len(doc.frames)})
    if not targets:
        return OpResult(doc, sel)  # no such frame -> decline
    out_frames = list(doc.frames)
    changed = False
    for i in targets:
        frame = out_frames[i]
        out = transform(frame.image)
        if out is None or out.tobytes() == frame.image.tobytes():
            continue  # missed / painted what was already there
        out_frames[i] = Frame.new(out, frame.duration_ms)  # fresh uid, same timing
        changed = True
    if not changed:
        return OpResult(doc, sel)
    return OpResult(replace(doc, frames=tuple(out_frames)), sel, index)


def _apply_mask_frames(doc: Document, sel: Selection, frames, index: int | None,
                       mask: Image.Image | None, color: Color, mode: str,
                       layer: Image.Image | None = None) -> OpResult:
    """Composite one mask into every frame in `frames`.

    A stroke, a flood fill, a shape, a cut and a paste differ only in how their
    coverage mask is built and how many frames they land on; from here on they
    are indistinguishable.
    """
    if mask is None:
        return OpResult(doc, sel)
    return _apply_frames(doc, sel, frames, index,
                         lambda image: _composite(image, mask, color, mode, layer))


def _apply_mask(doc: Document, sel: Selection, index: int,
                mask: Image.Image | None, color: Color, mode: str) -> OpResult:
    """The single-frame case: composite into frame `index` alone.

    Keep the playhead on the frame just painted, and select it. The op must own
    this: `run_op` sends the index to `result.selection.first`, so passing the
    old selection through would jump the playhead off the frame we just edited.
    (An op that edits *many* frames cannot use this trick and says where the
    playhead goes directly instead -- see `OpResult.index`.)
    """
    result = _apply_mask_frames(doc, sel, (index,), None, mask, color, mode)
    if result.doc is doc:
        return result
    return OpResult(result.doc, Selection.single(int(index)))


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
    """Paint -- or clear -- the connected run of similar pixels under a click.

    Registered `in_menu=False` and carrying no `Param`s for the same reason crop
    and the strokes are: the interesting argument is a point on the image, and
    typing two numbers is a poor way to choose one.

    **`mode="erase"` is the whole of "flood fill with transparency".** It needs
    no new mask generator and no new op, because the mask answers *which pixels*
    and the mode answers *what happens to them* -- two questions that were
    already separate. What it does need is a truthful undo label, since "Undo
    Fill" after removing pixels describes the code and not the action; hence
    `label_for`.

    Worth stating because it is the question a user actually asks: there is no
    colour that erases. Painting alpha-composites, so a fully transparent colour
    composited over a frame changes nothing and the op declines. Removing alpha
    is a different operation, not a different colour.
    """

    id = "paint.fill"
    label = "Fill"  # feeds "Undo Fill"
    accel = None
    needs_selection = False
    in_menu = False
    params = ()

    def label_for(self, mode: str = "paint", **_) -> str:
        return "Erase Fill" if mode == "erase" else self.label

    def apply(self, doc: Document, sel: Selection, index: int = 0,
              x: int = 0, y: int = 0, color: Color = (0, 0, 0, 255),
              tolerance: int = 0, mode: str = "paint", **_) -> OpResult:
        if not (0 <= int(index) < len(doc.frames)):
            return OpResult(doc, sel)
        image = doc.frames[int(index)].image
        mask = _fill_mask(image, int(x), int(y), max(0, int(tolerance)))
        return _apply_mask(doc, sel, index, mask, _rgba(color), mode)


@register_op
class DrawShape:
    """Line, rectangle or ellipse from one drag, drawn or erased.

    One op with a `kind` rather than three ops, because the three differ by a
    single `ImageDraw` call and nothing else -- the same reasoning that makes
    Pencil and Eraser one `StrokeTool` in the frontend. An unknown kind declines
    rather than guessing, so a typo in a future tool surfaces as "nothing to do"
    instead of quietly drawing a rectangle.

    `mode="erase"` arrived with the fill bucket's and cost the same: nothing.
    Erasing a filled rectangle is the shortest way to clear an area, which is
    the second half of the question that asked for erase-fill in the first place.
    """

    id = "paint.shape"
    label = "Shape"
    accel = None
    needs_selection = False
    in_menu = False
    params = ()

    def label_for(self, mode: str = "paint", **_) -> str:
        return "Erase Shape" if mode == "erase" else self.label

    def apply(self, doc: Document, sel: Selection, index: int = 0, kind: str = "line",
              x0: int = 0, y0: int = 0, x1: int = 0, y1: int = 0,
              size: int = 1, color: Color = (0, 0, 0, 255),
              filled: bool = False, mode: str = "paint", **_) -> OpResult:
        mask = _shape_mask(doc.size, kind, (x0, y0, x1, y1), size, bool(filled))
        return _apply_mask(doc, sel, index, mask, _rgba(color), mode)


def _move_pixels(image: Image.Image, x: int, y: int, width: int, height: int,
                 dx: int, dy: int) -> Image.Image | None:
    """Lift the region out of `image`, clear it, and land it `(dx, dy)` away.

    Both halves are `_composite` calls on the same frame, which is exactly why
    the commit path above had to stop being mask-shaped: a move is not one
    coverage mask, it is two operations in a fixed order.

    **The pixels are this frame's own.** That is what makes a move different
    from a paste applied across frames: paste stamps one image everywhere, move
    shifts whatever each frame happens to have in that rectangle. Nudging a
    sprite three pixels left through a whole animation is the case, and stamping
    frame 7's version of it over the other twenty would be the wrong answer to it.

    Pixels pushed off the canvas are lost, like a crop's. Undo has them, and the
    alternative -- growing the canvas to keep them -- would silently change the
    document's size behind a gesture that said nothing about size.

    **A zero offset needs no special case.** Erasing the region and compositing
    the same pixels straight back into it is the identity, exactly, including
    for partial alpha and for the RGB that erase leaves under transparent
    pixels -- so `_apply_frames` sees no change and declines, which is what a
    guard here would have arranged more expensively. An earlier draft had that
    guard; a mutation run showed removing it changed nothing, which is the
    second time this session that a check turned out to be standing in front of
    a wall. `commit_float` still short-circuits an unplaced move, but for a
    different reason -- to keep "nothing to do" off the status line.
    """
    if width < 1 or height < 1:
        return None  # nothing to move -> decline
    mask = _region_mask(image.size, x, y, width, height)
    if mask is None:
        return None
    content = image.crop((x, y, x + width, y + height))
    cleared = _composite(image, mask, (0, 0, 0, 0), "erase")
    layer = _paste_layer(image.size, content, x + dx, y + dy)
    if layer is None:
        return cleared
    return _composite(cleared, layer.getchannel("A"), (0, 0, 0, 0), "paint", layer)


@register_op
class MoveRegion:
    """Shift the pixels inside a region by `(dx, dy)`, on one frame or many.

    The commit half of a floating move (ARCHITECTURE.md 28). It is deliberately
    *one* op rather than a cut followed by a paste, even though it does exactly
    that: two ops would put two entries on the undo stack, and Ctrl+Z after
    moving a sprite would give the hole back while leaving you still holding the
    sprite. One user action, one undo entry.

    Being one op is also what makes the floating preview free. The frontend
    renders a drag in progress by *running this* and displaying the result
    without committing -- no second implementation of what a move looks like,
    and no way for the preview to disagree with the outcome.
    """

    id = "paint.move"
    label = "Move"
    accel = None
    needs_selection = False
    in_menu = False  # a drag chooses the offset; four numbers would not
    params = ()

    def apply(self, doc: Document, sel: Selection, index: int = 0, frames=(),
              x: int = 0, y: int = 0, width: int = 0, height: int = 0,
              dx: int = 0, dy: int = 0, **_) -> OpResult:
        targets = tuple(frames) if frames else (int(index),)
        return _apply_frames(
            doc, sel, targets, int(index),
            lambda image: _move_pixels(image, int(x), int(y), int(width),
                                       int(height), int(dx), int(dy)))


@register_op
class CutRegion:
    """Clear the pixels inside a region, on the frame the playhead is on.

    Named for the action rather than for what it does, because what it does is
    only half of it: cut is *copy the pixels* (session state -- the clipboard
    lives in the controller and survives undo, because a clipboard that
    reverted with the document would be a clipboard nobody could rely on) plus
    *clear them* (document state -- this). Calling the op `paint.clear` and
    labelling it "Clear" would put "Undo Clear" in the menu after the user
    pressed Cut, which is an accurate description of an implementation detail
    and a wrong description of what happened.

    One frame, unlike paste. Copy can only read the frame you are looking at,
    so cut clears the frame it read; clearing frames you cannot see, on the
    strength of a selection you may have made for another reason, is the kind
    of destruction that is undoable and unnoticeable at the same time.
    """

    id = "paint.cut"
    label = "Cut"
    accel = None
    needs_selection = False
    in_menu = False  # region-driven; there is no dialog that could ask for one
    params = ()

    def apply(self, doc: Document, sel: Selection, index: int = 0,
              x: int = 0, y: int = 0, width: int = 0, height: int = 0,
              **_) -> OpResult:
        mask = _region_mask(doc.size, int(x), int(y), int(width), int(height))
        return _apply_mask(doc, sel, index, mask, (0, 0, 0, 0), "erase")


@register_op
class PasteRegion:
    """Composite a clipboard image into one or more frames at (x, y).

    The mask is the pasted image's own alpha, so this is one more entry in the
    table in ARCHITECTURE.md 23.1 rather than a new kind of edit -- see
    `_paste_layers` for the one thing it adds.

    **`frames` is a tuple, and that is the whole reason `OpResult.index`
    exists.** Stamping a sprite across an animation is the case this was asked
    for, and it is the first op here to edit several frames' pixels at once.
    The single-frame ops keep the playhead still by returning
    `Selection.single(index)`; this one cannot, because it must leave the user's
    frame selection exactly as they made it -- otherwise a second paste would
    land on one frame instead of twenty. So it states the playhead directly.
    """

    id = "paint.paste"
    label = "Paste"
    accel = None
    needs_selection = False
    in_menu = False  # the interesting argument is a clipboard, not a number
    params = ()

    def apply(self, doc: Document, sel: Selection, index: int = 0, frames=(),
              image: Image.Image | None = None, x: int = 0, y: int = 0,
              **_) -> OpResult:
        layer = _paste_layer(doc.size, image, x, y)
        if layer is None:
            return OpResult(doc, sel)
        targets = tuple(frames) if frames else (int(index),)
        return _apply_mask_frames(doc, sel, targets, int(index),
                                  layer.getchannel("A"), (0, 0, 0, 0),
                                  "paint", layer)
