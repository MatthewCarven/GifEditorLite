"""Canvas operations: resize, rotate, flip.

These allocate new pixels, so beyond correctness the tests check that output
frames get fresh uids (stale cache guard) while source frames are untouched
(covered more exhaustively in test_immutability.py).
"""

from __future__ import annotations

from PIL import Image

from giflite.core.model import Document, Frame, Selection
from giflite.core.ops import get_op, op_defaults


def doc(n=3, size=(8, 8)) -> Document:
    frames = tuple(Frame.new(Image.new("RGBA", size, (0, 0, 0, 255)), 100) for _ in range(n))
    return Document(frames, size)


def corner_doc(size=(4, 2)) -> Document:
    """One frame with a red pixel at the top-left, for orientation checks."""
    im = Image.new("RGBA", size, (0, 0, 0, 255))
    im.putpixel((0, 0), (255, 0, 0, 255))
    return Document((Frame.new(im, 100),), size)


def red_at(frame) -> tuple[int, int]:
    im = frame.image
    for y in range(im.height):
        for x in range(im.width):
            if im.getpixel((x, y))[:3] == (255, 0, 0):
                return (x, y)
    raise AssertionError("red pixel vanished")


def run(op_id, d, sel=Selection.empty(), **params):
    return get_op(op_id).apply(d, sel, **params)


class TestResize:
    def test_changes_canvas_and_every_frame(self):
        r = run("canvas.resize", doc(3, (8, 8)), width=16, height=4, keep_aspect=False)
        assert r.doc.size == (16, 4)
        assert all(f.image.size == (16, 4) for f in r.doc.frames)
        assert len(r.doc) == 3

    def test_keep_aspect_derives_height_from_width(self):
        r = run("canvas.resize", doc(1, (40, 20)), width=80, keep_aspect=True)
        assert r.doc.size == (80, 40)  # 2:1 preserved

    def test_output_frames_get_fresh_uids(self):
        d = doc(2, (8, 8))
        r = run("canvas.resize", d, width=4, height=4, keep_aspect=False)
        assert r.doc.frames[0].image_uid != d.frames[0].image_uid

    def test_default_params_seed_from_current_size(self):
        d = doc(1, (123, 45))
        defaults = op_defaults(get_op("canvas.resize"), d, Selection.empty())
        assert defaults["width"] == 123 and defaults["height"] == 45


class TestRotate:
    def test_clockwise_swaps_dimensions(self):
        r = run("canvas.rotate", doc(2, (8, 4)), angle="cw")
        assert r.doc.size == (4, 8)
        assert all(f.image.size == (4, 8) for f in r.doc.frames)

    def test_clockwise_moves_top_left_to_top_right(self):
        # 4x2, red at (0,0). 90 CW -> 2x4, red at top-right (1,0).
        r = run("canvas.rotate", corner_doc((4, 2)), angle="cw")
        assert r.doc.size == (2, 4)
        assert red_at(r.doc.frames[0]) == (1, 0)

    def test_counter_clockwise_moves_top_left_to_bottom_left(self):
        r = run("canvas.rotate", corner_doc((4, 2)), angle="ccw")
        assert red_at(r.doc.frames[0]) == (0, 3)

    def test_180_keeps_dimensions_and_moves_to_opposite_corner(self):
        r = run("canvas.rotate", corner_doc((4, 2)), angle="180")
        assert r.doc.size == (4, 2)
        assert red_at(r.doc.frames[0]) == (3, 1)


class TestFlip:
    def test_horizontal_mirrors_left_right(self):
        r = run("canvas.flip", corner_doc((4, 2)), direction="horizontal")
        assert r.doc.size == (4, 2)  # size unchanged
        assert red_at(r.doc.frames[0]) == (3, 0)  # top-left -> top-right

    def test_vertical_mirrors_top_bottom(self):
        r = run("canvas.flip", corner_doc((4, 2)), direction="vertical")
        assert red_at(r.doc.frames[0]) == (0, 1)  # top-left -> bottom-left


class TestGlobalNature:
    def test_canvas_ops_ignore_selection_and_hit_every_frame(self):
        d = doc(4, (8, 8))
        r = run("canvas.flip", d, sel=Selection(frozenset({0})), direction="horizontal")
        # all four frames flipped, not just the selected one
        assert all(f.image_uid != d.frames[i].image_uid for i, f in enumerate(r.doc.frames))

    def test_result_validates(self):
        run("canvas.resize", doc(3, (8, 8)), width=5, height=9,
            keep_aspect=False).doc.validate()
        run("canvas.rotate", doc(3, (8, 4)), angle="cw").doc.validate()
