"""Saving through the controller: dirty clears, path updates, events fire."""

from __future__ import annotations

from pathlib import Path

import pytest

from giflite.app import events as ev
from giflite.app.controller import AppController
from giflite.core.io.gif_read import read_gif
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
    return controller, frontend, tmp_path


class TestSaveAs:
    def test_writes_a_file(self, loaded):
        controller, _, tmp_path = loaded
        out = tmp_path / "out.gif"
        assert controller.save_as(out) is True
        assert out.exists()
        assert len(read_gif(out)) == 5

    def test_updates_the_path(self, loaded):
        controller, _, tmp_path = loaded
        out = tmp_path / "renamed.gif"
        controller.save_as(out)
        assert controller.path == out

    def test_announces_the_save(self, loaded):
        controller, frontend, tmp_path = loaded
        controller.save_as(tmp_path / "out.gif")
        assert "Saved out.gif" in frontend.last(ev.STATUS).payload["message"]


class TestDirtyOnSave:
    def test_saving_clears_dirty(self, loaded):
        controller, _, tmp_path = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        assert controller.dirty
        controller.save_as(tmp_path / "out.gif")
        assert not controller.dirty

    def test_title_event_reflects_clean_after_save(self, loaded):
        controller, frontend, tmp_path = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        frontend.clear()
        controller.save_as(tmp_path / "out.gif")
        assert frontend.last(ev.TITLE_CHANGED).payload["dirty"] is False

    def test_editing_after_save_is_dirty_again(self, loaded):
        controller, _, tmp_path = loaded
        controller.save_as(tmp_path / "out.gif")
        assert not controller.dirty
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        assert controller.dirty

    def test_undo_back_to_saved_state_is_clean(self, loaded):
        controller, _, tmp_path = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        controller.save_as(tmp_path / "out.gif")  # saved after one edit
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.delete")
        assert controller.dirty
        controller.undo()  # back to the saved state
        assert not controller.dirty


class TestSaveInPlace:
    def test_save_uses_the_current_path(self, loaded):
        controller, _, tmp_path = loaded
        assert controller.has_path
        assert controller.save() is True

    def test_save_without_a_document_is_a_noop(self):
        controller = AppController()
        assert controller.has_path is False
        assert controller.save() is False

    def test_merge_is_reported_when_saving_a_held_duplicate(self, loaded):
        controller, frontend, tmp_path = loaded
        controller.set_selection(Selection.single(2))
        controller.run_op("frames.duplicate")  # creates an identical held pair
        frontend.clear()
        controller.save_as(tmp_path / "held.gif")
        assert "merged" in frontend.last(ev.STATUS).payload["message"]


class TestBadTarget:
    def test_unknown_extension_reports_an_error(self, loaded):
        controller, frontend, tmp_path = loaded
        assert controller.save_as(tmp_path / "out.xyz") is False
        assert frontend.count(ev.ERROR) == 1
