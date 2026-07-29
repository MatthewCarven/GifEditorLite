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
    EllipseTool,
    EraserTool,
    EyedropperTool,
    FillTool,
    LineTool,
    PencilTool,
    RectTool,
    SelectTool,
    ShapeTool,
    default_tools,
)


class FakeContext:
    """Records what a tool asked for instead of doing it."""

    def __init__(self, frame_index: int = 2, brush_size: int = 3,
                 fg_color=(255, 0, 0, 255), fill_shapes: bool = False,
                 tolerance: int = 0, erase_mode: bool = False) -> None:
        self._frame_index = frame_index
        self._brush_size = brush_size
        self._fg_color = fg_color
        self._fill_shapes = fill_shapes
        self._tolerance = tolerance
        self._erase_mode = erase_mode
        self.commits: list[tuple[str, dict]] = []
        self.strokes: list[tuple[tuple, bool]] = []
        self.rects: list[tuple] = []
        self.picks: list[tuple[int, int]] = []
        self.regions: list = []
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

    @property
    def fill_shapes(self) -> bool:
        return self._fill_shapes

    @property
    def erase_mode(self) -> bool:
        return self._erase_mode

    @property
    def tolerance(self) -> int:
        return self._tolerance

    def commit(self, op_id: str, **params) -> None:
        self.commits.append((op_id, params))

    def pick_color(self, x: int, y: int) -> None:
        self.picks.append((x, y))

    def set_region(self, region) -> None:
        self.regions.append(None if region is None else tuple(region))

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


class TestEraseMode:
    """One checkbox, every painting tool.

    The point of the flag is that "erase" is not a colour and never could be:
    painting alpha-composites, so no colour subtracts alpha. It is the other
    branch of the same operation, which is why it reaches the strokes by
    swapping the op and the fill and shapes by a `mode` param -- the strokes
    have had two ops since M4 and the others have one.
    """

    def erasing(self, **kwargs) -> FakeContext:
        return FakeContext(erase_mode=True, **kwargs)

    def test_the_pencil_becomes_an_eraser(self):
        ctx = self.erasing()
        tool = PencilTool()
        tool.on_press(ctx, 1, 1)
        tool.on_release(ctx, 4, 4)
        (op_id, params), = ctx.commits
        assert op_id == "paint.erase"
        assert "color" not in params, "erase subtracts alpha; a colour would be a lie"

    def test_the_eraser_still_erases_with_the_box_unticked(self):
        """`or`, not the checkbox alone. Reading the flag straight would turn
        the Eraser into a pencil whenever the box happened to be off."""
        ctx = FakeContext(erase_mode=False)
        tool = EraserTool()
        tool.on_press(ctx, 1, 1)
        tool.on_release(ctx, 4, 4)
        assert ctx.commits[0][0] == "paint.erase"

    def test_the_stroke_previews_as_an_erase(self):
        """The preview is how you know before releasing. A pencil that draws a
        solid red line and then removes pixels is a worse surprise than no
        preview at all."""
        ctx = self.erasing()
        tool = PencilTool()
        tool.on_press(ctx, 1, 1)
        tool.on_drag(ctx, 2, 2)
        assert [erase for _points, erase in ctx.strokes] == [True, True]

    def test_the_fill_bucket_clears_instead(self):
        ctx = self.erasing()
        FillTool().on_press(ctx, 3, 4)
        assert ctx.commits[0][1]["mode"] == "erase"

    def test_shapes_clear_instead(self):
        ctx = self.erasing(fill_shapes=True)
        tool = RectTool()
        tool.on_press(ctx, 1, 1)
        tool.on_release(ctx, 6, 6)
        params = ctx.commits[0][1]
        assert params["mode"] == "erase"
        assert params["filled"] is True, "an erased rect still needs to be solid"

    @pytest.mark.parametrize("cls", [LineTool, RectTool, EllipseTool])
    def test_every_shape_tool_carries_it(self, cls):
        ctx = self.erasing()
        tool = cls()
        tool.on_press(ctx, 1, 1)
        tool.on_release(ctx, 5, 5)
        assert ctx.commits[0][1]["mode"] == "erase"

    def test_off_is_the_default_and_says_paint(self):
        ctx = FakeContext()
        FillTool().on_press(ctx, 1, 1)
        assert ctx.commits[0][1]["mode"] == "paint"

    def test_the_tools_it_does_not_reach_are_untouched(self):
        """Select, crop and the eyedropper edit no pixels, so the flag has
        nothing to say to them -- and must not quietly change what they commit.
        """
        for tool, expected in ((CropTool(), [("canvas.crop", ...)]), (SelectTool(), [])):
            ctx = self.erasing()
            tool.on_press(ctx, 1, 1)
            tool.on_release(ctx, 9, 9)
            assert [op for op, _ in ctx.commits] == [op for op, _ in expected]
        ctx = self.erasing()
        EyedropperTool().on_press(ctx, 2, 2)
        assert ctx.commits == []


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


class TestSelectTool:
    """The first tool whose result *outlives the gesture*.

    Everything checked here is about that: it hands the region over instead of
    committing an op, a click is a dismissal rather than a decline, and the
    provisional marquee is cleared exactly once on release -- the committed one
    is a different mechanism drawn by the canvas from state.
    """

    def test_a_drag_hands_over_the_region_and_commits_nothing(self, ctx):
        tool = SelectTool()
        tool.on_press(ctx, 10, 20)
        tool.on_drag(ctx, 30, 50)
        tool.on_release(ctx, 30, 50)
        assert ctx.regions == [(10, 20, 20, 30)]
        assert ctx.commits == []  # a selection is not an edit

    def test_a_backwards_drag_normalises_the_region(self, ctx):
        tool = SelectTool()
        tool.on_press(ctx, 40, 60)
        tool.on_release(ctx, 10, 20)
        assert ctx.regions == [(10, 20, 30, 40)]

    def test_it_matches_the_crop_box_for_the_same_drag(self, ctx):
        """Both address the edges between pixels, so the same drag has to give
        the same rectangle -- that shared convention is what would make a
        Crop-to-Selection a one-liner instead of a second set of arithmetic."""
        crop_ctx = FakeContext()
        crop = CropTool()
        crop.on_press(crop_ctx, 4, 6)
        crop.on_release(crop_ctx, 19, 26)
        select = SelectTool()
        select.on_press(ctx, 4, 6)
        select.on_release(ctx, 19, 26)
        x, y, w, h = ctx.regions[0]
        assert crop_ctx.commits == [("canvas.crop", {"x": x, "y": y,
                                                     "width": w, "height": h})]

    @pytest.mark.parametrize("end", [(10, 20), (10, 50), (40, 20)])
    def test_a_click_or_zero_area_drag_clears_the_region(self, ctx, end):
        """Unlike crop, which declines. Clicking off a selection to dismiss it
        is what a click means everywhere else, and an empty selection -- unlike
        an empty crop box -- has an obvious meaning."""
        tool = SelectTool()
        tool.on_press(ctx, 10, 20)
        tool.on_release(ctx, *end)
        assert ctx.regions == [None]
        assert ctx.commits == []

    def test_previews_the_box_while_dragging_then_clears_it_once(self, ctx):
        tool = SelectTool()
        tool.on_press(ctx, 10, 20)
        tool.on_drag(ctx, 30, 50)
        assert ctx.rects == [(10, 20, 10, 20), (10, 20, 30, 50)]
        assert ctx.clears == 0
        tool.on_release(ctx, 30, 50)
        assert ctx.clears == 1

    def test_a_cancelled_gesture_leaves_the_region_alone(self, ctx):
        """Esc mid-drag, or a window resize, must not clear a selection the
        user made earlier -- it abandons the *new* rectangle, nothing else."""
        tool = SelectTool()
        tool.on_press(ctx, 10, 20)
        tool.on_drag(ctx, 30, 50)
        tool.on_cancel(ctx)
        assert ctx.regions == []
        assert not tool.is_gesturing

    def test_a_release_with_no_press_does_nothing(self, ctx):
        SelectTool().on_release(ctx, 5, 5)
        assert ctx.regions == []

    def test_it_reports_gesturing_between_press_and_release(self, ctx):
        tool = SelectTool()
        assert not tool.is_gesturing
        tool.on_press(ctx, 1, 1)
        assert tool.is_gesturing  # so Esc abandons the drag before the tool
        tool.on_release(ctx, 9, 9)
        assert not tool.is_gesturing


class TestToolSet:
    def test_default_tools_are_keyed_by_id(self):
        tools = default_tools()
        assert set(tools) == {"select", "crop", "pencil", "eraser", "fill",
                              "line", "rect", "ellipse", "eyedropper"}
        assert all(tool.id == key for key, tool in tools.items())

    def test_pixel_tools_want_pixels_and_rect_tools_want_edges(self):
        """A brush addresses the pixel under the cursor; a crop box and a
        selection address the boundaries *between* pixels. The canvas maps each
        differently (floor vs round), so a tool declaring the wrong one paints a
        pixel off -- invisible at 1:1 zoom, a whole pixel wrong on blown-up
        pixel art.

        Derived from the palette rather than from a hardcoded list, because a
        hardcoded list is a test that silently stops covering the thing it names.
        This one did: adding fill and the three shape tools left it passing while
        checking none of them.
        """
        edge_tools = {"crop", "select"}
        tools = default_tools()
        for tid in edge_tools:
            assert tools[tid].coords == "edge", tid
        for tid, tool in tools.items():
            if tid in edge_tools:
                continue
            assert tool.coords == "pixel", tid

    def test_coords_is_always_a_known_mode(self):
        for tool in default_tools().values():
            assert tool.coords in ("pixel", "edge"), tool.id

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


# ---- the fill bucket -----------------------------------------------------


class TestFillTool:
    def test_it_commits_on_press_with_no_drag_needed(self):
        """The one committing tool that isn't a drag. There is nothing to
        preview -- the affected region depends on pixels the frontend would have
        to reimplement the op to know -- so waiting for a release would add
        latency and change nothing."""
        tool, ctx = FillTool(), FakeContext(frame_index=4, fg_color=(0, 255, 0, 255))
        tool.on_press(ctx, 7, 9)
        assert ctx.commits == [("paint.fill", {
            "index": 4, "x": 7, "y": 9, "color": (0, 255, 0, 255), "tolerance": 0,
            "mode": "paint",
        })]

    def test_it_passes_the_tolerance_from_the_context(self):
        tool, ctx = FillTool(), FakeContext(tolerance=24)
        tool.on_press(ctx, 1, 1)
        assert ctx.commits[0][1]["tolerance"] == 24

    def test_it_is_never_gesturing_so_esc_puts_it_away(self):
        tool, ctx = FillTool(), FakeContext()
        tool.on_press(ctx, 1, 1)
        assert tool.is_gesturing is False

    def test_a_drag_does_not_paint_a_trail_of_fills(self):
        """Dragging with the bucket held down would otherwise commit one
        undoable fill per pixel crossed."""
        tool, ctx = FillTool(), FakeContext()
        tool.on_press(ctx, 1, 1)
        tool.on_drag(ctx, 2, 2)
        tool.on_drag(ctx, 3, 3)
        tool.on_release(ctx, 3, 3)
        assert len(ctx.commits) == 1


# ---- shapes --------------------------------------------------------------


class TestShapeTools:
    @pytest.mark.parametrize("cls,kind", [
        (LineTool, "line"), (RectTool, "rect"), (EllipseTool, "ellipse"),
    ])
    def test_each_tool_commits_its_own_kind(self, cls, kind):
        tool, ctx = cls(), FakeContext()
        tool.on_press(ctx, 2, 3)
        tool.on_release(ctx, 7, 9)
        op_id, params = ctx.commits[0]
        assert op_id == "paint.shape"
        assert params["kind"] == kind
        assert (params["x0"], params["y0"], params["x1"], params["y1"]) == (2, 3, 7, 9)

    def test_it_carries_size_colour_and_fill_from_the_context(self):
        tool = RectTool()
        ctx = FakeContext(brush_size=5, fg_color=(1, 2, 3, 255), fill_shapes=True)
        tool.on_press(ctx, 0, 0)
        tool.on_release(ctx, 4, 4)
        params = ctx.commits[0][1]
        assert (params["size"], params["color"], params["filled"]) == (5, (1, 2, 3, 255), True)

    def test_the_preview_encloses_the_last_pixel_rather_than_bisecting_it(self):
        """A shape's coordinates are pixels; `preview_rect` draws through
        pixel *corners*. So the far edge is pushed out by one, or the marquee is
        a pixel short on each far side and the committed shape doesn't match the
        box the user drew."""
        assert ShapeTool.preview_box((2, 3), 7, 9) == (2, 3, 8, 10)

    def test_the_preview_normalises_a_backwards_drag(self):
        assert ShapeTool.preview_box((7, 9), 2, 3) == (2, 3, 8, 10)

    def test_a_single_pixel_click_still_previews_one_whole_pixel(self):
        assert ShapeTool.preview_box((4, 4), 4, 4) == (4, 4, 5, 5)

    def test_it_previews_live_while_dragging(self):
        tool, ctx = RectTool(), FakeContext()
        tool.on_press(ctx, 1, 1)
        tool.on_drag(ctx, 5, 5)
        tool.on_drag(ctx, 6, 7)
        assert ctx.rects == [(1, 1, 2, 2), (1, 1, 6, 6), (1, 1, 7, 8)]
        assert ctx.commits == []          # nothing committed until release

    def test_a_click_without_a_drag_still_marks(self):
        """Unlike crop, where an empty box would mean "crop to nothing", a 1x1
        rect is a legitimate mark -- and the op declines anyway if it changes
        nothing."""
        tool, ctx = RectTool(), FakeContext()
        tool.on_press(ctx, 3, 3)
        tool.on_release(ctx, 3, 3)
        assert len(ctx.commits) == 1

    def test_esc_mid_drag_commits_nothing_and_clears_the_marquee(self):
        tool, ctx = EllipseTool(), FakeContext()
        tool.on_press(ctx, 1, 1)
        tool.on_drag(ctx, 8, 8)
        assert tool.is_gesturing
        tool.on_cancel(ctx)
        assert not tool.is_gesturing
        assert ctx.commits == []
        assert ctx.clears >= 1

    def test_a_release_with_no_press_does_nothing(self):
        """Reachable: press on the timeline, release over the canvas."""
        tool, ctx = LineTool(), FakeContext()
        tool.on_release(ctx, 4, 4)
        assert ctx.commits == []

    def test_shapes_address_pixels_not_boundaries(self):
        """The `coords` declaration is the one thing separating a shape from a
        crop box, and it is a whole pixel at high zoom."""
        for cls in (LineTool, RectTool, EllipseTool):
            assert cls.coords == "pixel"
        assert CropTool.coords == "edge"
