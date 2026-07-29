"""Timing operations: set delay, scale speed."""

from __future__ import annotations

from PIL import Image

from giflite.core.model import MIN_DURATION_MS, Document, Frame, Selection
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


class TestRetimingDeclines:
    """Both timing ops used to return a fresh Document unconditionally, so
    setting the delay that was already there pushed an identity edit onto undo.
    It went unnoticed while the only way in was a dialog -- you rarely open one
    to retype the value already in it. An inline delay box asks on every commit.
    """

    def test_set_delay_to_the_value_already_there_declines(self):
        d = doc([100, 100, 100])
        assert run("timing.set_delay", d, Selection.empty(), delay_ms=100).doc is d

    def test_a_request_that_quantises_to_the_current_value_also_declines(self):
        """103ms quantises to 100ms, so the *request* differs while the result
        does not -- comparing requests rather than results would miss this."""
        d = doc([100, 100, 100])
        assert run("timing.set_delay", d, Selection.empty(), delay_ms=103).doc is d

    def test_a_real_change_still_applies(self):
        d = doc([100, 100, 100])
        assert run("timing.set_delay", d, Selection.empty(), delay_ms=250).doc is not d

    def test_a_partial_change_still_applies(self):
        """Only one frame moves; the op must not decline just because most
        frames are untouched."""
        d = doc([100, 200, 100])
        r = run("timing.set_delay", d, Selection(frozenset({1})), delay_ms=100)
        assert r.doc is not d
        assert [f.duration_ms for f in r.doc] == [100, 100, 100]

    def test_scaling_speed_by_one_declines(self):
        d = doc([100, 200, 300])
        assert run("timing.scale_speed", d, Selection.empty(), factor=1.0).doc is d

    def test_scaling_speed_that_floors_everything_to_the_same_value_declines(self):
        """Already at the floor, so a further speed-up cannot move anything."""
        d = doc([MIN_DURATION_MS] * 3)
        assert run("timing.scale_speed", d, Selection.empty(), factor=4.0).doc is d


class TestSetDelayDialogSeed:
    def test_it_seeds_from_the_frames_it_would_change(self):
        d = doc([100, 250, 250])
        op = get_op("timing.set_delay")
        assert op.default_params(d, Selection(frozenset({1, 2})))["delay_ms"] == 250

    def test_a_mixed_selection_seeds_from_the_shortest(self):
        """Retiming a mixed run is nearly always about slowing part of it down,
        so the smallest is the value you are most likely adjusting away from --
        and unlike an average it is a delay some frame actually has."""
        d = doc([300, 100, 200])
        op = get_op("timing.set_delay")
        assert op.default_params(d, Selection.empty())["delay_ms"] == 100
