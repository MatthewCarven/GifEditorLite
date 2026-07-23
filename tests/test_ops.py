"""Frame operation tests.

Each op is a pure function, so these construct a small Document of solid-colour
frames, apply, and assert the frame order and the returned selection. Colour is
the identity marker -- frame i is RGBA (i, 0, 0, 255) -- so reorderings are easy
to read.
"""

from __future__ import annotations

import pytest
from PIL import Image

from giflite.core.model import Document, Frame, Selection
from giflite.core.ops import get_op


def doc(n: int = 5, size=(8, 8)) -> Document:
    frames = tuple(
        Frame.new(Image.new("RGBA", size, (i, 0, 0, 255)), 100) for i in range(n)
    )
    return Document(frames=frames, size=size)


def colours(d: Document) -> list[int]:
    """The red channel of each frame -- a readable fingerprint of order."""
    return [f.image.getpixel((0, 0))[0] for f in d.frames]


def run(op_id, d, sel, **params):
    return get_op(op_id).apply(d, sel, **params)


class TestDelete:
    def test_removes_selected_frames(self):
        r = run("frames.delete", doc(5), Selection(frozenset({1, 3})))
        assert colours(r.doc) == [0, 2, 4]

    def test_selects_what_slid_into_the_first_gap(self):
        r = run("frames.delete", doc(5), Selection(frozenset({1, 2})))
        # frame 3 (colour 3) is now at index 1
        assert r.selection.ordered == (1,)
        assert r.doc.frames[1].image.getpixel((0, 0))[0] == 3

    def test_deleting_the_last_frame_clamps_selection(self):
        r = run("frames.delete", doc(3), Selection(frozenset({2})))
        assert colours(r.doc) == [0, 1]
        assert r.selection.ordered == (1,)

    def test_refuses_to_empty_the_document(self):
        d = doc(3)
        r = run("frames.delete", d, Selection(frozenset({0, 1, 2})))
        assert r.doc is d  # unchanged; controller surfaces the message


class TestDuplicate:
    def test_duplicates_in_place(self):
        r = run("frames.duplicate", doc(3), Selection(frozenset({1})))
        assert colours(r.doc) == [0, 1, 1, 2]

    def test_selects_the_new_copies(self):
        r = run("frames.duplicate", doc(3), Selection(frozenset({1})))
        assert r.selection.ordered == (2,)

    def test_multiple_copies(self):
        r = run("frames.duplicate", doc(2), Selection(frozenset({0})), copies=3)
        assert colours(r.doc) == [0, 0, 0, 0, 1]
        assert r.selection.ordered == (1, 2, 3)

    def test_duplicate_shares_pixels_with_the_original(self):
        d = doc(2)
        r = run("frames.duplicate", d, Selection(frozenset({0})))
        assert r.doc.frames[0].image_uid == r.doc.frames[1].image_uid
        assert r.doc.frames[1].image is d.frames[0].image


class TestMove:
    def test_moves_a_single_frame_forward(self):
        # move frame 0 to before original index 3 -> 1,2,0,3,4
        r = run("frames.move", doc(5), Selection(frozenset({0})), to=3)
        assert colours(r.doc) == [1, 2, 0, 3, 4]

    def test_moves_a_block_to_the_front(self):
        r = run("frames.move", doc(5), Selection(frozenset({2, 3})), to=0)
        assert colours(r.doc) == [2, 3, 0, 1, 4]

    def test_moves_to_the_end(self):
        r = run("frames.move", doc(4), Selection(frozenset({0})), to=4)
        assert colours(r.doc) == [1, 2, 3, 0]

    def test_selection_follows_the_moved_block(self):
        r = run("frames.move", doc(5), Selection(frozenset({0, 1})), to=5)
        assert colours(r.doc) == [2, 3, 4, 0, 1]
        assert r.selection.ordered == (3, 4)


class TestReverse:
    def test_no_selection_reverses_everything(self):
        r = run("frames.reverse", doc(4), Selection.empty())
        assert colours(r.doc) == [3, 2, 1, 0]

    def test_reverses_only_the_selected_frames_in_place(self):
        # reverse frames at positions 1 and 3: their contents swap, positions hold
        r = run("frames.reverse", doc(5), Selection(frozenset({1, 3})))
        assert colours(r.doc) == [0, 3, 2, 1, 4]

    def test_selection_is_preserved_for_in_place_reverse(self):
        r = run("frames.reverse", doc(5), Selection(frozenset({1, 3})))
        assert r.selection.ordered == (1, 3)


class TestTrim:
    def test_keeps_only_the_selected_frames(self):
        r = run("frames.trim", doc(5), Selection(frozenset({1, 2, 4})))
        assert colours(r.doc) == [1, 2, 4]

    def test_selects_all_of_the_result(self):
        r = run("frames.trim", doc(5), Selection(frozenset({1, 2, 4})))
        assert r.selection.ordered == (0, 1, 2)


class TestRegistryMetadata:
    def test_all_five_frame_ops_are_registered(self):
        from giflite.core.ops import all_ops

        ids = {op.id for op in all_ops()}
        # subset, not equality: other groups (timing, canvas) register too
        assert {
            "frames.delete",
            "frames.duplicate",
            "frames.move",
            "frames.reverse",
            "frames.trim",
        } <= ids

    def test_move_is_not_a_menu_command(self):
        from giflite.core.ops import menu_groups

        menu_ids = {op.id for op in menu_groups()["frames"]}
        assert "frames.move" not in menu_ids
        assert "frames.delete" in menu_ids

    @pytest.mark.parametrize("op_id", ["frames.delete", "frames.duplicate", "frames.trim"])
    def test_editing_ops_need_a_selection(self, op_id):
        assert get_op(op_id).needs_selection is True

    def test_reverse_does_not_need_a_selection(self):
        assert get_op("frames.reverse").needs_selection is False
