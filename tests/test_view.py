"""The view transform, headless.

`ui/tk/view.py` imports no toolkit, so zoom and pan -- the arithmetic, which is
where this sort of feature actually goes wrong -- are testable without a
display. If these tests ever need Tk, the seam has leaked.

The mapping tests here matter more than their size suggests. ARCHITECTURE §19.1
records two half-pixel bugs that were invisible at 1:1 and 15 screen pixels
wrong at 30x. Until this slice, "30x" was hypothetical and those tests were
synthetic. Now it is reachable from a menu.
"""

from __future__ import annotations

import pytest

from giflite.ui.tk.view import (
    GRID_ALWAYS,
    GRID_AUTO,
    GRID_AUTO_SCALE,
    GRID_MIN_SCALE,
    GRID_OFF,
    LADDER,
    ViewTransform,
)


def make_view(viewport=(400, 300), source=(100, 80)) -> ViewTransform:
    view = ViewTransform()
    view.set_viewport(*viewport)
    view.set_source(*source)
    return view


# ---- fit -----------------------------------------------------------------


def test_fit_scales_to_the_smaller_axis():
    # 100x80 into 400x300 less 16px padding: 384/100 = 3.84, 284/80 = 3.55.
    view = make_view()
    assert view.fit_scale == pytest.approx(284 / 80)


def test_fit_snaps_to_one_to_one_when_it_is_within_a_percent():
    view = make_view(viewport=(116, 116), source=(100, 100))
    assert view.fit_scale == 1.0


def test_fit_stays_fit_across_a_resize():
    """The reason scale is None rather than a number. A baked float would hold
    the old percentage while the window grew around it."""
    view = make_view()
    first = view.scale
    view.set_viewport(800, 600)
    assert view.is_fit
    assert view.scale != first
    assert view.scale == pytest.approx(584 / 80)


def test_fit_geometry_centres_the_image():
    view = make_view()
    left, top, fw, fh = view.geometry()
    assert (left, top) == ((400 - fw) // 2, (300 - fh) // 2)
    assert fw <= 400 and fh <= 300


def test_fit_shows_the_whole_source():
    """At fit the renderer's crop is the entire image, so the pre-zoom path is
    unchanged and the playback bitmap cache still hits."""
    view = make_view()
    assert view.visible_source_rect() == (0, 0, 100, 80)


# ---- the ladder ----------------------------------------------------------


def test_zoom_in_from_fit_lands_on_the_next_rung_up():
    view = make_view()               # fit is 3.55
    assert view.zoom_in()
    assert view.scale == 4.0
    assert not view.is_fit


def test_zoom_out_from_fit_lands_on_the_next_rung_down():
    view = make_view()               # fit is 3.55
    assert view.zoom_out()
    assert view.scale == 2.0


def test_the_ladder_has_ends():
    view = make_view()
    view.set_scale(LADDER[-1])
    assert not view.can_zoom_in
    assert view.zoom_in() is False
    assert view.scale == LADDER[-1]

    view.set_scale(LADDER[0])
    assert not view.can_zoom_out
    assert view.zoom_out() is False
    assert view.scale == LADDER[0]


def test_set_scale_is_capped_to_the_ladder():
    view = make_view()
    view.set_scale(9999)
    assert view.scale == LADDER[-1]


def test_actual_size_is_one_to_one():
    view = make_view()
    view.actual_size()
    assert view.scale == 1.0
    left, top, fw, fh = view.geometry()
    assert (fw, fh) == (100, 80)


def test_fit_recentres_but_actual_size_does_not():
    """Fitting is a request to see everything, so a pan offset would defeat it.
    Asking for 1:1 is a request to inspect what is under the middle."""
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(1.0)
    view.pan_right()
    view.pan_down()
    panned = view.center
    assert panned != (500, 500)

    view.actual_size()
    assert view.center == panned

    view.fit()
    assert view.center == (500, 500)


# ---- pan -----------------------------------------------------------------


def test_pan_is_ignored_when_the_image_fits():
    """Nothing to look around at. The image stays centred rather than drifting
    off to one side."""
    view = make_view(viewport=(400, 300), source=(20, 20))
    view.set_scale(1.0)
    before = view.geometry()
    assert view.pan_right() is False
    assert view.geometry() == before
    assert not view.can_pan_x
    assert not view.can_pan_y


def test_panning_moves_the_view_right_and_the_image_left():
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(1.0)
    left_before, _, _, _ = view.geometry()
    assert view.pan_right()
    left_after, _, _, _ = view.geometry()
    assert left_after < left_before


def test_a_pan_step_is_a_quarter_of_the_viewport():
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(1.0)
    cx, _ = view.center
    view.pan_right()
    assert view.center[0] == pytest.approx(cx + 50)


def test_pan_never_reveals_pasteboard_on_an_axis_with_image_to_spare():
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(1.0)
    for _ in range(50):
        view.pan_right()
        view.pan_down()
    left, top, fw, fh = view.geometry()
    assert left <= 0 and top <= 0
    assert left + fw >= 200 and top + fh >= 200


def test_pan_stops_reporting_movement_at_the_edge():
    """With buttons and no drag, a control that looks live but does nothing is
    the only feedback there is -- so the edge has to be detectable."""
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(1.0)
    while view.pan_right():
        pass
    assert view.pan_right() is False


@pytest.mark.parametrize("source,rung", [
    (333, 0.5), (101, 0.5), (777, 0.25), (99, 2.0), (37, 8.0), (1000, 1.0),
])
def test_the_edge_holds_even_when_the_scaled_size_does_not_divide_evenly(source, rung):
    """Two scales are in play and they are not quite the same number.

    The centre is clamped against the *requested* scale, but the image is drawn
    at `width // source`, which truncates. On a source whose scaled size lands
    between pixels the two disagree by a fraction, and the pan limit derived
    from one is off by a pixel against the other -- a one-pixel strip of
    pasteboard down the right edge at full pan. Cheap to prevent, invisible
    until someone screenshots it.
    """
    view = make_view(viewport=(100, 100), source=(source, source))
    view.set_scale(rung)
    for _ in range(200):
        view.pan_right()
        view.pan_down()
    left, top, fw, fh = view.geometry()
    if fw > 100:
        assert left + fw >= 100 and left <= 0
    if fh > 100:
        assert top + fh >= 100 and top <= 0


def test_axis_origin_is_the_guard_for_an_unclamped_centre():
    """Tested directly, and deliberately.

    Every route through this class clamps the centre before geometry sees it,
    so the limit inside `_axis_origin` never fires in practice -- dropping it
    breaks nothing that goes via `nudge`. It stays because the next pan input
    (a drag) would set a centre from raw mouse deltas, and this is where that
    lands. Asserting the contract here keeps it honest code rather than
    untested code that happens to be right.
    """
    # viewport 100, image 500 wide at 1:1, asked to centre on a point far off
    # the right-hand end. The image must still cover the viewport.
    assert ViewTransform._axis_origin(100, 500, 9999, 1.0) == -400
    assert ViewTransform._axis_origin(100, 500, -9999, 1.0) == 0
    # and an axis with nothing to pan ignores the centre entirely
    assert ViewTransform._axis_origin(100, 40, 9999, 1.0) == 30


def test_can_pan_reports_per_axis():
    view = make_view(viewport=(400, 100), source=(100, 100))
    view.set_scale(2.0)     # 200x200: taller than the viewport, not wider
    assert not view.can_pan_x
    assert view.can_pan_y


# ---- zoom holds your place -----------------------------------------------


def test_zoom_holds_the_centre():
    """The whole reason pan is stored as an image point rather than an offset."""
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(2.0)
    view.pan_right()
    view.pan_down()
    held = view.center

    view.zoom_in()
    assert view.center == pytest.approx(held)
    view.zoom_out()
    assert view.center == pytest.approx(held)


def test_zooming_out_far_enough_recentres_rather_than_holding_a_stale_offset():
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(4.0)
    for _ in range(20):
        view.pan_right()
    view.set_scale(0.125)          # 125x125, smaller than the viewport
    assert view.center == (500, 500)
    left, top, fw, fh = view.geometry()
    assert (left, top) == ((200 - fw) // 2, (200 - fh) // 2)


# ---- the document changing size under the view ---------------------------


def test_a_crop_keeps_the_zoom():
    """You cropped in order to look at what is left; being thrown back to fit at
    that moment is the wrong answer."""
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(4.0)
    view.set_source(300, 300)
    assert view.scale == 4.0


def test_a_crop_pulls_a_now_impossible_centre_back_in_bounds():
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(1.0)
    for _ in range(50):
        view.pan_right()
        view.pan_down()
    assert view.center[0] > 300

    view.set_source(200, 200)      # the pan target no longer exists
    cx, cy = view.center
    assert 0 <= cx <= 200 and 0 <= cy <= 200
    left, top, fw, fh = view.geometry()
    assert (left, top) == ((200 - fw) // 2, (200 - fh) // 2)


def test_reset_goes_back_to_fit_and_centre():
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(8.0)
    view.pan_right()
    view.reset()
    assert view.is_fit
    assert view.center == (500, 500)


# ---- the visible rectangle -----------------------------------------------


def test_the_visible_rect_is_viewport_bounded_however_far_in_you_zoom():
    """The renderer composes only this rectangle. Composing the whole image at
    32x would be ~4 GB of RGBA for a 1000x1000 GIF; this stays viewport-sized."""
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    for rung in (1.0, 4.0, 16.0, 32.0):
        view.set_scale(rung)
        x0, y0, x1, y1 = view.visible_source_rect()
        assert (x1 - x0) * rung <= 200 + 2 * rung
        assert (y1 - y0) * rung <= 200 + 2 * rung


def test_the_visible_rect_rounds_outward():
    """Inward rounding would leave an uncovered sliver at the viewport edge."""
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(3.0)            # deliberately off-ladder: a partial pixel
    left, top, _, _ = view.geometry()
    x0, y0, x1, y1 = view.visible_source_rect()
    assert left + x0 * 3.0 <= 0
    assert left + x1 * 3.0 >= 200


def test_the_visible_rect_never_collapses():
    """A viewport smaller than one displayed pixel still has to render one, or
    Pillow is handed a zero-sized crop and raises."""
    view = make_view(viewport=(4, 4), source=(1000, 1000))
    view.set_scale(32.0)
    x0, y0, x1, y1 = view.visible_source_rect()
    assert x1 > x0 and y1 > y0


def test_the_visible_rect_stays_inside_the_source():
    view = make_view(viewport=(900, 900), source=(100, 80))
    view.set_scale(2.0)
    assert view.visible_source_rect() == (0, 0, 100, 80)


# ---- the mapping tools depend on (ARCHITECTURE §19.1) --------------------
#
# These exercise ViewTransform's own `image_to_display` / `display_to_image`.
# They previously tested local copies of the canvas's versions, which was worth
# roughly nothing: a copy agreeing with itself proves the arithmetic is
# self-consistent, not that it matches what the canvas actually does. The real
# functions moved here when the navigator needed them too.


@pytest.mark.parametrize("rung", [1.0, 2.0, 8.0, 32.0])
def test_clicking_a_pixels_centre_addresses_that_pixel_at_every_zoom(rung):
    view = make_view(viewport=(300, 300), source=(100, 80))
    view.set_scale(rung)
    for target in ((0, 0), (7, 3), (50, 40), (99, 79)):
        cx, cy = view.image_to_display(*target, center=True)
        assert view.display_to_image(cx, cy) == target


@pytest.mark.parametrize("rung", [1.0, 4.0, 16.0])
def test_the_mapping_holds_after_panning(rung):
    view = make_view(viewport=(150, 150), source=(200, 200))
    view.set_scale(rung)
    view.pan_right()
    view.pan_down()
    for target in ((10, 10), (100, 100), (199, 199)):
        cx, cy = view.image_to_display(*target, center=True)
        assert view.display_to_image(cx, cy) == target


def test_edge_snapping_addresses_boundaries_not_pixels():
    """A crop box is described by the lines *between* pixels, so it rounds to
    the nearest boundary and clamps to 0..src inclusive."""
    view = make_view(viewport=(300, 300), source=(100, 80))
    view.set_scale(2.0)
    corner = view.image_to_display(10, 10)          # boundary, not pixel centre
    assert view.display_to_image(*corner, snap="edge") == (10, 10)
    left, top, fw, fh = view.geometry()
    assert view.display_to_image(left - 500, top - 500, snap="edge") == (0, 0)
    assert view.display_to_image(left + fw + 500, top + fh + 500,
                                 snap="edge") == (100, 80)


def test_pixel_snapping_does_not_clamp():
    """The paint ops clip off-canvas points for free; clamping here would smear
    a stroke that runs off the edge along the border instead of letting it go."""
    view = make_view(viewport=(300, 300), source=(100, 80))
    view.set_scale(2.0)
    left, top, _, _ = view.geometry()
    assert view.display_to_image(left - 40, top - 40) == (-20, -20)


def test_a_whole_number_zoom_gives_every_pixel_the_same_block_size():
    """Why the ladder is integers above 1:1: uneven blocks are what shimmering
    pixel art looks like from the inside."""
    source = (37, 37)              # deliberately not a round number
    view = make_view(viewport=(900, 900), source=source)
    view.set_scale(8.0)
    widths = {
        view.image_to_display(i + 1, 0)[0] - view.image_to_display(i, 0)[0]
        for i in range(source[0])
    }
    assert widths == {8.0}


# ---- center_on: what the navigator drags against -------------------------


def test_center_on_puts_the_point_in_the_middle():
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(1.0)
    view.center_on(400, 700)
    assert view.center == (400, 700)
    # and the geometry agrees: that image point lands mid-viewport
    x, y = view.image_to_display(400, 700)
    assert abs(x - 100) <= 1 and abs(y - 100) <= 1


def test_center_on_clamps_rather_than_refusing():
    """Dragging past the edge of the map should slide the view to the edge and
    stop, not decline to move."""
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(1.0)
    assert view.center_on(99999, 99999)
    cx, cy = view.center
    assert cx == 900 and cy == 900          # 1000 - viewport/2
    left, top, fw, fh = view.geometry()
    assert left + fw == 200 and top + fh == 200


def test_center_on_reports_whether_anything_moved():
    view = make_view(viewport=(200, 200), source=(1000, 1000))
    view.set_scale(1.0)
    view.center_on(400, 400)
    assert view.center_on(400, 400) is False


def test_center_on_is_ignored_on_an_axis_with_nothing_to_pan():
    view = make_view(viewport=(400, 400), source=(50, 50))
    view.set_scale(1.0)
    view.center_on(0, 0)
    assert view.center == (25, 25)


# ---- the navigator's own transform ---------------------------------------


def test_a_second_transform_can_use_a_smaller_pad():
    """The navigator thumbnail is this same class, fit-locked, in a ~160px
    panel -- where the preview's 16px of breathing room is a tenth of the
    width."""
    wide = ViewTransform()
    tight = ViewTransform(fit_pad=4)
    for view in (wide, tight):
        view.set_viewport(160, 160)
        view.set_source(100, 100)
    assert tight.fit_scale > wide.fit_scale
    assert tight.fit_scale == pytest.approx(156 / 100)
    assert wide.fit_scale == pytest.approx(144 / 100)


# ---- the pixel grid ------------------------------------------------------
#
# The grid divides pixels, so the one thing it must never do is disagree with
# the mapping that decides which pixel you clicked. These tests hold the rules
# against `image_to_display`/`display_to_image` rather than against a formula of
# their own -- a grid checked by its own arithmetic is the ARCHITECTURE 19.1
# mistake with a fresh coat of paint.


def test_grid_defaults_to_auto():
    assert make_view().grid_mode == GRID_AUTO


def test_grid_mode_rejects_an_unknown_value():
    with pytest.raises(ValueError):
        make_view().set_grid_mode("sometimes")


def test_grid_mode_reports_whether_it_changed():
    view = make_view()
    assert view.set_grid_mode(GRID_ALWAYS) is True
    assert view.set_grid_mode(GRID_ALWAYS) is False


def test_grid_mode_cycles_off_auto_always():
    view = make_view()
    view.set_grid_mode(GRID_OFF)
    assert [view.cycle_grid_mode() for _ in range(4)] == [
        GRID_AUTO, GRID_ALWAYS, GRID_OFF, GRID_AUTO,
    ]


def test_off_hides_the_grid_at_every_rung():
    view = make_view(viewport=(400, 300), source=(20, 20))
    view.set_grid_mode(GRID_OFF)
    for rung in LADDER:
        view.set_scale(rung)
        assert view.grid_visible is False
        assert view.grid_lines() is None


def test_auto_shows_the_grid_from_four_times_and_no_earlier():
    view = make_view(viewport=(400, 300), source=(20, 20))
    view.set_grid_mode(GRID_AUTO)
    for rung in LADDER:
        view.set_scale(rung)
        assert view.grid_visible is (rung >= GRID_AUTO_SCALE), f"at {rung}x"


def test_always_reaches_further_down_than_auto_but_stops_at_the_floor():
    """`Always` has to mean something `Auto` doesn't, or the third state is
    decoration -- and it still has a floor, because at 1:1 the rules touch and
    the grid is a flat fill costing one canvas item per source pixel."""
    view = make_view(viewport=(400, 300), source=(20, 20))
    view.set_grid_mode(GRID_ALWAYS)
    for rung in LADDER:
        view.set_scale(rung)
        assert view.grid_visible is (rung >= GRID_MIN_SCALE), f"at {rung}x"
    assert GRID_MIN_SCALE < GRID_AUTO_SCALE


def test_a_mode_that_is_on_but_invisible_reports_itself():
    """The frontend says so in the status line. Two of the three modes can be
    switched on and change nothing you can see."""
    view = make_view(viewport=(400, 300), source=(20, 20))
    view.set_scale(1.0)
    view.set_grid_mode(GRID_AUTO)
    assert view.grid_suppressed is True
    view.set_scale(8.0)
    assert view.grid_suppressed is False
    view.set_grid_mode(GRID_OFF)
    assert view.grid_suppressed is False   # off is not "suppressed", it is off


def test_grid_survives_a_reset():
    """Scale and pan describe this document; whether you like a grid is about
    you. Resetting it on every open is the mistake resetting the active tool
    would be."""
    view = make_view()
    view.set_grid_mode(GRID_ALWAYS)
    view.reset()
    assert view.grid_mode == GRID_ALWAYS


def test_grid_rules_land_exactly_where_the_mapping_puts_pixel_boundaries():
    view = make_view(viewport=(400, 300), source=(20, 20))
    view.set_scale(8.0)
    lines = view.grid_lines()
    x0, y0, x1, y1 = view.visible_source_rect()
    assert lines.xs == tuple(view.image_to_display(ix, y0)[0] for ix in range(x0, x1 + 1))
    assert lines.ys == tuple(view.image_to_display(x0, iy)[1] for iy in range(y0, y1 + 1))


def test_a_rule_is_the_boundary_between_the_pixels_either_side_of_it():
    """The check that actually matters. Land a point a hair left of a rule and a
    hair right of it, and `display_to_image` must name the two pixels the rule
    claims to separate. If this fails the grid is lying about the pixels."""
    view = make_view(viewport=(400, 300), source=(20, 20))
    view.set_scale(8.0)
    lines = view.grid_lines()
    x0, _, _, _ = view.visible_source_rect()
    for offset, x in enumerate(lines.xs[1:-1], start=1):   # skip the outer edges
        left_px, _ = view.display_to_image(x - 0.25, lines.top + 0.5)
        right_px, _ = view.display_to_image(x + 0.25, lines.top + 0.5)
        assert (left_px, right_px) == (x0 + offset - 1, x0 + offset), f"rule at {x}"


def test_grid_rules_only_ever_fall_on_whole_display_pixels():
    """Every rung at or above the floor is an integer and `geometry()` quantises
    the origin, so a rule can never land on a genuine half-pixel and blur.

    The tolerance is not slack. `image_to_display` computes `ix / sw * fw`, and
    that division leaves float dust: panned onto pixel 287 of a 400px source at
    8x, the boundary comes back as -3.999999999999993 rather than -4.0. It is
    ~1e-14 of a screen pixel and Tk rounds it away, so it is invisible -- but
    asserting `is_integer()` here would be asserting something that is false for
    a reason that does not matter, and the assertion would then be "fixed" by
    rounding in `grid_lines`, which is how a grid stops agreeing with the
    mapping. Dust is fine; a real half-pixel is not, and 0.01 tells them apart.
    """
    view = make_view(viewport=(400, 300), source=(400, 400))
    view.set_grid_mode(GRID_ALWAYS)
    for rung in (r for r in LADDER if r >= GRID_MIN_SCALE):
        view.set_scale(rung)
        view.center_on(287, 287)
        lines = view.grid_lines()
        assert all(abs(v - round(v)) < 0.01 for v in lines.xs), f"at {rung}x"
        assert all(abs(v - round(v)) < 0.01 for v in lines.ys), f"at {rung}x"


def test_the_grid_stops_at_the_image_and_does_not_cross_the_pasteboard():
    """A small image at high zoom does not fill the viewport; the rules have to
    stop at the artwork, not run out over the background."""
    view = make_view(viewport=(400, 400), source=(10, 10))
    view.set_grid_mode(GRID_ALWAYS)
    view.set_scale(4.0)
    left, top, fw, fh = view.geometry()
    lines = view.grid_lines()
    assert (lines.left, lines.top) == (left, top)
    assert (lines.right, lines.bottom) == (left + fw, top + fh)
    assert len(lines.xs) == 11 and len(lines.ys) == 11   # 10 pixels, 11 boundaries


def test_the_rule_count_is_bounded_by_the_viewport_not_by_the_image():
    """Why high zoom is affordable at all: the same reason `visible_source_rect`
    exists. 32x on a 4000px image is the same handful of rules as 32x on a 40px
    one."""
    small = make_view(viewport=(320, 320), source=(40, 40))
    huge = make_view(viewport=(320, 320), source=(4000, 4000))
    for view in (small, huge):
        view.set_grid_mode(GRID_ALWAYS)
        view.set_scale(32.0)
    assert len(huge.grid_lines().xs) == len(small.grid_lines().xs)
    assert len(huge.grid_lines().xs) <= 320 // 32 + 2


def test_grid_follows_a_pan_rather_than_starting_at_the_image_origin():
    view = make_view(viewport=(200, 200), source=(400, 400))
    view.set_grid_mode(GRID_ALWAYS)
    view.set_scale(8.0)
    def snapshot():
        """Read the rules and what they should be *at this pan*. Both halves
        have to be sampled together: the first draft of this test read them back
        through the final geometry, so it compared a value with itself and
        passed for the wrong reason."""
        first = view.visible_source_rect()[0]
        return first, view.image_to_display(first, 0)[0], view.grid_lines()

    view.center_on(20, 20)
    near_first, near_expected, near = snapshot()
    view.center_on(300, 300)
    far_first, far_expected, far = snapshot()
    # Same count of rules, but over different source pixels: the grid is
    # anchored to the image, not to the viewport. Each side is checked against
    # the mapping *at the time*, which the first draft of this test got wrong --
    # it read both back through the final geometry and so compared a value with
    # itself, and passed for the wrong reason.
    assert len(near.xs) == len(far.xs)
    assert near_first != far_first
    assert near.xs[0] == pytest.approx(near_expected, abs=0.01)
    assert far.xs[0] == pytest.approx(far_expected, abs=0.01)


def test_an_absurd_rule_count_declines_rather_than_freezing_the_window():
    """Unreachable through the ladder -- the floor bounds a 4K viewport well
    inside the cap -- so this pins the guard rather than the policy."""
    view = make_view(viewport=(100_000, 100_000), source=(100_000, 100_000))
    view.set_grid_mode(GRID_ALWAYS)
    view.set_scale(2.0)
    assert view.grid_visible is True
    assert view.grid_lines() is None
