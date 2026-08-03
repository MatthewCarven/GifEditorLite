"""Canvas tools: every interactive gesture on the preview (ARCHITECTURE.md 19).

A Tool is a *frontend* object. It owns a gesture on the preview and its
transient state (the points of a stroke, the anchor of a rectangle), and on
release it commits exactly one pure core op -- the "gesture commits one op"
rule the timeline drag follows too. Nothing here touches pixels; the op does
that. Some tools commit no op at all (the eyedropper only reads a pixel), which
is the whole reason "Tool" is its own concept and not just "an op with a drag".

Crop lives here as well. It was originally a bespoke mode inside the preview
canvas, written before this layer existed; folding it in leaves the frontend
with *one* interaction mechanism instead of two parallel ones, and makes the
next gesture tool (fill, shapes, pan) cost a class rather than a mode.

Tools are toolkit-neutral: they receive image-space coordinates and talk to a
`ToolContext` (duck-typed, implemented by the Tk MainWindow) rather than to Tk
directly, so the reusable interaction logic could lift to another frontend
unchanged. The context provides:

    frame_index -> int              the frame a stroke edits (the playhead)
    brush_size  -> int
    fg_color    -> (r, g, b, a)
    fill_shapes -> bool             whether a shape tool draws solid or outline
    erase_mode  -> bool             whether marks remove pixels instead of adding
    tolerance   -> int              how near a colour must be for the fill bucket
    commit(op_id, **params)         run a core op (undoable)
    pick_color(x, y)                read a pixel and adopt it as the fg colour
    set_region(region)              select a rectangle of canvas, or None
    floating     -> bool            whether a move/paste is being placed
    float_offset -> (dx, dy)        where it currently sits
    begin_move()                    lift the region off the frame
    move_float(dx, dy)              place it at an absolute offset
    preview_stroke(points, erase)   show/refresh the provisional stroke overlay
    preview_rect(box)               show/refresh a marquee, box in image pixels
    clear_preview()                 drop any overlay
    end_tool()                      put tools away, back to plain viewing

Two lifecycle hooks matter as much as the mouse ones:

    is_gesturing    True between press and release. Drives two-stage Esc (first
                    press abandons the gesture, second puts the tool away) and
                    lets the canvas know a pending gesture exists.
    on_cancel(ctx)  Abandon in-progress state without committing. Called on Esc
                    and on a window resize, which rescales and moves the image
                    so any coordinates collected so far are now stale.

Every mouse hook also receives a `Mods`: which modifier keys were down for
*that event*. Modifiers travel with the event rather than living on the
context because they are a fact about a moment, not about the session -- a
context property would be one more piece of global state the canvas had to
keep in sync with the keyboard, and it would be wrong for exactly the event
that mattered whenever it lagged. Constraining happens per sample, so holding
or releasing Shift mid-drag changes the preview from the next mouse movement
on, and the state at *release* is what commits -- the same rule every editor
the user already knows applies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol


class ToolContext(Protocol):
    @property
    def frame_index(self) -> int: ...
    @property
    def brush_size(self) -> int: ...
    @property
    def fg_color(self) -> tuple[int, int, int, int]: ...
    @property
    def fill_shapes(self) -> bool: ...
    @property
    def erase_mode(self) -> bool: ...
    @property
    def tolerance(self) -> int: ...
    def commit(self, op_id: str, **params) -> None: ...
    def pick_color(self, x: int, y: int) -> None: ...
    def set_region(self, region: tuple[int, int, int, int] | None) -> None: ...
    @property
    def floating(self) -> bool: ...
    @property
    def float_offset(self) -> tuple[int, int]: ...
    def begin_move(self) -> bool: ...
    def move_float(self, dx: int, dy: int) -> None: ...
    def preview_stroke(self, points, erase: bool = False) -> None: ...
    def preview_rect(self, box: tuple[int, int, int, int]) -> None: ...
    def clear_preview(self) -> None: ...
    def end_tool(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Mods:
    """Which modifier keys were down for a mouse event.

    Toolkit-neutral on purpose: the canvas translates its toolkit's state
    bitmask into one of these, so tools never see a Tk constant. Only `shift`
    exists because only Shift has a customer; the object (rather than a bare
    bool argument) is what makes the *next* modifier a new field instead of
    another change to every handler signature -- the API break is paid once,
    here.
    """

    shift: bool = False


NO_MODS = Mods()

# The eight compass directions a Shift-constrained line may take.
_DIRECTIONS: tuple[tuple[int, int], ...] = (
    (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))


def constrain_line(ax: int, ay: int, x: int, y: int) -> tuple[int, int]:
    """The far end of a line from (ax, ay), snapped to the nearest 45 degrees.

    Nearest by *angle*, then projected: the drag vector keeps as much of its
    length as lies along the snapped direction, so the line tracks the cursor
    instead of jumping between fixed lengths. No trigonometry -- the nearest
    of eight unit directions is the one with the largest normalised dot
    product, and the projection is that dot product again. A drag of (10, 8)
    lands diagonal at (9, 9); (10, 3) lands horizontal at (10, 0); the
    boundary between those octants is exactly 22.5 degrees, which no integer
    shortcut (the tempting `2*ady <= adx`) gets right.
    """
    dx, dy = x - ax, y - ay
    if dx == 0 and dy == 0:
        return (x, y)
    best = max(_DIRECTIONS,
               key=lambda u: (dx * u[0] + dy * u[1]) / math.hypot(u[0], u[1]))
    t = (dx * best[0] + dy * best[1]) / (best[0] ** 2 + best[1] ** 2)
    return (ax + round(t * best[0]), ay + round(t * best[1]))


def constrain_box(ax: int, ay: int, x: int, y: int) -> tuple[int, int]:
    """The far corner of a box from (ax, ay), equalised to the larger extent.

    Squares from rectangles, circles from ellipses, square crops and square
    selections -- one rule for every rubber-band. The larger of the two
    extents wins so the box grows under the cursor rather than shrinking away
    from it, and the drag's own directions are kept. A drag with no extent on
    an axis grows positive there (rightward/downward): *some* sign is needed,
    and the reading-order one is the least surprising.
    """
    dx, dy = x - ax, y - ay
    d = max(abs(dx), abs(dy))
    sx = 1 if dx >= 0 else -1
    sy = 1 if dy >= 0 else -1
    return (ax + sx * d, ay + sy * d)


class Tool:
    """Base tool. Coordinates arrive already mapped to image pixels."""

    id: str = ""
    label: str = ""
    cursor: str = "crosshair"
    # Shown in the status line while this tool is active. Lives on the tool so
    # the frontend has no per-tool if-chain.
    hint: str = "drag on the image"
    # How the canvas should turn a cursor position into an image coordinate.
    # "pixel" = the pixel under the cursor (brushes, eyedropper: they address
    # pixels). "edge" = the nearest pixel boundary (crop: it addresses the lines
    # *between* pixels). Getting this wrong is invisible at 1:1 zoom and a whole
    # pixel off at 30x, so it's declared per tool rather than assumed.
    coords: str = "pixel"

    @property
    def is_gesturing(self) -> bool:
        """Whether a press is currently outstanding. Tools holding transient
        gesture state override this; stateless ones (eyedropper) never are."""
        return False

    # `mods` defaults to none-held so a caller without a keyboard (tests, a
    # programmatic driver) can omit it -- the neutral value is a real state,
    # not a placeholder.
    def on_press(self, ctx: ToolContext, x: int, y: int,
                 mods: Mods = NO_MODS) -> None: ...
    def on_drag(self, ctx: ToolContext, x: int, y: int,
                mods: Mods = NO_MODS) -> None: ...
    def on_release(self, ctx: ToolContext, x: int, y: int,
                   mods: Mods = NO_MODS) -> None: ...

    def on_cancel(self, ctx: ToolContext) -> None:
        """Abandon the gesture in progress, committing nothing."""
        ctx.clear_preview()


class StrokeTool(Tool):
    """Freehand stroke: accumulate points, preview live, commit one op on release.

    Pencil and eraser differ only in which op they commit and whether they carry
    a colour -- everything else, including the transient point buffer, is shared.

    Erase mode is therefore free here in a way it is not for the other tools:
    strokes have had two ops since M4, so the pencil in erase mode is not a
    parameter, it is the eraser's op. The Eraser tool stays in the palette
    regardless -- erasing is common enough to deserve one click rather than two,
    and a checkbox you have to remember to turn off is a worse default than a
    tool you can see is selected.
    """

    op_id: str = ""
    erase: bool = False

    def __init__(self) -> None:
        self._points: list[tuple[int, int]] = []

    @property
    def is_gesturing(self) -> bool:
        return bool(self._points)

    def _erasing(self, ctx: ToolContext) -> bool:
        """The eraser always erases; everything else asks the checkbox.

        `or`, not the checkbox alone: with Erase ticked *off* the Eraser has to
        go on erasing, or the box would silently turn the eraser into a pencil.
        """
        return self.erase or bool(ctx.erase_mode)

    def on_press(self, ctx: ToolContext, x: int, y: int,
                 mods: Mods = NO_MODS) -> None:
        self._points = [(x, y)]
        ctx.preview_stroke(self._points, erase=self._erasing(ctx))

    def on_drag(self, ctx: ToolContext, x: int, y: int,
                mods: Mods = NO_MODS) -> None:
        if not self._points:
            return
        # Skip duplicate samples so a still cursor doesn't pile up points.
        if (x, y) != self._points[-1]:
            self._points.append((x, y))
            ctx.preview_stroke(self._points, erase=self._erasing(ctx))

    def on_release(self, ctx: ToolContext, x: int, y: int,
                   mods: Mods = NO_MODS) -> None:
        if not self._points:
            return
        if (x, y) != self._points[-1]:
            self._points.append((x, y))
        erasing = self._erasing(ctx)
        params = dict(index=ctx.frame_index, points=tuple(self._points), size=ctx.brush_size)
        if not erasing:
            params["color"] = ctx.fg_color
        self._points = []
        ctx.clear_preview()
        ctx.commit("paint.erase" if erasing else self.op_id, **params)

    def on_cancel(self, ctx: ToolContext) -> None:
        self._points = []
        ctx.clear_preview()


class PencilTool(StrokeTool):
    id = "pencil"
    label = "Pencil"
    op_id = "paint.stroke"
    erase = False


class EraserTool(StrokeTool):
    id = "eraser"
    label = "Eraser"
    op_id = "paint.erase"
    erase = True


class CropTool(Tool):
    """Rubber-band crop: drag a rectangle, commit one `canvas.crop` on release.

    Crop is coordinate-driven and typing four numbers is a poor way to choose a
    rectangle, which is why the op is registered `in_menu=False` and reached
    through a gesture instead. The anchor is kept in *image* pixels like every
    other tool, so the display mapping stays entirely in the canvas and this
    class has no idea a screen exists.

    A stray click (zero width or height) commits nothing rather than cropping to
    nothing -- the core op would decline an empty box anyway, but declining here
    keeps a pointless call off the seam.
    """

    id = "crop"
    label = "Crop"
    op_id = "canvas.crop"
    hint = "drag a rectangle on the image   |   Shift for a square   |   Esc to cancel"
    # A crop box is described by its *edges*, so snap to the nearest pixel
    # boundary (and 0..src inclusive) rather than to the pixel under the cursor.
    coords = "edge"

    def __init__(self) -> None:
        self._anchor: tuple[int, int] | None = None

    @property
    def is_gesturing(self) -> bool:
        return self._anchor is not None

    def _far(self, anchor: tuple[int, int], x: int, y: int,
             mods: Mods) -> tuple[int, int]:
        """Shift squares the box. On edge coordinates a square of edges is a
        square of pixels, so the one constraint function serves both
        conventions. A square that runs past the canvas gets the same clamp
        any marquee dragged past the edge gets (op and controller both own
        one), so at the very edge the constraint yields to the canvas --
        which is what every editor's fixed-ratio marquee does too."""
        if not mods.shift:
            return (x, y)
        return constrain_box(anchor[0], anchor[1], x, y)

    def on_press(self, ctx: ToolContext, x: int, y: int,
                 mods: Mods = NO_MODS) -> None:
        self._anchor = (x, y)
        ctx.preview_rect((x, y, x, y))

    def on_drag(self, ctx: ToolContext, x: int, y: int,
                mods: Mods = NO_MODS) -> None:
        if self._anchor is None:
            return
        fx, fy = self._far(self._anchor, x, y, mods)
        ctx.preview_rect((self._anchor[0], self._anchor[1], fx, fy))

    def on_release(self, ctx: ToolContext, x: int, y: int,
                   mods: Mods = NO_MODS) -> None:
        anchor, self._anchor = self._anchor, None
        ctx.clear_preview()
        if anchor is None:
            return
        x, y = self._far(anchor, x, y, mods)
        left, right = sorted((anchor[0], x))
        top, bottom = sorted((anchor[1], y))
        width, height = right - left, bottom - top
        if width < 1 or height < 1:
            return  # a click, not a drag
        ctx.commit(self.op_id, x=left, y=top, width=width, height=height)

    def on_cancel(self, ctx: ToolContext) -> None:
        self._anchor = None
        ctx.clear_preview()


class SelectTool(Tool):
    """Rubber-band a rectangle of canvas. Commits no op.

    The second tool after the eyedropper that changes state without touching
    the document, and the first whose state *persists*: a stroke's points and a
    crop's anchor die on release, but a region outlives the gesture, the frame
    you drew it on, and every edit that doesn't invalidate it. That is why it
    goes to the controller rather than staying here (session state,
    ARCHITECTURE.md 9), and why the canvas has to draw its marquee on every
    redraw rather than as gesture state that a redraw clears.

    `coords = "edge"`, like crop and unlike the brushes: a region is described
    by the lines between pixels, and copying "pixels 2 to 7" is the same
    rectangle crop would take (ARCHITECTURE.md 19.1.1). The two even share the
    argument list, which is what would make a future Crop-to-Selection a
    one-liner rather than a second convention.

    A click with no drag *clears* the region rather than declining like crop
    does. Crop declines because an empty crop box has no sensible meaning;
    here it has an obvious one -- clicking off a selection to dismiss it is
    what a click on empty canvas means in every editor -- and `Region`
    already reports a degenerate drag as None, so this is one branch, not two.
    """

    id = "select"
    label = "Select"
    hint = ("drag to select an area   |   Shift for a square   |   "
            "Ctrl+C copy, Ctrl+X cut, Ctrl+V paste   |   click or Esc to clear")
    coords = "edge"

    def __init__(self) -> None:
        self._anchor: tuple[int, int] | None = None

    @property
    def is_gesturing(self) -> bool:
        return self._anchor is not None

    def _far(self, anchor: tuple[int, int], x: int, y: int,
             mods: Mods) -> tuple[int, int]:
        # Same rule as crop's, for the same rubber-band, with the same yield
        # to the canvas edge (set_region clamps on the way in).
        if not mods.shift:
            return (x, y)
        return constrain_box(anchor[0], anchor[1], x, y)

    def on_press(self, ctx: ToolContext, x: int, y: int,
                 mods: Mods = NO_MODS) -> None:
        self._anchor = (x, y)
        ctx.preview_rect((x, y, x, y))

    def on_drag(self, ctx: ToolContext, x: int, y: int,
                mods: Mods = NO_MODS) -> None:
        if self._anchor is None:
            return
        fx, fy = self._far(self._anchor, x, y, mods)
        ctx.preview_rect((self._anchor[0], self._anchor[1], fx, fy))

    def on_release(self, ctx: ToolContext, x: int, y: int,
                   mods: Mods = NO_MODS) -> None:
        anchor, self._anchor = self._anchor, None
        if anchor is not None:
            x, y = self._far(anchor, x, y, mods)
        # The provisional marquee goes; the committed one is redrawn by the
        # canvas from the region itself, so for a moment there are neither and
        # then there is one. Clearing after setting the region would delete the
        # wrong overlay -- these are two different mechanisms on purpose.
        ctx.clear_preview()
        if anchor is None:
            return
        left, right = sorted((anchor[0], x))
        top, bottom = sorted((anchor[1], y))
        width, height = right - left, bottom - top
        if width < 1 or height < 1:
            ctx.set_region(None)  # a click dismisses the selection
            return
        ctx.set_region((left, top, width, height))

    def on_cancel(self, ctx: ToolContext) -> None:
        self._anchor = None
        ctx.clear_preview()


class MoveTool(Tool):
    """Drag the floating edit around. Commits nothing -- Enter does that.

    The odd one here, and the reason is the third state (ARCHITECTURE.md 28):
    every other tool either commits on release or changes a setting, while this
    one manipulates something that is neither committed nor a gesture. A drag
    places the float; letting go changes nothing; the float stays until Enter
    lands it or Esc drops it.

    **A resize or a zoom must not disturb it**, which is why `on_cancel` clears
    only the drag anchor. Every other tool has to abandon everything there,
    because their collected coordinates are screen-derived and now stale
    (§20.4). A float's offset is in *image* pixels, so nothing about the view
    can invalidate it -- the one piece of in-flight state in this program that
    genuinely survives the window moving underneath it.

    Offsets are tracked from the offset *at press time* rather than from zero,
    so a second drag nudges the float further instead of teleporting it back.
    """

    id = "move"
    label = "Move"
    cursor = "fleur"
    hint = ("drag to place it   |   arrows nudge   |   "
            "Enter to drop it, Esc to put it back")
    # Edge coordinates, like the region it moves: the offset is a difference
    # between two of them, so mixing conventions would be a half-pixel drift
    # that only shows at high zoom (§19.1.1).
    coords = "edge"

    def __init__(self) -> None:
        self._anchor: tuple[int, int] | None = None
        self._start = (0, 0)

    @property
    def is_gesturing(self) -> bool:
        return self._anchor is not None

    def on_press(self, ctx: ToolContext, x: int, y: int,
                 mods: Mods = NO_MODS) -> None:
        if not ctx.floating and not ctx.begin_move():
            return  # nothing selected to move, and nothing floating to place
        self._anchor = (x, y)
        self._start = ctx.float_offset

    def on_drag(self, ctx: ToolContext, x: int, y: int,
                mods: Mods = NO_MODS) -> None:
        if self._anchor is None:
            return
        ctx.move_float(self._start[0] + x - self._anchor[0],
                       self._start[1] + y - self._anchor[1])

    def on_release(self, ctx: ToolContext, x: int, y: int,
                   mods: Mods = NO_MODS) -> None:
        self.on_drag(ctx, x, y, mods)
        self._anchor = None

    def on_cancel(self, ctx: ToolContext) -> None:
        # The drag, not the float. See the class docstring.
        self._anchor = None


class FillTool(Tool):
    """Flood-fill the region under the click. Commits on *press*.

    The odd one out, and deliberately so: every other committing tool here is a
    drag, so it previews and commits on release. A fill has exactly one
    coordinate and nothing to preview -- there is no intermediate state to show,
    because the answer depends on pixels the frontend would have to reimplement
    the op to know. Waiting for the release would add latency and change
    nothing, so it commits immediately and `is_gesturing` stays False, which
    also means Esc puts the tool away rather than cancelling a phantom gesture.
    """

    id = "fill"
    label = "Fill"
    op_id = "paint.fill"
    hint = ("click a region to flood-fill it   |   Shift-click fills every "
            "matching pixel on the frame   |   Tolerance sets how near a colour must be")

    def on_press(self, ctx: ToolContext, x: int, y: int,
                 mods: Mods = NO_MODS) -> None:
        # Shift lifts the connectivity requirement: fill everything the seed
        # matches, reachable or not. A modifier rather than a panel setting
        # because it is a per-click intent -- and the panel already has one
        # "Fill" too many (TODO's own note).
        ctx.commit(self.op_id, index=ctx.frame_index, x=x, y=y,
                   color=ctx.fg_color, tolerance=ctx.tolerance,
                   contiguous=not mods.shift,
                   mode="erase" if ctx.erase_mode else "paint")


class ShapeTool(Tool):
    """Drag from anchor to cursor, commit one `paint.shape` on release.

    Line, rectangle and ellipse differ by a single class attribute, the same way
    Pencil and Eraser differ only by which op they commit -- the core op takes a
    `kind`, so there is one code path here and one there.

    `coords = "pixel"`, unlike crop. A shape addresses the pixels it covers, so
    a rectangle dragged from pixel 2 to pixel 7 covers both; a crop box
    addresses the boundaries *between* pixels. The two conventions look
    interchangeable and are one pixel apart, which is invisible at 1:1 and
    obvious at 30x.
    """

    id = ""
    kind = ""
    op_id = "paint.shape"
    hint = "drag on the image   |   Esc to cancel"
    coords = "pixel"
    # What Shift snaps the far point to. Boxes square up; the line overrides
    # with the 45-degree snap. A staticmethod so subclasses swap the *rule*,
    # not the plumbing around it.
    constrain = staticmethod(constrain_box)

    def __init__(self) -> None:
        self._anchor: tuple[int, int] | None = None

    @property
    def is_gesturing(self) -> bool:
        return self._anchor is not None

    def _far(self, anchor: tuple[int, int], x: int, y: int,
             mods: Mods) -> tuple[int, int]:
        if not mods.shift:
            return (x, y)
        return self.constrain(anchor[0], anchor[1], x, y)

    @staticmethod
    def preview_box(anchor: tuple[int, int], x: int, y: int) -> tuple[int, int, int, int]:
        """The marquee for a pixel-inclusive shape, in *marquee* coordinates.

        `preview_rect` draws corner-to-corner through `image_to_display(...,
        center=False)`, which returns the top-left corner of a pixel -- correct
        for crop, whose numbers are already boundaries. A shape's numbers are
        pixels, so the far edge has to be pushed out by one to enclose the last
        pixel rather than bisecting it. Without this the preview is a pixel
        short on each far side and the committed shape does not match the box
        the user just drew, which is exactly the sort of half-pixel disagreement
        ARCHITECTURE 19.1 is about.
        """
        left, right = sorted((anchor[0], x))
        top, bottom = sorted((anchor[1], y))
        return (left, top, right + 1, bottom + 1)

    def on_press(self, ctx: ToolContext, x: int, y: int,
                 mods: Mods = NO_MODS) -> None:
        self._anchor = (x, y)
        ctx.preview_rect(self.preview_box((x, y), x, y))

    def on_drag(self, ctx: ToolContext, x: int, y: int,
                mods: Mods = NO_MODS) -> None:
        if self._anchor is None:
            return
        fx, fy = self._far(self._anchor, x, y, mods)
        ctx.preview_rect(self.preview_box(self._anchor, fx, fy))

    def on_release(self, ctx: ToolContext, x: int, y: int,
                   mods: Mods = NO_MODS) -> None:
        anchor, self._anchor = self._anchor, None
        ctx.clear_preview()
        if anchor is None:
            return
        x, y = self._far(anchor, x, y, mods)
        # A click with no drag is committed rather than declined: a single-pixel
        # line or a 1x1 rect is a legitimate mark, and unlike crop -- where an
        # empty box would mean "crop to nothing" -- there is no degenerate case
        # to guard against. The op declines anyway if the mark changes nothing.
        ctx.commit(self.op_id, index=ctx.frame_index, kind=self.kind,
                   x0=anchor[0], y0=anchor[1], x1=x, y1=y,
                   size=ctx.brush_size, color=ctx.fg_color, filled=ctx.fill_shapes,
                   mode="erase" if ctx.erase_mode else "paint")

    def on_cancel(self, ctx: ToolContext) -> None:
        self._anchor = None
        ctx.clear_preview()


class LineTool(ShapeTool):
    id = "line"
    label = "Line"
    kind = "line"
    hint = "drag to draw a line   |   Shift snaps to 45\N{DEGREE SIGN}   |   Esc to cancel"
    constrain = staticmethod(constrain_line)


class RectTool(ShapeTool):
    id = "rect"
    label = "Rectangle"
    kind = "rect"
    hint = ("drag to draw a rectangle   |   Shift for a square   |   "
            "Fill makes it solid   |   Esc to cancel")


class EllipseTool(ShapeTool):
    id = "ellipse"
    label = "Ellipse"
    kind = "ellipse"
    hint = ("drag to draw an ellipse   |   Shift for a circle   |   "
            "Fill makes it solid   |   Esc to cancel")


class EyedropperTool(Tool):
    """Reads a pixel and adopts it as the foreground colour. Commits no op --
    it changes tool state, not the document."""

    id = "eyedropper"
    label = "Eyedropper"
    hint = "click a pixel to pick its colour"

    def on_press(self, ctx: ToolContext, x: int, y: int,
                 mods: Mods = NO_MODS) -> None:
        ctx.pick_color(x, y)

    def on_drag(self, ctx: ToolContext, x: int, y: int,
                mods: Mods = NO_MODS) -> None:
        ctx.pick_color(x, y)  # live pick while dragging


def default_tools() -> dict[str, Tool]:
    """The tool set, keyed by id. One instance each (they hold only transient
    per-gesture state, reset on press). Order here is palette order."""
    return {t.id: t for t in (
        SelectTool(), MoveTool(), CropTool(),
        PencilTool(), EraserTool(), FillTool(),
        LineTool(), RectTool(), EllipseTool(),
        EyedropperTool(),
    )}
