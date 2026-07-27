"""The tool layer, headless.

`ui/tk/tools.py` deliberately imports no toolkit and talks only to a duck-typed
`ToolContext`, so the whole interaction layer -- including crop, now that it's a
tool rather than a canvas mode -- is testable without a display. If these tests
ever need Tk, the seam has leaked.

Coordinates here are image pixels, which is what a real tool receives: the
display mapping is the canvas's job and is covered by the Xvfb smoke test.
"""

from __future__ import annotations

import pytest

from giflite.ui.tk.tools import (
    CropTool,
    EraserTool,
    EyedropperTool,
    PencilTool,
    default_tools,
)


class FakeContext:
    """Records what a tool asked for instead of doing it."""

    def __init__(self, frame_index: int = 2, brush_size: int = 3,
                 fg_color=(255, 0, 0, 255)) -> None:
        self._frame_index = frame_index
        self._brush_size = brush_size
        self._fg_color = fg_color
        self.commits: list[tuple[str, dict]] = []
        self.strokes: list[tuple[tuple, bool]] = []
        self.rects: list[tuple] = []
        self.picks: list[tuple[int, int]] = []
        self.clears = 0
        self.ended = 0

    @property
    def frame_index(self) -> int:
        return self._frame_index

    @property
    def brush_size(self) -> int:
        return self._brush_size

    @property
    def fg_color(self):
        return self._fg_color

    def commit(self, op_id: str, **params) -> None:
        self.commits.append((op_id, params))

    def pick_color(self, x: int, y: int) -> None:
        self.picks.append((x, y))

    def preview_stroke(self, points, erase: bool = False) -> None:
        self.strokes.append((tuple(points), erase))

    def preview_rect(self, box) -> None:
        self.rects.append(tuple(box))

    def clear_preview(self) -> None:
        self.clears += 1

    def end_tool(self) -> None:
        self.ended += 1


@pytest.fixture
def ctx() -> FakeContext:
    return FakeContext()


class TestCropTool:
    def test_a_drag_commits_one_crop_with_the_dragged_box(self, ctx):
        tool = CropTool()
        tool.on_press(ctx, 10, 20)
        tool.on_drag(ctx, 30, 50)
        tool.on_release(ctx, 30, 50)
        assert ctx.commits == [
            ("canvas.crop", {"x": 10, "y": 20, "width": 20, "height": 30})
        ]

    def test_previews_the_box_while_dragging(self, ctx):
        tool = CropTool()
        tool.on_press(ctx, 10, 20)
        tool.on_drag(ctx, 30, 50)
        assert ctx.rects == [(10, 20, 10, 20), (10, 20, 30, 50)]

    def test_a_backwards_drag_normalises_the_box(self, ctx):
        """Dragging up-left must give the same rectangle as down-right, not a
        negative width the op would then have to defend against."""
        tool = CropTool()
        tool.on_press(ctx, 40, 60)
        tool.on_release(ctx, 10, 20)
        assert ctx.commits == [
            ("canvas.crop", {"x": 10, "y": 20, "width": 30, "height": 40})
        ]

    @pytest.mark.parametrize("end", [(10, 20), (10, 50), (40, 20)])
    def test_a_click_or_zero_area_drag_commits_nothing(self, ctx, end):
        tool = CropTool()
        tool.on_press(ctx, 10, 20)
        tool.on_release(ctx, *end)
        assert ctx.commits == []

    def test_release_without_press_commits_nothing(self, ctx):
        """A release can arrive with no press behind it -- a drag begun outside
        the widget, or after a resize cancelled the gesture."""
        CropTool().on_release(ctx, 30, 50)
        assert ctx.commits == []

    def test_cancel_drops_the_gesture_and_the_overlay(self, ctx):
        tool = CropTool()
        tool.on_press(ctx, 10, 20)
        tool.on_drag(ctx, 30, 50)
        tool.on_cancel(ctx)
        assert not tool.is_gesturing
        assert ctx.clears == 1
        # And a stray release afterwards must not resurrect it.
        tool.on_release(ctx, 30, 50)
        assert ctx.commits == []

    def test_is_gesturing_tracks_press_to_release(self, ctx):
        tool = CropTool()
        assert not tool.is_gesturing
        tool.on_press(ctx, 1, 1)
        assert tool.is_gesturing
        tool.on_release(ctx, 9, 9)
        assert not tool.is_gesturing


class TestStrokeTools:
    def test_pencil_commits_a_stroke_with_colour_and_size(self, ctx):
        tool = PencilTool()
        tool.on_press(ctx, 1, 1)
        tool.on_drag(ctx, 2, 2)
        tool.on_release(ctx, 3, 3)
        (op_id, params), = ctx.commits
        assert op_id == "paint.stroke"
        assert params == {
            "index": 2, "points": ((1, 1), (2, 2), (3, 3)),
            "size": 3, "color": (255, 0, 0, 255),
        }

    def test_eraser_commits_no_colour(self, ctx):
        """Erase subtracts alpha, so a colour would be meaningless -- passing one
        would also make the op's signature lie about what it does."""
        tool = EraserTool()
        tool.on_press(ctx, 1, 1)
        tool.on_release(ctx, 4, 4)
        (op_id, params), = ctx.commits
        assert op_id == "paint.erase"
        assert "color" not in params

    def test_duplicate_samples_are_skipped(self, ctx):
        tool = PencilTool()
        tool.on_press(ctx, 5, 5)
        tool.on_drag(ctx, 5, 5)
        tool.on_drag(ctx, 5, 5)
        tool.on_release(ctx, 5, 5)
        (_op, params), = ctx.commits
        assert params["points"] == ((5, 5),)

    def test_cancel_mid_stroke_commits_nothing(self, ctx):
        """The guard a window resize relies on: geometry moved, so the points
        collected so far would paint in the wrong place."""
        tool = PencilTool()
        tool.on_press(ctx, 1, 1)
        tool.on_drag(ctx, 2, 2)
        tool.on_cancel(ctx)
        tool.on_release(ctx, 3, 3)
        assert ctx.commits == []
        assert not tool.is_gesturing


class TestEyedropper:
    def test_picks_on_press_and_while_dragging(self, ctx):
        tool = EyedropperTool()
        tool.on_press(ctx, 7, 8)
        tool.on_drag(ctx, 9, 10)
        assert ctx.picks == [(7, 8), (9, 10)]

    def test_commits_no_op(self, ctx):
        """The whole reason Tool is its own concept: this one changes tool state,
        not the document."""
        tool = EyedropperTool()
        tool.on_press(ctx, 7, 8)
        tool.on_release(ctx, 7, 8)
        assert ctx.commits == []

    def test_never_counts_as_gesturing(self, ctx):
        tool = EyedropperTool()
        tool.on_press(ctx, 7, 8)
        assert not tool.is_gesturing  # so Esc puts it away rather than "cancelling"


class TestToolSet:
    def test_default_tools_are_keyed_by_id(self):
        tools = default_tools()
        assert set(tools) == {"crop", "pencil", "eraser", "eyedropper"}
        assert all(tool.id == key for key, tool in tools.items())

    def test_every_tool_has_a_label_and_a_hint(self):
        """Both are user-visible (palette, status line), so an empty one is a bug
        that would only show up by eye."""
        for tool in default_tools().values():
            assert tool.label and tool.hint

    def test_no_toolkit_import(self):
        """Belt and braces alongside tests/test_boundaries.py: that test exempts
        all of ui/tk, but this module is meant to stay portable."""
        source = __import__("giflite.ui.tk.tools", fromlist=["x"]).__file__
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        for banned in ("import tkinter", "from tkinter", "from PIL"):
            assert banned not in text, banned
