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


def striped(width=20, height=10) -> Document:
    """A blue field with two separate red squares. The gap between them is the
    point: a contiguous fill must reach one and not the other."""
    im = Image.new("RGBA", (width, height), (0, 0, 255, 255))
    for left in (2, 12):
        for x in range(left, left + 4):
            for y in range(2, 6):
                im.putpixel((x, y), (255, 0, 0, 255))
    return Document((Frame.new(im, 100),), (width, height))


class TestFill:
    def test_it_fills_the_region_under_the_seed(self):
        d = striped()
        r = run("paint.fill", d, index=0, x=3, y=3, color=(0, 255, 0, 255))
        img = r.doc.frames[0].image
        assert img.getpixel((3, 3)) == (0, 255, 0, 255)
        assert img.getpixel((5, 5)) == (0, 255, 0, 255)   # far corner of the same square

    def test_it_stops_at_the_region_boundary(self):
        d = striped()
        r = run("paint.fill", d, index=0, x=3, y=3, color=(0, 255, 0, 255))
        img = r.doc.frames[0].image
        assert img.getpixel((1, 3)) == (0, 0, 255, 255)   # the field just outside
        assert img.getpixel((6, 3)) == (0, 0, 255, 255)

    def test_a_matching_but_unreachable_region_is_left_alone(self):
        """The difference between a fill and a global replace, and the reason
        `_fill_mask` has a connectivity stage at all."""
        d = striped()
        r = run("paint.fill", d, index=0, x=3, y=3, color=(0, 255, 0, 255))
        assert r.doc.frames[0].image.getpixel((13, 3)) == (255, 0, 0, 255)

    def test_filling_the_field_flows_around_the_islands(self):
        d = striped()
        r = run("paint.fill", d, index=0, x=0, y=0, color=(0, 255, 0, 255))
        img = r.doc.frames[0].image
        assert img.getpixel((19, 9)) == (0, 255, 0, 255)  # reached the far corner
        assert img.getpixel((3, 3)) == (255, 0, 0, 255)   # both islands survive
        assert img.getpixel((13, 3)) == (255, 0, 0, 255)

    def test_tolerance_zero_ignores_a_one_step_difference(self):
        im = Image.new("RGBA", (4, 1), (100, 100, 100, 255))
        im.putpixel((1, 0), (108, 100, 100, 255))
        d = Document((Frame.new(im, 100),), (4, 1))
        r = run("paint.fill", d, index=0, x=0, y=0, color=(0, 0, 0, 255), tolerance=0)
        img = r.doc.frames[0].image
        assert img.getpixel((0, 0)) == (0, 0, 0, 255)
        assert img.getpixel((1, 0)) == (108, 100, 100, 255)  # blocked
        assert img.getpixel((3, 0)) == (100, 100, 100, 255)  # and so unreachable

    def test_tolerance_lets_the_fill_through_a_near_match(self):
        im = Image.new("RGBA", (4, 1), (100, 100, 100, 255))
        im.putpixel((1, 0), (108, 100, 100, 255))
        d = Document((Frame.new(im, 100),), (4, 1))
        r = run("paint.fill", d, index=0, x=0, y=0, color=(0, 0, 0, 255), tolerance=8)
        img = r.doc.frames[0].image
        assert img.getpixel((1, 0)) == (0, 0, 0, 255)
        assert img.getpixel((3, 0)) == (0, 0, 0, 255)   # the barrier is gone, so it flows on

    def test_tolerance_is_per_channel_not_summed(self):
        """Chebyshev, not Manhattan or Euclidean: 'tolerance 8' means no single
        channel differs by more than 8, which is a sentence a user can hold in
        their head. A summed metric would let (8, 8, 8) through at tolerance 8
        while this deliberately does not treat it as further away than (8, 0, 0)
        -- both are exactly 8 by the largest channel."""
        im = Image.new("RGBA", (3, 1), (100, 100, 100, 255))
        im.putpixel((1, 0), (108, 108, 108, 255))
        d = Document((Frame.new(im, 100),), (3, 1))
        r = run("paint.fill", d, index=0, x=0, y=0, color=(0, 0, 0, 255), tolerance=8)
        assert r.doc.frames[0].image.getpixel((1, 0)) == (0, 0, 0, 255)

    def test_it_fills_transparent_regions_too(self):
        """The common GIF case: click the transparent background and give it a
        colour. Transparency is just another value to the match mask."""
        im = Image.new("RGBA", (6, 6), (0, 0, 0, 0))
        d = Document((Frame.new(im, 100),), (6, 6))
        r = run("paint.fill", d, index=0, x=3, y=3, color=(255, 0, 0, 255))
        assert r.doc.frames[0].image.getpixel((0, 0)) == (255, 0, 0, 255)

    def test_a_seed_outside_the_canvas_declines(self):
        d = striped()
        assert run("paint.fill", d, index=0, x=99, y=99, color=(0, 255, 0, 255)).doc is d
        assert run("paint.fill", d, index=0, x=-1, y=0, color=(0, 255, 0, 255)).doc is d

    def test_filling_with_the_colour_already_there_declines(self):
        d = striped()
        assert run("paint.fill", d, index=0, x=0, y=0, color=(0, 0, 255, 255)).doc is d

    def test_it_leaves_the_playhead_on_the_frame_it_painted(self):
        d = doc(3, (8, 8))
        r = run("paint.fill", d, index=2, x=1, y=1, color=(255, 0, 0, 255))
        assert r.selection.ordered == (2,)

    def test_only_the_indexed_frame_changes(self):
        d = doc(3, (8, 8))
        r = run("paint.fill", d, index=1, x=1, y=1, color=(255, 0, 0, 255))
        assert r.doc.frames[1].image_uid != d.frames[1].image_uid
        assert r.doc.frames[0].image_uid == d.frames[0].image_uid
        assert r.doc.frames[2].image_uid == d.frames[2].image_uid


class TestShapes:
    def test_rect_outline_leaves_its_interior_alone(self):
        d = doc(1, (10, 10))
        r = run("paint.shape", d, index=0, kind="rect", x0=2, y0=2, x1=7, y1=7,
                size=1, color=(255, 0, 0, 255))
        img = r.doc.frames[0].image
        assert img.getpixel((2, 2)) == (255, 0, 0, 255)      # corner
        assert img.getpixel((5, 2)) == (255, 0, 0, 255)      # top edge
        assert img.getpixel((5, 5)) == (255, 255, 255, 255)  # interior untouched

    def test_a_filled_rect_covers_its_interior(self):
        d = doc(1, (10, 10))
        r = run("paint.shape", d, index=0, kind="rect", x0=2, y0=2, x1=7, y1=7,
                size=1, color=(255, 0, 0, 255), filled=True)
        assert r.doc.frames[0].image.getpixel((5, 5)) == (255, 0, 0, 255)

    def test_the_box_is_pixel_inclusive_at_both_ends(self):
        """Unlike a crop box, whose coordinates are the boundaries *between*
        pixels. A rect from 2 to 7 covers pixel 7 and not pixel 8; getting this
        wrong is a one-pixel error invisible at 1:1 and obvious at 30x."""
        d = doc(1, (10, 10))
        r = run("paint.shape", d, index=0, kind="rect", x0=2, y0=2, x1=7, y1=7,
                size=1, color=(255, 0, 0, 255), filled=True)
        img = r.doc.frames[0].image
        assert img.getpixel((7, 7)) == (255, 0, 0, 255)
        assert img.getpixel((8, 7)) == (255, 255, 255, 255)

    def test_a_backwards_drag_draws_the_same_rect(self):
        d = doc(1, (10, 10))
        forward = run("paint.shape", d, index=0, kind="rect", x0=2, y0=2, x1=7, y1=7,
                      size=1, color=(255, 0, 0, 255), filled=True)
        backward = run("paint.shape", d, index=0, kind="rect", x0=7, y0=7, x1=2, y1=2,
                       size=1, color=(255, 0, 0, 255), filled=True)
        assert forward.doc.frames[0].image.tobytes() == backward.doc.frames[0].image.tobytes()

    def test_ellipse_is_round_not_square(self):
        d = doc(1, (12, 12))
        r = run("paint.shape", d, index=0, kind="ellipse", x0=1, y0=1, x1=10, y1=10,
                size=1, color=(255, 0, 0, 255), filled=True)
        img = r.doc.frames[0].image
        assert img.getpixel((5, 5)) == (255, 0, 0, 255)      # centre is inside
        assert img.getpixel((1, 1)) == (255, 255, 255, 255)  # the corner is not

    def test_line_paints_both_ends_and_the_middle(self):
        d = doc(1, (10, 10))
        r = run("paint.shape", d, index=0, kind="line", x0=1, y0=1, x1=8, y1=8,
                size=1, color=(255, 0, 0, 255))
        img = r.doc.frames[0].image
        assert img.getpixel((1, 1)) == (255, 0, 0, 255)
        assert img.getpixel((4, 4)) == (255, 0, 0, 255)
        assert img.getpixel((8, 8)) == (255, 0, 0, 255)

    def test_line_ignores_filled(self):
        d = doc(1, (10, 10))
        plain = run("paint.shape", d, index=0, kind="line", x0=1, y0=1, x1=8, y1=8,
                    size=1, color=(255, 0, 0, 255))
        filled = run("paint.shape", d, index=0, kind="line", x0=1, y0=1, x1=8, y1=8,
                     size=1, color=(255, 0, 0, 255), filled=True)
        assert plain.doc.frames[0].image.tobytes() == filled.doc.frames[0].image.tobytes()

    def test_size_thickens_an_outline(self):
        d = doc(1, (16, 16))
        thin = run("paint.shape", d, index=0, kind="rect", x0=3, y0=3, x1=12, y1=12,
                   size=1, color=(255, 0, 0, 255))
        thick = run("paint.shape", d, index=0, kind="rect", x0=3, y0=3, x1=12, y1=12,
                    size=3, color=(255, 0, 0, 255))
        assert thin.doc.frames[0].image.getpixel((4, 4)) == (255, 255, 255, 255)
        assert thick.doc.frames[0].image.getpixel((4, 4)) == (255, 0, 0, 255)

    def test_a_shape_off_the_canvas_is_clipped_not_an_error(self):
        d = doc(1, (8, 8))
        r = run("paint.shape", d, index=0, kind="rect", x0=-20, y0=-20, x1=3, y1=3,
                size=1, color=(255, 0, 0, 255), filled=True)
        assert r.doc.frames[0].image.getpixel((0, 0)) == (255, 0, 0, 255)

    def test_a_shape_entirely_off_the_canvas_declines(self):
        d = doc(1, (8, 8))
        r = run("paint.shape", d, index=0, kind="rect", x0=-40, y0=-40, x1=-30, y1=-30,
                size=1, color=(255, 0, 0, 255), filled=True)
        assert r.doc is d

    def test_an_unknown_kind_declines_rather_than_guessing(self):
        """So a typo in a future tool surfaces as "nothing to do" rather than
        quietly drawing a rectangle."""
        d = doc(1, (8, 8))
        r = run("paint.shape", d, index=0, kind="squiggle", x0=1, y0=1, x1=5, y1=5,
                size=1, color=(255, 0, 0, 255))
        assert r.doc is d

    def test_it_leaves_the_playhead_on_the_frame_it_painted(self):
        d = doc(3, (8, 8))
        r = run("paint.shape", d, index=2, kind="rect", x0=1, y0=1, x1=4, y1=4,
                size=1, color=(255, 0, 0, 255))
        assert r.selection.ordered == (2,)
