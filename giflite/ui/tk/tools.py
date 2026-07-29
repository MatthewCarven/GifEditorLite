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
    tolerance   -> int              how near a colour must be for the fill bucket
    commit(op_id, **params)         run a core op (undoable)
    pick_color(x, y)                read a pixel and adopt it as the fg colour
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
"""

from __future__ import annotations

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
    def tolerance(self) -> int: ...
    def commit(self, op_id: str, **params) -> None: ...
    def pick_color(self, x: int, y: int) -> None: ...
    def preview_stroke(self, points, erase: bool = False) -> None: ...
    def preview_rect(self, box: tuple[int, int, int, int]) -> None: ...
    def clear_preview(self) -> None: ...
    def end_tool(self) -> None: ...


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

    def on_press(self, ctx: ToolContext, x: int, y: int) -> None: ...
    def on_drag(self, ctx: ToolContext, x: int, y: int) -> None: ...
    def on_release(self, ctx: ToolContext, x: int, y: int) -> None: ...

    def on_cancel(self, ctx: ToolContext) -> None:
        """Abandon the gesture in progress, committing nothing."""
        ctx.clear_preview()


class StrokeTool(Tool):
    """Freehand stroke: accumulate points, preview live, commit one op on release.

    Pencil and eraser differ only in which op they commit and whether they carry
    a colour -- everything else, including the transient point buffer, is shared.
    """

    op_id: str = ""
    erase: bool = False

    def __init__(self) -> None:
        self._points: list[tuple[int, int]] = []

    @property
    def is_gesturing(self) -> bool:
        return bool(self._points)

    def on_press(self, ctx: ToolContext, x: int, y: int) -> None:
        self._points = [(x, y)]
        ctx.preview_stroke(self._points, erase=self.erase)

    def on_drag(self, ctx: ToolContext, x: int, y: int) -> None:
        if not self._points:
            return
        # Skip duplicate samples so a still cursor doesn't pile up points.
        if (x, y) != self._points[-1]:
            self._points.append((x, y))
            ctx.preview_stroke(self._points, erase=self.erase)

    def on_release(self, ctx: ToolContext, x: int, y: int) -> None:
        if not self._points:
            return
        if (x, y) != self._points[-1]:
            self._points.append((x, y))
        params = dict(index=ctx.frame_index, points=tuple(self._points), size=ctx.brush_size)
        if not self.erase:
            params["color"] = ctx.fg_color
        self._points = []
        ctx.clear_preview()
        ctx.commit(self.op_id, **params)

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
    hint = "drag a rectangle on the image   |   Esc to cancel"
    # A crop box is described by its *edges*, so snap to the nearest pixel
    # boundary (and 0..src inclusive) rather than to the pixel under the cursor.
    coords = "edge"

    def __init__(self) -> None:
        self._anchor: tuple[int, int] | None = None

    @property
    def is_gesturing(self) -> bool:
        return self._anchor is not None

    def on_press(self, ctx: ToolContext, x: int, y: int) -> None:
        self._anchor = (x, y)
        ctx.preview_rect((x, y, x, y))

    def on_drag(self, ctx: ToolContext, x: int, y: int) -> None:
        if self._anchor is None:
            return
        ctx.preview_rect((self._anchor[0], self._anchor[1], x, y))

    def on_release(self, ctx: ToolContext, x: int, y: int) -> None:
        anchor, self._anchor = self._anchor, None
        ctx.clear_preview()
        if anchor is None:
            return
        left, right = sorted((anchor[0], x))
        top, bottom = sorted((anchor[1], y))
        width, height = right - left, bottom - top
        if width < 1 or height < 1:
            return  # a click, not a drag
        ctx.commit(self.op_id, x=left, y=top, width=width, height=height)

    def on_cancel(self, ctx: ToolContext) -> None:
        self._anchor = None
        ctx.clear_preview()


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
    hint = "click a region to flood-fill it   |   Tolerance sets how near a colour must be"

    def on_press(self, ctx: ToolContext, x: int, y: int) -> None:
        ctx.commit(self.op_id, index=ctx.frame_index, x=x, y=y,
                   color=ctx.fg_color, tolerance=ctx.tolerance)


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

    def __init__(self) -> None:
        self._anchor: tuple[int, int] | None = None

    @property
    def is_gesturing(self) -> bool:
        return self._anchor is not None

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

    def on_press(self, ctx: ToolContext, x: int, y: int) -> None:
        self._anchor = (x, y)
        ctx.preview_rect(self.preview_box((x, y), x, y))

    def on_drag(self, ctx: ToolContext, x: int, y: int) -> None:
        if self._anchor is None:
            return
        ctx.preview_rect(self.preview_box(self._anchor, x, y))

    def on_release(self, ctx: ToolContext, x: int, y: int) -> None:
        anchor, self._anchor = self._anchor, None
        ctx.clear_preview()
        if anchor is None:
            return
        # A click with no drag is committed rather than declined: a single-pixel
        # line or a 1x1 rect is a legitimate mark, and unlike crop -- where an
        # empty box would mean "crop to nothing" -- there is no degenerate case
        # to guard against. The op declines anyway if the mark changes nothing.
        ctx.commit(self.op_id, index=ctx.frame_index, kind=self.kind,
                   x0=anchor[0], y0=anchor[1], x1=x, y1=y,
                   size=ctx.brush_size, color=ctx.fg_color, filled=ctx.fill_shapes)

    def on_cancel(self, ctx: ToolContext) -> None:
        self._anchor = None
        ctx.clear_preview()


class LineTool(ShapeTool):
    id = "line"
    label = "Line"
    kind = "line"
    hint = "drag to draw a line   |   Esc to cancel"


class RectTool(ShapeTool):
    id = "rect"
    label = "Rectangle"
    kind = "rect"
    hint = "drag to draw a rectangle   |   Fill makes it solid   |   Esc to cancel"


class EllipseTool(ShapeTool):
    id = "ellipse"
    label = "Ellipse"
    kind = "ellipse"
    hint = "drag to draw an ellipse   |   Fill makes it solid   |   Esc to cancel"


class EyedropperTool(Tool):
    """Reads a pixel and adopts it as the foreground colour. Commits no op --
    it changes tool state, not the document."""

    id = "eyedropper"
    label = "Eyedropper"
    hint = "click a pixel to pick its colour"

    def on_press(self, ctx: ToolContext, x: int, y: int) -> None:
        ctx.pick_color(x, y)

    def on_drag(self, ctx: ToolContext, x: int, y: int) -> None:
        ctx.pick_color(x, y)  # live pick while dragging


def default_tools() -> dict[str, Tool]:
    """The tool set, keyed by id. One instance each (they hold only transient
    per-gesture state, reset on press). Order here is palette order."""
    return {t.id: t for t in (
        CropTool(),
        PencilTool(), EraserTool(), FillTool(),
        LineTool(), RectTool(), EllipseTool(),
        EyedropperTool(),
    )}
