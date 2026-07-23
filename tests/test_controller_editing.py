"""Editing through the controller: run_op, undo/redo, dirty, event contract.

Driven through the fake frontend, so these double as regression tests for the
frontend seam -- a menu-driven editor talks to exactly this surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from giflite.app import events as ev
from giflite.app.controller import AppController
from giflite.core.model import Selection
from tests.conftest import make_gif
from tests.fake_frontend import FakeFrontend


@pytest.fixture
def loaded(tmp_path: Path):
    controller = AppController()
    frontend = FakeFrontend().attach(controller)
    make_gif(tmp_path / "a.gif", frames=5)
    controller.open(tmp_path / "a.gif")
    frontend.clear()
    return controller, frontend


class TestRunOp:
    def test_delete_removes_frames(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({1, 2})))
        controller.run_op("frames.delete")
        assert controller.frame_count == 3

    def test_op_emits_one_doc_changed_carrying_everything(self, loaded):
        controller, frontend = loaded
        controller.set_selection(Selection.single(2))
        frontend.clear()
        controller.run_op("frames.duplicate")
        assert frontend.count(ev.DOC_CHANGED) == 1
        payload = frontend.last(ev.DOC_CHANGED).payload
        assert set(payload) == {"doc", "selection", "index", "reason"}
        assert payload["reason"] == "op:frames.duplicate"

    def test_op_with_no_selection_is_refused_with_a_message(self, loaded):
        controller, frontend = loaded
        controller.set_selection(Selection.empty())
        frontend.clear()
        controller.run_op("frames.delete")
        assert controller.frame_count == 5  # unchanged
        assert frontend.count(ev.STATUS) == 1
        assert frontend.count(ev.DOC_CHANGED) == 0

    def test_deleting_all_frames_is_refused(self, loaded):
        controller, frontend = loaded
        controller.set_selection(Selection(frozenset(range(5))))
        frontend.clear()
        controller.run_op("frames.delete")
        assert controller.frame_count == 5
        assert "nothing to do" in frontend.last(ev.STATUS).payload["message"]

    def test_playhead_follows_the_edit(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection.single(3))
        controller.run_op("frames.duplicate")
        # the new copy lands at index 4 and becomes the selection/playhead
        assert controller.index == 4

    def test_reverse_needs_no_selection(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection.empty())
        controller.run_op("frames.reverse")
        assert controller.frame_count == 5  # ran, didn't refuse


class TestUndoRedo:
    def test_undo_restores_the_previous_document(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({0, 1})))
        controller.run_op("frames.delete")
        assert controller.frame_count == 3
        controller.undo()
        assert controller.frame_count == 5

    def test_redo_reapplies(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({0, 1})))
        controller.run_op("frames.delete")
        controller.undo()
        controller.redo()
        assert controller.frame_count == 3

    def test_undo_restores_selection_and_playhead_too(self, loaded):
        controller, _ = loaded
        controller.seek(4)
        controller.set_selection(Selection.single(4))
        controller.run_op("frames.duplicate")
        # now edited; undo should bring back the pre-op selection and index
        controller.undo()
        assert controller.selection.ordered == (4,)
        assert controller.index == 4

    def test_undo_labels_track_the_ops(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        assert controller.can_undo
        assert controller.undo_label == "Duplicate Frames"
        assert not controller.can_redo
        controller.undo()
        assert controller.redo_label == "Duplicate Frames"

    def test_nothing_to_undo_on_a_fresh_document(self, loaded):
        controller, _ = loaded
        assert not controller.can_undo
        controller.undo()  # harmless no-op
        assert controller.frame_count == 5

    def test_a_new_edit_after_undo_discards_redo(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        controller.undo()
        controller.set_selection(Selection.single(1))
        controller.run_op("frames.delete")
        assert not controller.can_redo


class TestDirty:
    def test_freshly_opened_is_clean(self, loaded):
        controller, _ = loaded
        assert not controller.dirty

    def test_edit_sets_dirty_and_updates_title(self, loaded):
        controller, frontend = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        assert controller.dirty
        assert frontend.last(ev.TITLE_CHANGED).payload["dirty"] is True

    def test_undo_back_to_open_clears_dirty(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        controller.undo()
        assert not controller.dirty


class TestCanRun:
    def test_can_run_reflects_selection(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection.empty())
        assert not controller.can_run("frames.delete")
        controller.set_selection(Selection.single(0))
        assert controller.can_run("frames.delete")

    def test_reverse_can_always_run_with_a_document(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection.empty())
        assert controller.can_run("frames.reverse")

    def test_nothing_can_run_without_a_document(self):
        controller = AppController()
        assert not controller.can_run("frames.delete")
        assert not controller.can_run("frames.reverse")


class TestClockStaysInSync:
    def test_playback_after_delete_uses_new_durations(self, loaded):
        controller, _ = loaded
        # delete down to 2 frames, then play; must not index past the new end
        controller.set_selection(Selection(frozenset({0, 1, 2})))
        controller.run_op("frames.delete")
        assert controller.frame_count == 2
        controller.play()
        for _ in range(10):
            controller.tick(100)
        assert 0 <= controller.index < controller.frame_count
