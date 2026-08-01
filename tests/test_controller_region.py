"""The region and the clipboard: session state beside the frame Selection.

Driven through the fake frontend, so these also pin the event contract a second
frontend would rely on. The distinction under test throughout is that a region
is *not* document state -- it is not undoable, it is not saved, and it survives
edits that do not invalidate it -- while everything it produces is.
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


@pytest.fixture
def loaded(tmp_path: Path):
    controller = AppController()
    frontend = FakeFrontend().attach(controller)
    make_gif(tmp_path / "a.gif", frames=5, size=(40, 20))
    controller.open(tmp_path / "a.gif")
    frontend.clear()
    return controller, frontend


def painted(controller, color=(9, 9, 9, 255)):
    """Put a known solid document in place, so pixel assertions are readable.

    Substituting `_doc` behind the controller's back means history is still
    baselined on the *opened GIF*, so undo would walk back to those frames
    rather than to this one -- and every "undo put it back" check whose pixel
    happened to agree would pass while asserting nothing. `test_controller_float`
    hit exactly that and fixed it here; this had the same shape and got away
    with it, which is worse, because nothing was failing to prompt the fix.
    """
    frames = tuple(Frame.new(Image.new("RGBA", (20, 20), color), 100)
                   for _ in range(5))
    controller._doc = Document(frames, (20, 20))
    controller._history.reset(
        Snapshot(controller._doc, controller._selection, controller.index, "Open"))
    return controller


class TestTheHelperAbove:
    """A test on `painted`, because it is the thing every undo check here rests on.

    The trap it fell into is invisible by construction: substituting `_doc`
    without re-baselining history leaves undo walking back to the *opened GIF*,
    and since both documents are flat colour, any "undo put it back" assertion
    whose sample pixel happens to agree passes without touching the document it
    claims to be about. The two differ in *size*, which is the one property no
    pixel comparison can accidentally satisfy -- so that is what to assert on.
    """

    def test_undo_returns_the_substituted_document_not_the_opened_file(self, loaded):
        controller, _ = loaded
        painted(controller)
        controller.set_region(Region(2, 2, 6, 6))
        controller.copy_region()
        controller.run_op("paint.cut", index=0, x=2, y=2, width=6, height=6)
        controller.undo()
        assert controller.doc.size == (20, 20), (
            "history is baselined on the opened GIF, so every undo check in "
            "this file is asserting against the wrong document")


class TestSettingTheRegion:
    def test_it_reports_the_change_once(self, loaded):
        controller, frontend = loaded
        controller.set_region(Region(2, 2, 5, 5))
        assert controller.region == Region(2, 2, 5, 5)
        assert frontend.count(ev.REGION_CHANGED) == 1

    def test_setting_the_same_region_again_says_nothing(self, loaded):
        controller, frontend = loaded
        controller.set_region(Region(2, 2, 5, 5))
        frontend.clear()
        controller.set_region(Region(2, 2, 5, 5))
        assert frontend.count(ev.REGION_CHANGED) == 0

    def test_it_is_clamped_on_the_way_in(self, loaded):
        """A marquee dragged past the edge of a zoomed-out canvas is ordinary,
        not an error -- so the region can never name pixels that don't exist."""
        controller, _ = loaded
        controller.set_region(Region(30, 15, 100, 100))
        assert controller.region == Region(30, 15, 10, 5)

    def test_a_region_entirely_outside_the_canvas_is_no_region(self, loaded):
        controller, _ = loaded
        controller.set_region(Region(500, 500, 4, 4))
        assert controller.region is None

    def test_none_clears_it(self, loaded):
        controller, frontend = loaded
        controller.set_region(Region(1, 1, 4, 4))
        frontend.clear()
        controller.set_region(None)
        assert controller.region is None
        assert frontend.last(ev.REGION_CHANGED).payload["region"] is None

    def test_it_survives_scrubbing(self, loaded):
        """The whole reason it lives in the controller rather than in a
        gesture: a region outlives every frame you step past."""
        controller, _ = loaded
        controller.set_region(Region(1, 1, 4, 4))
        controller.seek(3)
        controller.step(1)
        assert controller.region == Region(1, 1, 4, 4)

    def test_it_survives_undo_and_redo(self, loaded):
        """Not document state. Undoing a paste gives back your pixels; it does
        not rearrange what you had selected."""
        controller, _ = loaded
        controller.set_region(Region(1, 1, 4, 4))
        controller.set_selection(Selection.single(1))
        controller.run_op("frames.duplicate")
        controller.undo()
        assert controller.region == Region(1, 1, 4, 4)


class TestRegionAgainstACanvasThatChanged:
    def test_a_crop_trims_it_rather_than_dropping_it(self, loaded):
        """After a crop the part you were working on is usually still there."""
        controller, _ = loaded
        controller.set_region(Region(5, 5, 20, 10))
        controller.run_op("canvas.crop", x=0, y=0, width=15, height=12)
        assert controller.region == Region(5, 5, 10, 7)

    def test_a_crop_that_excludes_it_entirely_clears_it(self, loaded):
        controller, frontend = loaded
        controller.set_region(Region(30, 10, 8, 8))
        frontend.clear()
        controller.run_op("canvas.crop", x=0, y=0, width=10, height=10)
        assert controller.region is None
        assert frontend.count(ev.REGION_CHANGED) == 1

    def test_the_region_change_is_announced_after_the_document(self, loaded):
        """Order matters: a listener told the region shrank while it still held
        the old document would draw the marquee against a canvas that is gone.
        """
        controller, frontend = loaded
        controller.set_region(Region(30, 10, 8, 8))
        frontend.clear()
        controller.run_op("canvas.crop", x=0, y=0, width=10, height=10)
        names = [n for n in frontend.names
                 if n in (ev.DOC_CHANGED, ev.REGION_CHANGED)]
        assert names == [ev.DOC_CHANGED, ev.REGION_CHANGED]

    def test_deleting_frames_leaves_it_alone(self, loaded):
        """A region names pixels, so a frame-count change is none of its
        business -- unlike the frame Selection, which is clamped."""
        controller, _ = loaded
        controller.set_region(Region(2, 2, 6, 6))
        controller.set_selection(Selection.single(4))
        controller.run_op("frames.delete")
        assert controller.region == Region(2, 2, 6, 6)

    def test_opening_a_file_clears_it(self, loaded, tmp_path: Path):
        controller, _ = loaded
        controller.set_region(Region(2, 2, 6, 6))
        make_gif(tmp_path / "b.gif", frames=3, size=(60, 60))
        controller.open(tmp_path / "b.gif")
        assert controller.region is None

    def test_closing_clears_it(self, loaded):
        controller, _ = loaded
        controller.set_region(Region(2, 2, 6, 6))
        controller.close()
        assert controller.region is None


class TestCopy:
    def test_it_takes_the_pixels_from_the_playhead_frame(self, loaded):
        controller, _ = loaded
        painted(controller)
        controller.set_region(Region(3, 4, 5, 6))
        assert controller.copy_region()
        assert controller.clipboard_size == (5, 6)

    def test_it_needs_a_region(self, loaded):
        controller, _ = loaded
        assert not controller.can_copy
        assert not controller.copy_region()
        assert not controller.can_paste

    def test_the_clipboard_is_detached_from_the_document(self, loaded):
        """It outlives the document it came from, so it cannot be a lazy view
        onto a frame someone is about to edit."""
        controller, _ = loaded
        painted(controller, (7, 7, 7, 255))
        controller.set_region(Region(0, 0, 4, 4))
        controller.copy_region()
        before = controller._clipboard.tobytes()
        controller.run_op("paint.fill", index=0, x=0, y=0, color=(1, 2, 3, 255))
        assert controller._clipboard.tobytes() == before

    def test_the_clipboard_survives_opening_another_file(self, loaded, tmp_path: Path):
        """Deliberate: copying a sprite out of one GIF and stamping it into
        another is a thing people do."""
        controller, _ = loaded
        painted(controller)
        controller.set_region(Region(0, 0, 4, 4))
        controller.copy_region()
        make_gif(tmp_path / "c.gif", frames=2, size=(30, 30))
        controller.open(tmp_path / "c.gif")
        assert controller.can_paste
        assert controller.region is None  # the region does not survive; see above


class TestCut:
    def test_it_copies_and_clears(self, loaded):
        controller, _ = loaded
        painted(controller)
        controller.set_region(Region(2, 2, 4, 4))
        assert controller.cut_region()
        assert controller.clipboard_size == (4, 4)
        image = controller.doc[controller.index].image
        assert image.getpixel((3, 3))[3] == 0
        assert image.getpixel((9, 9))[3] == 255

    def test_it_clears_only_the_frame_the_playhead_is_on(self, loaded):
        controller, _ = loaded
        painted(controller)
        controller.set_selection(Selection(frozenset({0, 1, 2, 3, 4})))
        controller.seek(2)
        controller.set_region(Region(2, 2, 4, 4))
        controller.cut_region()
        assert controller.doc[2].image.getpixel((3, 3))[3] == 0
        for i in (0, 1, 3, 4):
            assert controller.doc[i].image.getpixel((3, 3))[3] == 255

    def test_it_is_one_undoable_edit(self, loaded):
        controller, _ = loaded
        painted(controller)
        controller.set_region(Region(2, 2, 4, 4))
        controller.cut_region()
        assert controller.undo_label == "Cut"
        controller.undo()
        assert controller.doc[0].image.getpixel((3, 3))[3] == 255

    def test_undo_does_not_give_the_clipboard_back(self, loaded):
        """Session state, not document state. Undoing a cut restores the
        pixels; the clipboard is not part of the document's history."""
        controller, _ = loaded
        painted(controller)
        controller.set_region(Region(2, 2, 4, 4))
        controller.cut_region()
        controller.undo()
        assert controller.can_paste

    def test_cutting_empty_pixels_still_fills_the_clipboard(self, loaded):
        """The op declines and says so; cutting nothing copies nothing, which
        is an outcome rather than a failure."""
        controller, _ = loaded
        painted(controller, (0, 0, 0, 0))
        controller.set_region(Region(2, 2, 4, 4))
        assert controller.cut_region()
        assert controller.clipboard_size == (4, 4)
        assert not controller.dirty  # nothing was actually edited

    def test_it_needs_a_region(self, loaded):
        controller, _ = loaded
        assert not controller.cut_region()


class TestUndoLabels:
    """What the Edit menu says you are about to undo.

    Here rather than beside the op, because `op_label` being right is not the
    same claim as `run_op` *using* it — a mutation run caught exactly that gap:
    replacing the call with the static label broke nothing until this existed.
    """

    def test_an_erase_fill_says_so(self, loaded):
        controller, _ = loaded
        painted(controller)
        controller.run_op("paint.fill", index=0, x=1, y=1, mode="erase")
        assert controller.undo_label == "Erase Fill"

    def test_an_ordinary_fill_still_says_fill(self, loaded):
        controller, _ = loaded
        painted(controller)
        controller.run_op("paint.fill", index=0, x=1, y=1, color=(1, 2, 3, 255))
        assert controller.undo_label == "Fill"

    def test_an_erased_shape_says_so(self, loaded):
        controller, _ = loaded
        painted(controller)
        controller.run_op("paint.shape", index=0, kind="rect", x0=1, y0=1,
                          x1=5, y1=5, filled=True, mode="erase")
        assert controller.undo_label == "Erase Shape"

    def test_a_decline_is_reported_under_the_same_name(self, loaded):
        """The "nothing to do" message and the undo entry have to agree about
        what was attempted, or the one you get tells you nothing about the other.
        """
        controller, frontend = loaded
        painted(controller, (0, 0, 0, 0))
        frontend.clear()
        controller.run_op("paint.fill", index=0, x=1, y=1, mode="erase")
        assert frontend.last(ev.STATUS).payload["message"] == "Erase Fill: nothing to do"

    def test_ops_without_the_hook_are_unaffected(self, loaded):
        controller, _ = loaded
        controller.set_selection(Selection.single(1))
        controller.run_op("frames.duplicate")
        assert controller.undo_label == "Duplicate Frames"


class TestPaste:
    def setup_clipboard(self, controller, region=Region(2, 2, 4, 4)):
        painted(controller)
        controller.set_region(region)
        controller.copy_region()
        return controller

    def test_it_lands_where_it_was_copied_from(self, loaded):
        controller, _ = loaded
        self.setup_clipboard(controller)
        controller.run_op("paint.cut", index=0, x=2, y=2, width=4, height=4)
        assert controller.doc[0].image.getpixel((3, 3))[3] == 0
        controller.paste()
        assert controller.doc[0].image.getpixel((3, 3)) == (9, 9, 9, 255)

    def test_it_needs_a_clipboard(self, loaded):
        controller, _ = loaded
        assert not controller.can_paste
        assert not controller.paste()

    def test_it_does_not_need_a_region(self, loaded):
        """Copy needs one, paste does not -- the clipboard already knows both
        what it holds and where it goes."""
        controller, _ = loaded
        self.setup_clipboard(controller)
        controller.set_region(None)
        assert controller.paste()

    def test_it_stamps_every_selected_frame_you_are_standing_in(self, loaded):
        controller, _ = loaded
        self.setup_clipboard(controller)
        controller.run_op("paint.cut", index=0, x=2, y=2, width=4, height=4)
        # Blank the same square everywhere, so a paste is visible on each frame.
        for i in range(1, 5):
            controller.run_op("paint.cut", index=i, x=2, y=2, width=4, height=4)
        controller.set_selection(Selection(frozenset({0, 1, 2})))
        controller.seek(1)
        controller.paste()
        for i in (0, 1, 2):
            assert controller.doc[i].image.getpixel((3, 3))[3] == 255, i
        for i in (3, 4):
            assert controller.doc[i].image.getpixel((3, 3))[3] == 0, i

    def test_it_stamps_only_the_playhead_frame_when_you_have_stepped_away(self, loaded):
        """The `frame_targets` rule, which paste inherits from the delay box: a
        selection you have arrowed out of is not what you are working on."""
        controller, _ = loaded
        self.setup_clipboard(controller)
        for i in range(5):
            controller.run_op("paint.cut", index=i, x=2, y=2, width=4, height=4)
        controller.set_selection(Selection(frozenset({0, 1})))
        controller.seek(4)
        controller.paste()
        assert controller.doc[4].image.getpixel((3, 3))[3] == 255
        for i in (0, 1):
            assert controller.doc[i].image.getpixel((3, 3))[3] == 0

    def test_the_playhead_and_the_selection_are_left_where_they_were(self, loaded):
        """The reason OpResult.index exists: without it the playhead lands on
        the lowest selected frame and the selection collapses to one."""
        controller, _ = loaded
        self.setup_clipboard(controller)
        for i in range(5):
            controller.run_op("paint.cut", index=i, x=2, y=2, width=4, height=4)
        selection = Selection(frozenset({0, 1, 2, 3}))
        controller.set_selection(selection)
        controller.seek(2)
        controller.paste()
        assert controller.index == 2
        assert controller.selection.ordered == (0, 1, 2, 3)

    def test_a_second_paste_hits_the_same_frames(self, loaded):
        """Follows from the above, and is the thing that breaks first if the
        selection is allowed to collapse."""
        controller, _ = loaded
        self.setup_clipboard(controller)
        for i in range(5):
            controller.run_op("paint.cut", index=i, x=2, y=2, width=4, height=4)
        controller.set_selection(Selection(frozenset({0, 1, 2})))
        controller.seek(0)
        controller.paste()
        assert controller.frame_targets == (0, 1, 2)

    def test_it_is_one_undoable_edit_however_many_frames(self, loaded):
        controller, _ = loaded
        self.setup_clipboard(controller)
        for i in range(5):
            controller.run_op("paint.cut", index=i, x=2, y=2, width=4, height=4)
        controller.set_selection(Selection(frozenset({0, 1, 2, 3, 4})))
        controller.seek(0)
        controller.paste()
        assert controller.undo_label == "Paste"
        controller.undo()
        for i in range(5):
            assert controller.doc[i].image.getpixel((3, 3))[3] == 0


class TestCropToRegion:
    """The command that exists because Region and canvas.crop are the same four
    numbers (§26). Almost all of what is worth testing is the *region's* fate
    afterwards, not the pixels' -- `canvas.crop` is covered in test_canvas_ops.
    """

    def test_it_crops_the_canvas_to_the_marquee(self, loaded):
        controller, _ = loaded
        controller.set_region(Region(5, 4, 12, 9))
        assert controller.crop_to_region()
        assert controller.doc.size == (12, 9)

    def test_it_takes_the_right_pixels_not_just_the_right_size(self, loaded):
        """A crop that lands on the wrong origin still passes a size check, so
        the assertion has to be about content."""
        controller, _ = loaded
        painted(controller, (0, 0, 0, 255))
        mark = controller.doc[0].image.copy()
        mark.putpixel((7, 6), (200, 30, 40, 255))
        controller._doc = Document(
            (Frame.new(mark, 100),) + controller.doc.frames[1:], (20, 20))
        controller.set_region(Region(5, 4, 6, 6))
        controller.crop_to_region()
        assert controller.doc[0].image.getpixel((2, 2)) == (200, 30, 40, 255)

    def test_it_crops_every_frame(self, loaded):
        """Canvas ops are global by nature -- a document whose frames disagreed
        about their size is not a document."""
        controller, _ = loaded
        controller.set_region(Region(2, 2, 8, 8))
        controller.crop_to_region()
        assert {f.image.size for f in controller.doc} == {(8, 8)}

    def test_the_marquee_is_dropped_afterwards(self, loaded):
        """It named a rectangle that is now the whole canvas. Keeping it would
        leave a marquee re-clamped against an origin that just moved."""
        controller, _ = loaded
        controller.set_region(Region(5, 4, 12, 9))
        controller.crop_to_region()
        assert controller.region is None

    def test_it_declines_with_no_region(self, loaded):
        controller, _ = loaded
        size_before = controller.doc.size
        assert not controller.crop_to_region()
        assert controller.doc.size == size_before

    def test_a_full_canvas_marquee_changes_nothing_and_keeps_the_marquee(self, loaded):
        """The op declines an identity crop rather than stacking a no-op undo
        entry -- so this must not take the selection away as a consolation
        prize. Doing nothing means doing nothing."""
        controller, _ = loaded
        whole = Region(0, 0, *controller.doc.size)
        controller.set_region(whole)
        controller.crop_to_region()
        assert controller.region == whole
        assert not controller.can_undo

    def test_it_is_one_undoable_edit(self, loaded):
        controller, _ = loaded
        controller.set_region(Region(5, 4, 12, 9))
        controller.crop_to_region()
        assert controller.undo_label == "Crop"
        controller.undo()
        assert controller.doc.size == (40, 20)

    def test_undo_gives_back_the_pixels_but_not_the_marquee(self, loaded):
        """Session state is not on the undo stack, and a region that came back
        from the dead pointing at the pre-crop canvas would be worse than none.
        """
        controller, _ = loaded
        controller.set_region(Region(5, 4, 12, 9))
        controller.crop_to_region()
        controller.undo()
        assert controller.region is None

    def test_it_settles_a_float_first_like_every_other_edit(self, loaded):
        """`run_op` commits an outstanding float before it does anything, and
        this reaching the op through `run_op` is what buys that for free."""
        controller, _ = loaded
        painted(controller)
        controller.set_region(Region(2, 2, 6, 6))
        assert controller.begin_move()
        assert controller.nudge_float(3, 0)
        assert controller.floating is not None  # or the check below is vacuous
        controller.set_region(Region(1, 1, 10, 10))
        controller.crop_to_region()
        assert controller.floating is None
        assert controller.doc.size == (10, 10)
