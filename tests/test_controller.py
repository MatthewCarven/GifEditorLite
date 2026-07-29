"""Controller and event-contract tests, driven through the fake frontend.

These are the regression tests for the frontend seam. If a change here forces
an edit to the Tk layer, the seam has leaked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from giflite.app import events as ev
from giflite.app.controller import AppController
from giflite.core.model import Selection
from tests.fake_frontend import FakeFrontend


@pytest.fixture
def wired():
    controller = AppController()
    frontend = FakeFrontend().attach(controller)
    return controller, frontend


class TestEmptyState:
    def test_starts_with_no_document(self, wired):
        controller, _ = wired
        assert controller.doc is None
        assert controller.frame_count == 0
        assert controller.frame_image() is None

    def test_seeking_an_empty_controller_is_harmless(self, wired):
        controller, frontend = wired
        controller.seek(7)
        assert controller.index == 0
        assert frontend.count(ev.PLAYHEAD_MOVED) == 0


class TestOpen:
    def test_loads_and_selects_the_first_frame(self, wired, gif_path: Path):
        controller, _ = wired
        assert controller.open(gif_path) is True
        assert controller.frame_count == 6
        assert controller.index == 0
        assert controller.selection.ordered == (0,)
        assert controller.path == gif_path

    def test_emits_exactly_one_doc_changed(self, wired, gif_path: Path):
        """The ordering contract in ARCHITECTURE.md 9.

        One event carrying doc, selection and index together. Splitting them
        lets a frontend restyle the timeline against the previous document.
        """
        controller, frontend = wired
        controller.open(gif_path)
        assert frontend.count(ev.DOC_CHANGED) == 1
        payload = frontend.last(ev.DOC_CHANGED).payload
        assert set(payload) == {"doc", "selection", "index", "reason"}
        assert payload["reason"] == "open"
        assert payload["index"] == 0

    def test_does_not_emit_a_separate_selection_change(self, wired, gif_path: Path):
        controller, frontend = wired
        controller.open(gif_path)
        assert frontend.count(ev.SELECTION_CHANGED) == 0

    def test_title_carries_state_not_a_formatted_string(self, wired, gif_path: Path):
        controller, frontend = wired
        controller.open(gif_path)
        payload = frontend.last(ev.TITLE_CHANGED).payload
        assert payload == {"path": gif_path, "dirty": False}


class TestOpenFailures:
    def test_missing_file_reports_an_error_rather_than_raising(self, wired, tmp_path):
        controller, frontend = wired
        assert controller.open(tmp_path / "nope.gif") is False
        assert frontend.count(ev.ERROR) == 1
        assert controller.doc is None

    def test_unknown_extension_is_reported(self, wired, tmp_path: Path):
        controller, frontend = wired
        path = tmp_path / "thing.xyz"
        path.write_bytes(b"not an animation")
        assert controller.open(path) is False
        assert "No reader" in str(frontend.last(ev.ERROR).payload["exception"])

    def test_corrupt_file_is_reported(self, wired, tmp_path: Path):
        controller, frontend = wired
        path = tmp_path / "broken.gif"
        path.write_bytes(b"GIF89a not really")
        assert controller.open(path) is False
        assert frontend.count(ev.ERROR) == 1

    def test_a_failed_open_leaves_the_previous_document_intact(
        self, wired, gif_path: Path, tmp_path: Path
    ):
        controller, _ = wired
        controller.open(gif_path)
        controller.open(tmp_path / "nope.gif")
        assert controller.frame_count == 6
        assert controller.path == gif_path


class TestSeek:
    def test_clamps_to_the_last_frame(self, wired, gif_path: Path):
        controller, _ = wired
        controller.open(gif_path)
        controller.seek(999)
        assert controller.index == 5

    def test_clamps_negatives_to_zero(self, wired, gif_path: Path):
        controller, _ = wired
        controller.open(gif_path)
        controller.seek(-4)
        assert controller.index == 0

    def test_a_no_op_seek_stays_quiet(self, wired, gif_path: Path):
        controller, frontend = wired
        controller.open(gif_path)
        controller.seek(3)
        frontend.clear()
        controller.seek(3)
        assert frontend.count(ev.PLAYHEAD_MOVED) == 0


class TestSelection:
    def test_out_of_range_selection_is_clamped_on_the_way_in(self, wired, gif_path):
        controller, _ = wired
        controller.open(gif_path)
        controller.set_selection(Selection.span(3, 99))
        assert controller.selection.ordered == (3, 4, 5)

    def test_a_no_op_selection_stays_quiet(self, wired, gif_path: Path):
        controller, frontend = wired
        controller.open(gif_path)
        controller.set_selection(Selection.single(2))
        frontend.clear()
        controller.set_selection(Selection.single(2))
        assert frontend.count(ev.SELECTION_CHANGED) == 0


class TestClose:
    def test_returns_to_the_empty_state(self, wired, gif_path: Path):
        controller, frontend = wired
        controller.open(gif_path)
        controller.seek(4)
        controller.close()
        assert controller.doc is None
        assert controller.index == 0
        assert controller.frame_image() is None
        assert frontend.last(ev.TITLE_CHANGED).payload["path"] is None


class TestPlayheadSurvivesDocumentChanges:
    def test_reopening_a_shorter_file_clamps_the_playhead(self, wired, tmp_path: Path):
        """The M2 crash, caught at M0.

        Park on frame 5, load something with 3 frames, and an unclamped index
        walks straight off the end on the next redraw. The clamp lives in the
        one method that emits DOC_CHANGED, so every path gets it for free.
        """
        from tests.conftest import make_gif

        controller, _ = wired
        controller.open(make_gif(tmp_path / "long.gif", frames=6))
        controller.seek(5)
        controller.open(make_gif(tmp_path / "short.gif", frames=3))
        assert controller.index <= controller.frame_count - 1
        assert controller.frame_image() is not None


class TestFrameDelay:
    """The fast path a frontend needs to put a delay box on screen.

    `timing.set_delay` has existed since M4; what's tested here is the state
    derivation and the *scope policy* around it, which live in the controller so
    a second frontend gets the same answers rather than re-deriving them.
    """

    @pytest.fixture
    def loaded(self, wired, tmp_path):
        from tests.conftest import make_gif
        controller, frontend = wired
        controller.open(make_gif(tmp_path / "d.gif", frames=5,
                                 durations=[100, 100, 200, 300, 100]))
        return controller, frontend

    def test_current_delay_is_the_playhead_frames_own(self, loaded):
        controller, _ = loaded
        controller.seek(3)
        assert controller.current_delay_ms == 300
        controller.seek(0)
        assert controller.current_delay_ms == 100

    def test_current_delay_is_not_the_total(self, loaded):
        """They coincide on a one-frame GIF, which is how the status line got
        away with showing only the total for this long."""
        controller, _ = loaded
        assert controller.current_delay_ms != controller.doc.total_duration_ms

    def test_no_document_has_no_delay(self, wired):
        controller, _ = wired
        assert controller.current_delay_ms is None
        assert controller.target_delay_ms is None
        assert controller.delay_targets == ()

    def test_with_no_selection_the_target_is_the_playhead_frame_alone(self, loaded):
        """Not the whole animation. The menu op treats "no selection" as "all",
        which is right behind a dialog and wrong for an inline box that reads as
        "this frame"."""
        controller, _ = loaded
        controller.set_selection(Selection.empty())
        controller.seek(2)
        assert controller.delay_targets == (2,)

    def test_stepping_away_from_the_selection_follows_the_playhead(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({0, 1})))
        controller.seek(3)                       # standing outside the selection
        assert controller.delay_targets == (3,)
        assert controller.target_delay_ms == 300

    def test_standing_inside_the_selection_keeps_it(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({1, 3})))
        controller.seek(3)                       # standing inside it
        assert controller.delay_targets == (1, 3)

    def test_opening_a_file_does_not_leave_the_box_pointed_at_frame_zero(self, loaded):
        """The concrete form of the trap: open, arrow forward, and the box must
        follow rather than silently still mean frame 0."""
        controller, _ = loaded
        assert controller.selection.ordered == (0,)   # what open() leaves behind
        controller.seek(3)
        assert controller.delay_targets == (3,)

    def test_with_a_selection_the_targets_are_the_selection(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({1, 3})))
        controller.seek(1)   # standing inside it -- see the two tests above
        assert controller.delay_targets == (1, 3)

    def test_target_delay_is_the_shared_value(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({0, 1})))   # both 100
        assert controller.target_delay_ms == 100

    def test_target_delay_is_none_when_the_targets_disagree(self, loaded):
        """"Mixed" has to be representable: a single number would be wrong for
        most of them, and blank is the one display that isn't a lie."""
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({0, 3})))   # 100 and 300
        assert controller.target_delay_ms is None

    def test_setting_with_no_selection_retimes_only_that_frame(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection.empty())
        controller.seek(2)
        controller.set_frame_delay(500)
        assert [f.duration_ms for f in controller.doc] == [100, 100, 500, 300, 100]

    def test_setting_with_a_selection_retimes_all_of_it(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({0, 4})))
        controller.set_frame_delay(250)
        assert [f.duration_ms for f in controller.doc] == [250, 100, 200, 300, 250]

    def test_it_is_one_undoable_edit(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({0, 1, 2})))
        before = [f.duration_ms for f in controller.doc]
        controller.set_frame_delay(400)
        controller.undo()
        assert [f.duration_ms for f in controller.doc] == before

    def test_it_floors_at_the_minimum_rather_than_accepting_anything(self, loaded):
        from giflite.core.model import MIN_DURATION_MS
        controller, _ = loaded
        controller.set_selection(Selection.empty())
        controller.seek(0)
        controller.set_frame_delay(1)
        assert controller.doc[0].duration_ms == MIN_DURATION_MS

    def test_setting_the_value_already_there_changes_nothing(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection.empty())
        controller.seek(2)
        before = controller.doc
        controller.set_frame_delay(200)
        assert controller.doc is before      # the op declined
        assert not controller.dirty

    def test_a_declined_set_does_not_leave_a_frame_selected(self, loaded):
        """The scoping selects the playhead frame to run the op. If the op then
        declines, that selection was never the user's and has to go back."""
        controller, _ = loaded
        controller.seek(2)
        controller.set_selection(Selection.empty())
        controller.set_frame_delay(200)      # already 200 -> declines
        assert controller.selection == Selection.empty()

    def test_a_successful_set_with_no_selection_does_select_the_frame(self, loaded):
        """The other half of the same trade, and consistent with the paint ops,
        which also hand back `Selection.single(index)`."""
        controller, _ = loaded
        controller.seek(2)
        controller.set_selection(Selection.empty())
        controller.set_frame_delay(500)
        assert controller.selection.ordered == (2,)

    def test_setting_with_no_document_is_a_no_op_not_a_crash(self, wired):
        controller, _ = wired
        controller.set_frame_delay(100)      # must not raise
        assert controller.doc is None
