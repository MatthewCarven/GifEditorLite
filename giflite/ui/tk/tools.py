"""Canvas tools: the interactive half of painting (ARCHITECTURE.md 19).

A Tool is a *frontend* object. It owns a gesture on the preview and its
transient state (the points of the stroke in progress), and on release it
commits exactly one pure core op -- the same "gesture commits one op" rule the
timeline drag and crop already follow. Nothing here touches pixels; the op does
that. Some tools commit no op at all (the eyedropper only reads a pixel), which
is the whole reason "Tool" is its own concept and not just "an op with a drag".

Tools are toolkit-neutral: they receive image-space coordinates and talk to a
`ToolContext` (duck-typed, implemented by the Tk MainWindow) rather than to Tk
directly, so the reusable interaction logic could lift to another frontend
unchanged. The context provides:

    frame_index -> int              the frame a stroke edits (the playhead)
    brush_size  -> int
    fg_color    -> (r, g, b, a)
    commit(op_id, **params)         run a core op (undoable)
    pick_color(x, y)                read a pixel and adopt it as the fg colour
    preview(points, erase=False)    show/refresh the provisional overlay
    clear_preview()                 drop the overlay
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
    def commit(self, op_id: str, **params) -> None: ...
    def pick_color(self, x: int, y: int) -> None: ...
    def preview(self, points, erase: bool = False) -> None: ...
    def clear_preview(self) -> None: ...


class Tool:
    """Base tool. Coordinates arrive already mapped to image pixels."""

    id: str = ""
    label: str = ""
    cursor: str = "crosshair"

    def on_press(self, ctx: ToolContext, x: int, y: int) -> None: ...
    def on_drag(self, ctx: ToolContext, x: int, y: int) -> None: ...
    def on_release(self, ctx: ToolContext, x: int, y: int) -> None: ...


class StrokeTool(Tool):
    """Freehand stroke: accumulate points, preview live, commit one op on release.

    Pencil and eraser differ only in which op they commit and whether they carry
    a colour -- everything else, including the transient point buffer, is shared.
    """

    op_id: str = ""
    erase: bool = False

    def __init__(self) -> None:
        self._points: list[tuple[int, int]] = []

    def on_press(self, ctx: ToolContext, x: int, y: int) -> None:
        self._points = [(x, y)]
        ctx.preview(self._points, erase=self.erase)

    def on_drag(self, ctx: ToolContext, x: int, y: int) -> None:
        if not self._points:
            return
        # Skip duplicate samples so a still cursor doesn't pile up points.
        if (x, y) != self._points[-1]:
            self._points.append((x, y))
            ctx.preview(self._points, erase=self.erase)

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


class EyedropperTool(Tool):
    """Reads a pixel and adopts it as the foreground colour. Commits no op --
    it changes tool state, not the document."""

    id = "eyedropper"
    label = "Eyedropper"
    cursor = "crosshair"

    def on_press(self, ctx: ToolContext, x: int, y: int) -> None:
        ctx.pick_color(x, y)

    def on_drag(self, ctx: ToolContext, x: int, y: int) -> None:
        ctx.pick_color(x, y)  # live pick while dragging


def default_tools() -> dict[str, Tool]:
    """The v1 tool set, keyed by id. One instance each (they hold only transient
    per-stroke state, reset on press)."""
    return {t.id: t for t in (PencilTool(), EraserTool(), EyedropperTool())}
