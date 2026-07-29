"""The floating edit: pixels shown but not committed.

The third state (ARCHITECTURE.md 28). Everything here is about the things that
follow from it and would otherwise have to be rediscovered one bug at a time:
the preview is the op run and thrown away, cancelling is free because the
document was never touched, committing is *one* undo entry, and anything else
you do settles the float rather than stranding it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from giflite.app import events as ev
from giflite.app.controller import AppController
from giflite.core.history import Snapshot
from giflite.core.model import Document, Frame, Region, Selection
from tests.conftest import make_gif
from tests.fake_frontend import FakeFrontend

SIZE = (20, 20)


@pytest.fixture
def loaded(tmp_path: Path):
    """A controller with a real history baseline and per-frame-distinct pixels.

    Each frame is a different colour so "did every frame keep its *own* pixels"
    is answerable, which is the whole difference between move and paste.
    """
    controller = AppController()
    frontend = FakeFrontend().attach(controller)
    make_gif(tmp_path / "a.gif", frames=4, size=SIZE)
    controller.open(tmp_path / "a.gif")
    controller._doc = Document(
        tuple(Frame.new(Image.new("RGBA", SIZE, (10 * i, 40, 60, 255)), 100)
              for i in range(4)),
        SIZE,
    )
    # Re-baseline history against the substituted document. Without this, undo
    # walks back to the *opened GIF's* frames -- which silently passes any
    # "undo put it back" check whose pixel happens to agree, and is exactly the
    # kind of test that looks green while asserting nothing.
    controller._history.reset(
        Snapshot(controller._doc, controller._selection, 0, "Open"))
    frontend.clear()
    return controller, frontend


def region(controller, x=2, y=2, w=4, h=4):
    controller.set_region(Region(x, y, w, h))
    return controller


class TestStartingAFloat:
    def test_a_move_needs_a_region(self, loaded):
        controller, _ = loaded
        assert not controller.begin_move()
        assert controller.floating is None

    def test_beginning_a_move_announces_it(self, loaded):
        controller, frontend = loaded
        region(controller)
        frontend.clear()
        assert controller.begin_move()
        assert controller.floating.kind == "move"
        assert frontend.count(ev.FLOAT_CHANGED) == 1

    def test_a_paste_needs_a_clipboard(self, loaded):
        controller, _ = loaded
        assert not controller.begin_paste()

    def test_a_paste_floats_where_it_was_copied_from(self, loaded):
        controller, _ = loaded
        region(controller, 3, 4, 5, 6)
        controller.copy_region()
        assert controller.begin_paste()
        assert controller.floating.kind == "paste"
        assert controller.floating.region == Region(3, 4, 5, 6)
        assert controller.float_offset == (0, 0)

    def test_a_paste_does_not_need_a_region(self, loaded):
        """The clipboard already knows what it holds and where it came from."""
        controller, _ = loaded
        region(controller)
        controller.copy_region()
        controller.set_region(None)
        assert controller.begin_paste()


class TestTheDocumentIsUntouched:
    def test_nothing_changes_while_it_floats(self, loaded):
        controller, _ = loaded
        region(controller)
        before = controller.doc
        controller.begin_move()
        controller.move_float(6, 6)
        assert controller.doc is before
        assert not controller.dirty

    def test_cancelling_is_free(self, loaded):
        controller, _ = loaded
        region(controller)
        before = controller.doc
        controller.begin_move()
        controller.move_float(6, 6)
        assert controller.cancel_float()
        assert controller.doc is before
        assert controller.floating is None
        assert not controller.can_undo or controller.undo_label != "Move"

    def test_cancelling_nothing_reports_false(self, loaded):
        controller, _ = loaded
        assert not controller.cancel_float()


class TestThePreview:
    def test_it_shows_the_move_that_has_not_happened(self, loaded):
        controller, _ = loaded
        region(controller)
        controller.begin_move()
        controller.move_float(5, 0)
        preview = controller.float_preview()
        assert preview.getpixel((7, 3))[3] == 255   # landed here
        assert preview.getpixel((2, 2))[3] == 0     # left a hole there
        assert controller.doc[0].image.getpixel((2, 2))[3] == 255  # really, though

    def test_it_matches_what_committing_produces(self, loaded):
        """Not merely consistent with the commit -- the same call. A move that
        lands wrong will look wrong first."""
        controller, _ = loaded
        region(controller)
        controller.begin_move()
        controller.move_float(4, 3)
        preview = controller.float_preview().tobytes()
        controller.commit_float()
        assert controller.doc[0].image.tobytes() == preview

    def test_with_no_float_it_is_just_the_frame(self, loaded):
        controller, _ = loaded
        assert controller.float_preview().tobytes() == controller.doc[0].image.tobytes()

    def test_it_previews_the_frame_you_ask_for(self, loaded):
        controller, _ = loaded
        region(controller)
        controller.begin_move()
        controller.move_float(5, 0)
        assert controller.float_preview(2).getpixel((7, 3))[:3] == (20, 40, 60)


class TestCommitting:
    def test_a_move_is_one_undoable_edit(self, loaded):
        controller, _ = loaded
        region(controller)
        controller.begin_move()
        controller.move_float(5, 2)
        assert controller.commit_float()
        assert controller.undo_label == "Move"
        before = controller.doc[0].image.getpixel((2, 2))
        controller.undo()
        assert controller.doc[0].image.getpixel((2, 2))[3] == 255
        assert before[3] == 0, "the commit really had cleared the source"

    def test_one_undo_is_enough(self, loaded):
        """The reason paint.move is one op rather than a cut and a paste: two
        would hand back the hole while you were still holding the sprite."""
        controller, _ = loaded
        pixels = controller.doc[0].image.tobytes()
        region(controller)
        controller.begin_move()
        controller.move_float(5, 2)
        controller.commit_float()
        controller.undo()
        assert controller.doc[0].image.tobytes() == pixels

    def test_a_move_put_straight_back_is_not_an_edit(self, loaded):
        controller, _ = loaded
        region(controller)
        before = controller.doc
        controller.begin_move()
        assert not controller.commit_float()
        assert controller.doc is before
        assert controller.floating is None

    def test_a_paste_at_zero_offset_still_lands(self, loaded):
        """Unlike a move: Ctrl+V then Enter is a paste in place, and pasting
        something onto a hole is exactly what it is for."""
        controller, _ = loaded
        region(controller)
        controller.copy_region()
        controller.cut_region()
        controller.begin_paste()
        assert controller.commit_float()
        assert controller.undo_label == "Paste"
        assert controller.doc[0].image.getpixel((3, 3))[3] == 255

    def test_a_paste_lands_where_it_was_dragged(self, loaded):
        """The headline bug this feature could have: drag the pasted sprite
        somewhere, and have it appear back at the origin anyway."""
        controller, _ = loaded
        region(controller, 2, 2, 4, 4)
        controller.copy_region()
        controller.cut_region()          # leave a hole so the landing is visible
        controller.begin_paste()
        controller.move_float(9, 5)
        controller.commit_float()
        image = controller.doc[0].image
        assert image.getpixel((12, 8))[3] == 255, "it did not land at the offset"
        assert image.getpixel((3, 3))[3] == 0, "it landed back at the origin too"

    def test_committing_nothing_reports_false(self, loaded):
        controller, _ = loaded
        assert not controller.commit_float()

    def test_the_float_is_gone_afterwards(self, loaded):
        controller, frontend = loaded
        region(controller)
        controller.begin_move()
        controller.move_float(3, 3)
        frontend.clear()
        controller.commit_float()
        assert controller.floating is None
        assert frontend.last(ev.FLOAT_CHANGED).payload["floating"] is None


class TestScope:
    def test_a_move_shifts_each_frame_s_own_pixels(self, loaded):
        """The whole difference from paste, which stamps one image everywhere."""
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({0, 1, 2})))
        controller.seek(1)
        region(controller)
        controller.begin_move()
        controller.move_float(6, 0)
        controller.commit_float()
        assert [controller.doc[i].image.getpixel((8, 3))[0] for i in range(4)] \
            == [0, 10, 20, 30]

    def test_it_leaves_frames_outside_the_selection_alone(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({0, 1})))
        controller.seek(0)
        region(controller)
        before = controller.doc[3]
        controller.begin_move()
        controller.move_float(6, 0)
        controller.commit_float()
        assert controller.doc[3] is before

    def test_the_playhead_and_selection_survive(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection(frozenset({0, 1, 2})))
        controller.seek(2)
        region(controller)
        controller.begin_move()
        controller.move_float(3, 3)
        controller.commit_float()
        assert controller.index == 2
        assert controller.selection.ordered == (0, 1, 2)


class TestSettling:
    """Anything else you do commits the float rather than stranding it.

    Committing rather than discarding because an unwanted commit is one Ctrl+Z
    away, while work discarded on your behalf is simply gone.
    """

    def floated(self, controller):
        region(controller)
        controller.begin_move()
        controller.move_float(5, 1)
        return controller

    def test_scrubbing_settles_it(self, loaded):
        controller, _ = loaded
        self.floated(controller)
        controller.seek(2)
        assert controller.floating is None
        assert controller.undo_label == "Move"

    def test_another_edit_settles_it(self, loaded):
        controller, _ = loaded
        self.floated(controller)
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        assert controller.floating is None
        assert controller.undo_label == "Duplicate Frames"
        controller.undo()
        assert controller.undo_label == "Move", "the move went on the stack first"

    def test_undo_settles_it_first(self, loaded):
        """Otherwise Ctrl+Z would undo the edit *before* the move you are still
        holding, and the float would then be sitting on a document that no
        longer matches where it came from."""
        controller, _ = loaded
        self.floated(controller)
        controller.undo()
        assert controller.floating is None

    def test_saving_settles_it(self, loaded, tmp_path: Path):
        controller, _ = loaded
        self.floated(controller)
        controller.save_as(tmp_path / "out.gif")
        assert controller.floating is None
        assert not controller.dirty, "the move was saved, not left behind"

    def test_opening_settles_it(self, loaded, tmp_path: Path):
        controller, _ = loaded
        self.floated(controller)
        make_gif(tmp_path / "b.gif", frames=2, size=(30, 30))
        controller.open(tmp_path / "b.gif")
        assert controller.floating is None

    def test_closing_settles_it(self, loaded):
        controller, _ = loaded
        self.floated(controller)
        controller.close()
        assert controller.floating is None

    def test_settling_does_not_recurse(self, loaded):
        """commit_float calls run_op, which settles the float. Without the
        guard that is unbounded recursion, and the float would be committed
        twice into the bargain."""
        controller, _ = loaded
        self.floated(controller)
        controller.commit_float()
        assert controller.floating is None
        controller.undo()
        assert controller.undo_label != "Move", "it was committed twice"

    def test_beginning_another_float_settles_the_first(self, loaded):
        controller, _ = loaded
        self.floated(controller)
        controller.copy_region()
        controller.begin_paste()
        assert controller.floating.kind == "paste"
        assert controller.undo_label == "Move"


class TestPlacing:
    def test_move_float_is_absolute(self, loaded):
        controller, _ = loaded
        region(controller)
        controller.begin_move()
        controller.move_float(3, 3)
        controller.move_float(5, 1)
        assert controller.float_offset == (5, 1)

    def test_nudge_is_relative(self, loaded):
        controller, _ = loaded
        region(controller)
        controller.begin_move()
        controller.move_float(3, 3)
        controller.nudge_float(1, -1)
        assert controller.float_offset == (4, 2)

    def test_placing_it_where_it_already_is_says_nothing(self, loaded):
        controller, frontend = loaded
        region(controller)
        controller.begin_move()
        controller.move_float(2, 2)
        frontend.clear()
        assert not controller.move_float(2, 2)
        assert frontend.count(ev.FLOAT_CHANGED) == 0

    def test_placing_with_nothing_floating_reports_false(self, loaded):
        controller, _ = loaded
        assert not controller.move_float(1, 1)
        assert not controller.nudge_float(1, 1)
