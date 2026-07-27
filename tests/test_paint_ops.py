"""Painting operations: paint.stroke and paint.erase.

Tool-driven ops (ARCHITECTURE.md 19): pure, mask-based, editing one frame by
`index`. Beyond correctness these check that only the target frame changes (a
fresh uid), and that a stroke which paints nothing declines rather than pushing
a no-op onto undo. Source-pixel immutability is covered in test_immutability.py.
"""

from __future__ import annotations

from PIL import Image

from giflite.core.model import Document, Frame, Selection
from giflite.core.ops import get_op


def doc(n=3, size=(8, 8), color=(255, 255, 255, 255)) -> Document:
    frames = tuple(Frame.new(Image.new("RGBA", size, color), 100) for _ in range(n))
    return Document(frames, size)


def run(op_id, d, sel=Selection.empty(), **params):
    return get_op(op_id).apply(d, sel, **params)


class TestPaint:
    def test_single_pixel_paints_the_target(self):
        d = doc(3, (8, 8))
        r = run("paint.stroke", d, index=0, points=((4, 4),), size=1, color=(255, 0, 0, 255))
        assert r.doc.frames[0].image.getpixel((4, 4)) == (255, 0, 0, 255)
        assert r.doc.frames[0].image.getpixel((5, 4)) == (255, 255, 255, 255)  # neighbour intact

    def test_only_the_indexed_frame_changes(self):
        d = doc(3, (8, 8))
        r = run("paint.stroke", d, index=2, points=((1, 1),), size=1, color=(0, 0, 0, 255))
        assert r.doc.frames[2].image_uid != d.frames[2].image_uid   # target got fresh pixels
        assert r.doc.frames[0].image_uid == d.frames[0].image_uid   # others shared unchanged
        assert r.doc.frames[1].image_uid == d.frames[1].image_uid

    def test_line_paints_every_point(self):
        d = doc(1, (8, 8))
        r = run("paint.stroke", d, index=0, points=((1, 1), (1, 5)), size=1, color=(0, 255, 0, 255))
        img = r.doc.frames[0].image
        assert img.getpixel((1, 1)) == (0, 255, 0, 255)
        assert img.getpixel((1, 3)) == (0, 255, 0, 255)  # a point along the segment
        assert img.getpixel((1, 5)) == (0, 255, 0, 255)

    def test_bigger_brush_covers_a_disc(self):
        d = doc(1, (16, 16))
        r = run("paint.stroke", d, index=0, points=((8, 8),), size=5, color=(0, 0, 255, 255))
        img = r.doc.frames[0].image
        assert img.getpixel((8, 8)) == (0, 0, 255, 255)
        assert img.getpixel((8, 9)) == (0, 0, 255, 255)     # within the disc
        assert img.getpixel((0, 0)) == (255, 255, 255, 255)  # far corner untouched

    def test_rgb_color_is_treated_as_opaque(self):
        d = doc(1, (8, 8))
        r = run("paint.stroke", d, index=0, points=((2, 2),), size=1, color=(10, 20, 30))
        assert r.doc.frames[0].image.getpixel((2, 2)) == (10, 20, 30, 255)

    def test_selection_follows_to_the_painted_frame(self):
        # run_op moves the playhead to result.selection.first, so a stroke must
        # select the frame it painted or the playhead jumps off it.
        d = doc(4, (8, 8))
        r = run("paint.stroke", d, sel=Selection(frozenset({0})), index=2,
                points=((1, 1),), size=1, color=(9, 9, 9, 255))
        assert r.selection.ordered == (2,)

    def test_result_validates(self):
        run("paint.stroke", doc(2, (8, 8)), index=0, points=((3, 3),), size=2,
            color=(1, 2, 3, 255)).doc.validate()


class TestErase:
    def test_erase_clears_alpha_on_the_target_pixel(self):
        d = doc(1, (8, 8), color=(255, 255, 255, 255))
        r = run("paint.erase", d, index=0, points=((4, 4),), size=1)
        assert r.doc.frames[0].image.getpixel((4, 4))[3] == 0    # fully transparent
        assert r.doc.frames[0].image.getpixel((5, 4))[3] == 255  # neighbour intact

    def test_erasing_already_transparent_declines(self):
        d = doc(1, (8, 8), color=(0, 0, 0, 0))
        assert run("paint.erase", d, index=0, points=((4, 4),), size=3).doc is d


class TestDeclines:
    def test_empty_points_declines(self):
        d = doc(2, (8, 8))
        assert run("paint.stroke", d, index=0, points=(), size=3, color=(1, 2, 3, 255)).doc is d

    def test_out_of_range_index_declines(self):
        d = doc(2, (8, 8))
        assert run("paint.stroke", d, index=9, points=((1, 1),), size=1, color=(1, 2, 3, 255)).doc is d

    def test_entirely_off_canvas_declines(self):
        d = doc(1, (8, 8))
        assert run("paint.stroke", d, index=0, points=((50, 50),), size=2, color=(1, 2, 3, 255)).doc is d
