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
