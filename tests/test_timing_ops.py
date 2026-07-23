"""Timing operations: set delay, scale speed."""

from __future__ import annotations

from PIL import Image

from giflite.core.model import Document, Frame, Selection
from giflite.core.ops import get_op


def doc(durations) -> Document:
    frames = tuple(Frame.new(Image.new("RGBA", (8, 8)), d) for d in durations)
    return Document(frames, (8, 8))


def run(op_id, d, sel, **params):
    return get_op(op_id).apply(d, sel, **params)


def durations(d) -> list[int]:
    return [f.duration_ms for f in d.frames]


class TestSetDelay:
    def test_sets_all_when_no_selection(self):
        r = run("timing.set_delay", doc([100, 100, 100]), Selection.empty(), delay_ms=250)
        assert durations(r.doc) == [250, 250, 250]

    def test_sets_only_the_selection(self):
        r = run("timing.set_delay", doc([100, 100, 100]),
                Selection(frozenset({1})), delay_ms=250)
        assert durations(r.doc) == [100, 250, 100]

    def test_quantises_the_value(self):
        r = run("timing.set_delay", doc([100]), Selection.empty(), delay_ms=137)
        assert durations(r.doc) == [130]  # floored to a 10ms step

    def test_below_floor_clamps_up_to_the_minimum(self):
        r = run("timing.set_delay", doc([100]), Selection.empty(), delay_ms=5)
        assert durations(r.doc) == [20]  # floor, not a jump to 100

    def test_shares_the_image_no_pixel_allocation(self):
        d = doc([100, 100])
        r = run("timing.set_delay", d, Selection.empty(), delay_ms=200)
        assert r.doc.frames[0].image is d.frames[0].image
        assert r.doc.frames[0].image_uid == d.frames[0].image_uid


class TestScaleSpeed:
    def test_double_speed_halves_durations(self):
        r = run("timing.scale_speed", doc([100, 200, 300]), Selection.empty(), factor=2.0)
        assert durations(r.doc) == [50, 100, 150]

    def test_half_speed_doubles_durations(self):
        r = run("timing.scale_speed", doc([100, 100]), Selection.empty(), factor=0.5)
        assert durations(r.doc) == [200, 200]

    def test_extreme_speedup_floors_at_the_minimum(self):
        # the bug the quantiser fix guards: fast frames must not balloon to 100
        r = run("timing.scale_speed", doc([60, 100]), Selection.empty(), factor=10.0)
        assert durations(r.doc) == [20, 20]

    def test_scales_only_the_selection(self):
        r = run("timing.scale_speed", doc([100, 100, 100]),
                Selection(frozenset({2})), factor=2.0)
        assert durations(r.doc) == [100, 100, 50]


class TestMetadata:
    def test_ops_declare_their_params(self):
        assert get_op("timing.set_delay").params[0].name == "delay_ms"
        assert get_op("timing.scale_speed").params[0].name == "factor"

    def test_timing_group_exists(self):
        from giflite.core.ops import menu_groups
        assert "timing" in menu_groups()
        assert {op.id for op in menu_groups()["timing"]} == {
            "timing.set_delay", "timing.scale_speed",
        }
