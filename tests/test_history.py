"""History tests: the undo stack and the saved-marker dirty logic."""

from __future__ import annotations

from PIL import Image

from giflite.core.history import History, Snapshot
from giflite.core.model import Document, Frame, Selection


def snap(label: str, n: int = 3) -> Snapshot:
    frames = tuple(Frame.new(Image.new("RGBA", (4, 4)), 100) for _ in range(n))
    return Snapshot(Document(frames, (4, 4)), Selection.single(0), 0, label)


class TestUndoRedo:
    def test_fresh_history_cannot_undo_or_redo(self):
        h = History()
        h.reset(snap("Open"))
        assert not h.can_undo
        assert not h.can_redo

    def test_push_enables_undo(self):
        h = History()
        h.reset(snap("Open"))
        h.push(snap("Delete Frames"))
        assert h.can_undo
        assert not h.can_redo

    def test_undo_returns_the_previous_state(self):
        h = History()
        h.reset(snap("Open", n=3))
        h.push(snap("Delete Frames", n=2))
        restored = h.undo()
        assert len(restored.doc) == 3
        assert h.can_redo

    def test_redo_returns_the_next_state(self):
        h = History()
        h.reset(snap("Open", n=3))
        h.push(snap("Delete Frames", n=2))
        h.undo()
        restored = h.redo()
        assert len(restored.doc) == 2

    def test_pushing_after_undo_discards_the_redo_branch(self):
        h = History()
        h.reset(snap("Open"))
        h.push(snap("A"))
        h.push(snap("B"))
        h.undo()  # back to A
        h.push(snap("C"))  # diverge
        assert not h.can_redo  # B is gone
        assert h.undo_label == "C"


class TestLabels:
    def test_undo_label_names_the_current_op(self):
        h = History()
        h.reset(snap("Open"))
        h.push(snap("Delete Frames"))
        assert h.undo_label == "Delete Frames"

    def test_redo_label_names_the_next_op(self):
        h = History()
        h.reset(snap("Open"))
        h.push(snap("Delete Frames"))
        h.undo()
        assert h.redo_label == "Delete Frames"
        assert h.undo_label is None


class TestDirty:
    def test_freshly_opened_is_clean(self):
        h = History()
        h.reset(snap("Open"))
        assert not h.dirty

    def test_an_edit_makes_it_dirty(self):
        h = History()
        h.reset(snap("Open"))
        h.push(snap("Delete Frames"))
        assert h.dirty

    def test_undoing_back_to_the_saved_state_clears_dirty(self):
        h = History()
        h.reset(snap("Open"))
        h.push(snap("Delete Frames"))
        assert h.dirty
        h.undo()
        assert not h.dirty  # a plain boolean flag could never do this

    def test_saving_marks_the_current_state_clean(self):
        h = History()
        h.reset(snap("Open"))
        h.push(snap("A"))
        h.mark_saved()
        assert not h.dirty
        h.push(snap("B"))
        assert h.dirty
        h.undo()
        assert not h.dirty  # back at the saved 'A'


class TestLimit:
    def test_history_is_bounded(self):
        h = History(limit=4)
        h.reset(snap("Open"))
        for i in range(10):
            h.push(snap(f"op{i}"))
        # can still undo, but not infinitely
        depth = 0
        while h.undo() is not None:
            depth += 1
        assert depth == 3  # limit 4 states -> 3 undos

    def test_dirty_stays_true_when_the_saved_state_falls_off(self):
        h = History(limit=3)
        h.reset(snap("Open"))  # saved here
        for i in range(5):
            h.push(snap(f"op{i}"))
        # the saved 'Open' has been trimmed away; we can never get back to clean
        assert h.dirty
        while h.undo() is not None:
            pass
        assert h.dirty
