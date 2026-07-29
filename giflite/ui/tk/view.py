"""The preview's view transform: what part of the image is on screen, and how big.

Zoom and pan are entirely the frontend's business (ARCHITECTURE.md §9) -- the
controller hands over full-resolution pixels and takes no view of how they are
displayed. This module is where that decision actually lives.

Like `tools.py`, it imports no toolkit. Everything here is arithmetic over
integers and floats, so `tests/test_view.py` covers it headlessly and the Tk
canvas is left with nothing but drawing. If these tests ever need a display,
the seam has leaked.

**The one integration point** is `geometry()`, which returns exactly the
`(left, top, fw, fh)` tuple `PreviewCanvas._image_geom` already publishes. Every
coordinate mapping in the canvas -- and therefore every tool -- reads through
that tuple and cannot tell whether it came from fit-to-window or from a manual
zoom. That is why this slice adds no changes to `tools.py` at all.

Two representation choices carry the design:

**Scale is `None` for fit, not a number.** Fit has to *stay* fit across a window
resize and across canvas ops that change the image's dimensions. Baking the
current fit factor into a float would silently unstick it -- the view would hold
37.4% while the window grew around it, which reads as a bug and is tedious to
diagnose.

**Pan is stored as the image point held at the viewport centre**, not as a pixel
offset. The centre is invariant under zoom, so zooming holds your place for
free; and re-clamping after a crop is one clamp of a point into the new bounds.
A pixel offset would have to be re-derived on every scale change, which is
precisely the sort of arithmetic that ends up half a pixel wrong (§19.1).
"""

from __future__ import annotations

import math
from typing import NamedTuple

# Zoom rungs. Discrete rather than continuous, and integers above 1:1 on
# purpose: NEAREST upscaling by a whole number maps each source pixel onto an
# exact block of screen pixels, so pixel art stays crisp and stays *still*. A
# fractional scale distributes the rounding error unevenly across the image and
# shimmers as you pan.
LADDER = (0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)

# Breathing room around the image at fit scale, in display pixels.
FIT_PAD = 16

# How far one pan step moves, as a fraction of the viewport. A quarter is enough
# to feel like progress and small enough to keep your bearings. The navigator
# pans absolutely rather than in steps, so this now serves `nudge` and whatever
# uses it next (keyboard panning being the obvious one).
PAN_STEP = 0.25

# Float comparisons against the ladder. Scales here are powers of two and exact
# in binary, but a fit scale arriving from a division is not, and `>=` against
# it decides whether zoom-in has anywhere to go.
_EPS = 1e-6

# ---- pixel grid ----------------------------------------------------------
#
# Three states rather than a checkbox, because "should the grid be on" and "is
# the grid useful right now" are different questions and answering both with one
# boolean makes the control lie in one direction or the other.
GRID_OFF = "off"
GRID_AUTO = "auto"          # visible only once a pixel is a worthwhile block
GRID_ALWAYS = "always"      # visible wherever it can be drawn at all
GRID_MODES = (GRID_OFF, GRID_AUTO, GRID_ALWAYS)

# Where AUTO starts. 4x is the first rung at which a 1px rule between cells is a
# quarter of the cell rather than most of it, so the grid reads as a guide
# instead of as a wash over the artwork.
GRID_AUTO_SCALE = 4.0

# The floor even ALWAYS respects, and the reason it is a floor rather than a
# second opinion about what the user asked for: at 1 display pixel per source
# pixel adjacent rules touch and the "grid" is a flat fill conveying nothing,
# while costing one canvas item per source pixel on every redraw -- during
# playback, at 30fps. 2x is the last rung where the cells are still cells.
GRID_MIN_SCALE = 2.0

# A hard ceiling on how many rules a single redraw may produce. Unreachable
# through the ladder (the floor above bounds a 4K viewport well inside it), so
# this is a guard against a future non-ladder scale, not a policy -- and it
# fails loudly by way of `grid_lines` returning nothing rather than by freezing
# the window for a second.
GRID_MAX_LINES = 4096


class GridLines(NamedTuple):
    """Where to draw the pixel grid, in display coordinates.

    `xs` are vertical rules, each spanning `top`..`bottom`; `ys` are horizontal
    rules spanning `left`..`right`. The spans are the *visible* extent of the
    image, so the grid stops at the artwork rather than running out over the
    pasteboard, and both lists are bounded by the viewport rather than by the
    image -- the same reason `visible_source_rect` exists.
    """

    xs: tuple[float, ...]
    ys: tuple[float, ...]
    left: float
    top: float
    right: float
    bottom: float


class ViewTransform:
    """Scale + pan for the preview surface.

    Stateful by design: the canvas keeps it informed of the viewport and source
    size (`set_viewport`, `set_source`), which lets `zoom_in()`, `nudge()` and
    friends take no arguments and stay internally consistent. Statefulness is
    not impurity -- there is still no I/O, no toolkit and no clock in here.
    """

    def __init__(self, fit_pad: int = FIT_PAD) -> None:
        self._scale: float | None = None        # None == fit to window
        self._center: tuple[float, float] | None = None   # None == image centre
        self._viewport: tuple[int, int] = (0, 0)
        self._source: tuple[int, int] = (0, 0)
        # Configurable because the navigator thumbnail is a second, fit-locked
        # instance of this class in a ~160px panel, where the preview's 16px of
        # breathing room is a tenth of the width.
        self._fit_pad = max(int(fit_pad), 0)
        # AUTO by default: this is exactly the behaviour the grid was asked for
        # ("show it once I'm zoomed past 4x"), and a grid that has to be switched
        # on every session is a grid nobody uses. One constant to change.
        self._grid_mode = GRID_AUTO

    # ---- context ---------------------------------------------------------

    def set_viewport(self, width: int, height: int) -> bool:
        """Tell the transform how big the visible area is. Returns whether it
        changed, so the caller can skip a redraw."""
        size = (max(int(width), 0), max(int(height), 0))
        if size == self._viewport:
            return False
        self._viewport = size
        self._clamp()
        return True

    def set_source(self, width: int, height: int) -> bool:
        """Tell the transform how big the image is.

        Deliberately keeps the current scale and only re-clamps the centre. You
        crop in order to look closely at what is left; being thrown back to
        fit-to-window at that moment is the wrong answer. Clamping is what stops
        the old centre pointing off the end of a now-smaller canvas.
        """
        size = (max(int(width), 0), max(int(height), 0))
        if size == self._source:
            return False
        self._source = size
        self._clamp()
        return True

    def reset(self) -> None:
        """Back to fit, centred. For opening a document -- not for editing one.

        Deliberately leaves the grid mode alone. Scale and pan describe *this*
        document and mean nothing for the next one; whether you like a pixel
        grid is a preference about you, and resetting it on every open is the
        same mistake as resetting the active tool would be.
        """
        self._scale = None
        self._center = None

    # ---- scale -----------------------------------------------------------

    @property
    def fit_scale(self) -> float:
        """The scale at which the image fills the viewport, padding aside."""
        vw, vh = self._viewport
        sw, sh = self._source
        if sw <= 0 or sh <= 0 or vw <= 0 or vh <= 0:
            return 1.0
        scale = min(max(vw - self._fit_pad, 1) / sw, max(vh - self._fit_pad, 1) / sh)
        # Within a percent of 1:1, call it 1:1. This is inherited behaviour and
        # worth keeping: it spares a full-image resample for a difference nobody
        # can see, and it means a GIF that nearly fits is displayed exactly.
        if abs(scale - 1.0) < 0.01:
            return 1.0
        return scale

    @property
    def scale(self) -> float:
        """The scale actually in force, fit resolved to a number."""
        return self.fit_scale if self._scale is None else self._scale

    @property
    def is_fit(self) -> bool:
        return self._scale is None

    def set_scale(self, scale: float) -> None:
        """Pin the scale to `scale`, holding the current centre."""
        center = self.center           # resolve before the scale moves under us
        self._scale = max(LADDER[0], min(float(scale), LADDER[-1]))
        self._center = center
        self._clamp()

    def fit(self) -> None:
        """Fit to window, re-centred. Fitting is a request to see the whole
        image, so holding a pan offset would defeat it."""
        self._scale = None
        self._center = None

    def actual_size(self) -> None:
        """1:1. Holds the centre, unlike `fit` -- you are asking to inspect the
        pixels under the middle of the view, not to go somewhere else."""
        self.set_scale(1.0)

    def zoom_in(self) -> bool:
        current = self.scale
        for rung in LADDER:
            if rung > current + _EPS:
                self.set_scale(rung)
                return True
        return False

    def zoom_out(self) -> bool:
        current = self.scale
        for rung in reversed(LADDER):
            if rung < current - _EPS:
                self.set_scale(rung)
                return True
        return False

    @property
    def can_zoom_in(self) -> bool:
        return self.scale < LADDER[-1] - _EPS

    @property
    def can_zoom_out(self) -> bool:
        return self.scale > LADDER[0] + _EPS

    @property
    def percent(self) -> int:
        return max(1, int(round(self.scale * 100)))

    @property
    def label(self) -> str:
        """For the zoom readout. Fit reports its percentage too -- "Fit" alone
        leaves you unable to tell a 40% view from a 400% one."""
        return f"Fit ({self.percent}%)" if self.is_fit else f"{self.percent}%"

    # ---- pan -------------------------------------------------------------

    @property
    def center(self) -> tuple[float, float]:
        """The image point held at the centre of the viewport."""
        sw, sh = self._source
        if self._center is None:
            return (sw / 2, sh / 2)
        return self._center

    def nudge(self, dx: float, dy: float) -> bool:
        """Pan by `dx`/`dy` viewport-fractions (+x right, +y down).

        Positive `dx` moves the *view* right, so the image slides left -- the
        direction a button labelled "→" should go. Returns whether anything
        moved, which is how a button knows to stay disabled at the edge.
        """
        vw, vh = self._viewport
        scale = self.scale
        if scale <= 0:
            return False
        before = self.center
        cx, cy = before
        self._center = (cx + dx * vw / scale, cy + dy * vh / scale)
        self._clamp()
        return self.center != before

    def pan_left(self) -> bool:
        return self.nudge(-PAN_STEP, 0)

    def pan_right(self) -> bool:
        return self.nudge(PAN_STEP, 0)

    def pan_up(self) -> bool:
        return self.nudge(0, -PAN_STEP)

    def pan_down(self) -> bool:
        return self.nudge(0, PAN_STEP)

    def center_on(self, ix: float, iy: float) -> bool:
        """Put image point `(ix, iy)` in the middle of the viewport.

        What the navigator drags against: a point picked off a thumbnail is
        already an image coordinate, so there is nothing to convert and nothing
        to accumulate. Clamped like every other centre change, which is what
        makes dragging *past* the edge of the map behave -- the view slides to
        the edge and stops rather than refusing to move.
        """
        before = self.center
        self._center = (float(ix), float(iy))
        self._clamp()
        return self.center != before

    def center_view(self) -> None:
        self._center = None

    @property
    def can_pan_x(self) -> bool:
        """Whether the image is wider than the viewport.

        No production caller since the navigator replaced the pan buttons these
        were written for -- the map answers "is there more over there?"
        visually, by drawing a rectangle smaller than the image. Kept because it
        is the question any *other* pan input has to ask (keyboard, a drag) and
        it is three lines that are already covered; deleting and re-adding it
        would be churn, not economy.
        """
        left, _, fw, _ = self.geometry()
        return fw > self._viewport[0]

    @property
    def can_pan_y(self) -> bool:
        _, top, _, fh = self.geometry()
        return fh > self._viewport[1]

    # ---- geometry --------------------------------------------------------

    def scaled_size(self) -> tuple[int, int]:
        """The whole image's size on screen, in display pixels."""
        sw, sh = self._source
        scale = self.scale
        return (max(int(sw * scale), 1), max(int(sh * scale), 1))

    def geometry(self) -> tuple[int, int, int, int]:
        """`(left, top, width, height)` of the whole image in display pixels.

        This *is* `PreviewCanvas._image_geom`. Left/top may be negative when
        zoomed in -- the image extends past the viewport, which is the point --
        and the tuple always describes the entire image, never just the visible
        part. Every coordinate mapping in the canvas depends on that: a stroke
        that runs off the edge has to keep making sense.

        Integers, not floats. The scale is exact on the ladder, so quantising
        the origin to whole display pixels costs at most 1/scale of an image
        pixel of pan precision and buys pixel-exact block alignment when
        upscaling. The alternative shimmers.
        """
        vw, vh = self._viewport
        fw, fh = self.scaled_size()
        cx, cy = self.center
        scale_x = fw / self._source[0] if self._source[0] else 1.0
        scale_y = fh / self._source[1] if self._source[1] else 1.0
        left = self._axis_origin(vw, fw, cx, scale_x)
        top = self._axis_origin(vh, fh, cy, scale_y)
        return (left, top, fw, fh)

    @staticmethod
    def _axis_origin(viewport: int, extent: int, center: float, scale: float) -> int:
        """Where one axis of the image starts on screen.

        Smaller than the viewport: centred, and pan is ignored entirely rather
        than clamped to a range of zero width -- there is nothing to look around
        at, and letting the image drift off-centre on one axis while the other
        is pinned looks broken.

        Larger: the origin is placed so `center` lands mid-viewport, then held
        inside `[viewport - extent, 0]` so no pasteboard shows on an axis that
        has image to spare.

        That last clamp is unreachable through this class's own API -- `_clamp`
        runs after every mutation, and a centre already in range produces an
        origin already in range (verified, not assumed: dropping the clamp
        breaks no test that goes through `nudge`). It is kept as the guard for
        any caller that sets a centre directly, which is what a drag-pan would
        be, and `test_axis_origin_is_the_guard_for_an_unclamped_centre` holds
        it to that contract rather than leaving it as untested belt-and-braces.
        """
        if extent <= viewport:
            return (viewport - extent) // 2
        origin = int(round(viewport / 2 - center * scale))
        return max(viewport - extent, min(origin, 0))

    # ---- mapping ---------------------------------------------------------
    #
    # These lived in the canvas until the navigator needed the same arithmetic
    # against a *different* geometry (a thumbnail rather than the preview).
    # Duplicating them would have been the exact setup for the two half-pixel
    # bugs in 19.1 to reappear on one side only. They are pure functions of
    # `geometry()` and the source size, so they belong here, where they are
    # tested directly rather than through a display.

    def image_to_display(self, ix: float, iy: float,
                         center: bool = False) -> tuple[float, float]:
        """An image coordinate -> a point in display space.

        `center=False` gives the pixel's top-left *corner*, which is what a crop
        marquee wants (its coordinates are boundaries). `center=True` gives the
        middle of the pixel, which is what a brush preview wants: a stroke drawn
        through corners sits visibly half a pixel up and to the left of the
        cursor, and at 30x zoom half a pixel is 15 screen pixels of "the tool is
        off".
        """
        left, top, fw, fh = self.geometry()
        sw, sh = self._source
        if sw <= 0 or sh <= 0:
            return (float(left), float(top))
        offset = 0.5 if center else 0.0
        return (left + (ix + offset) / sw * fw,
                top + (iy + offset) / sh * fh)

    def display_to_image(self, dx: float, dy: float,
                         snap: str = "pixel") -> tuple[int, int]:
        """A display point -> image coordinates. Two honest answers here:

        `snap="pixel"` -- *which pixel is under the cursor*, what a brush or
        eyedropper needs. That is `floor`, not `round`: a pixel spans
        `[i, i+1)`, so rounding sends everything past its midpoint to the
        neighbour, and clicking the visible centre of a pixel paints the one to
        its right. Invisible at 1:1 and a whole pixel wrong at 30x zoom, which is
        exactly where pixel art gets edited. Not clamped -- the paint ops clip
        off-canvas points for free, and clamping would smear a stroke that runs
        off the edge along the border instead of just letting it leave.

        `snap="edge"` -- *the nearest pixel boundary*, what a crop box needs,
        since its coordinates are edges and not pixels. Rounding is correct here,
        and the result is clamped to `0..src` because a crop box has to be a
        valid rectangle inside the canvas.
        """
        left, top, fw, fh = self.geometry()
        sw, sh = self._source
        if sw <= 0 or sh <= 0 or fw <= 0 or fh <= 0:
            return (0, 0)
        fx = (dx - left) / fw * sw
        fy = (dy - top) / fh * sh
        if snap == "edge":
            return (max(0, min(round(fx), sw)), max(0, min(round(fy), sh)))
        return (math.floor(fx), math.floor(fy))

    def visible_source_rect(self) -> tuple[int, int, int, int]:
        """The part of the source that is actually on screen, as whole pixels.

        The reason the renderer can afford high zoom at all: composing the whole
        image at 32x would be a gigabyte of RGBA for a modest GIF, while this
        rectangle is viewport-bounded no matter how far in you go. Rounded
        *outward* to whole source pixels so the crop lands on pixel boundaries
        and an integer scale stays block-exact; the fractional remainder is
        carried by where the bitmap is placed, not by the resample.

        At fit this is the whole image, so the fit path is byte-for-byte what it
        was before zoom existed.
        """
        vw, vh = self._viewport
        left, top, fw, fh = self.geometry()
        sw, sh = self._source
        if sw <= 0 or sh <= 0:
            return (0, 0, 0, 0)
        scale_x = fw / sw
        scale_y = fh / sh
        x0 = _clip(math.floor((0 - left) / scale_x), 0, sw)
        x1 = _clip(math.ceil((vw - left) / scale_x), 0, sw)
        y0 = _clip(math.floor((0 - top) / scale_y), 0, sh)
        y1 = _clip(math.ceil((vh - top) / scale_y), 0, sh)
        # A viewport too small to hold a single pixel still has to render one,
        # or the canvas gets a zero-sized image and Pillow raises.
        if x1 <= x0:
            x1 = min(x0 + 1, sw)
        if y1 <= y0:
            y1 = min(y0 + 1, sh)
        return (x0, y0, x1, y1)

    # ---- pixel grid ------------------------------------------------------
    #
    # The grid is a *view* overlay: it never touches pixels, never reaches the
    # controller, and the canvas draws it as plain items rather than baking it
    # into the composed bitmap, so toggling it invalidates no cached frames.
    #
    # The rules are generated by calling `image_to_display` rather than by
    # walking `left + i * scale`. That is the whole point of putting this here:
    # a grid derived from its own copy of the arithmetic can disagree with the
    # mapping every tool uses, and ARCHITECTURE 19.1 is the record of what that
    # costs -- a grid half a pixel off from the pixels it claims to divide is
    # worse than no grid, because you would trust it.

    @property
    def grid_mode(self) -> str:
        return self._grid_mode

    def set_grid_mode(self, mode: str) -> bool:
        """Set off/auto/always. Returns whether it changed, so a caller can skip
        a redraw. Raises on an unknown mode rather than silently picking one --
        a typo'd mode that quietly means "off" is a bug you find by squinting.
        """
        if mode not in GRID_MODES:
            raise ValueError(f"unknown grid mode {mode!r}, expected one of {GRID_MODES}")
        if mode == self._grid_mode:
            return False
        self._grid_mode = mode
        return True

    def cycle_grid_mode(self) -> str:
        """Off -> Auto -> Always -> Off. What the keyboard shortcut drives."""
        nxt = GRID_MODES[(GRID_MODES.index(self._grid_mode) + 1) % len(GRID_MODES)]
        self._grid_mode = nxt
        return nxt

    @property
    def grid_visible(self) -> bool:
        """Whether the grid should be drawn at the current scale."""
        if self._grid_mode == GRID_OFF:
            return False
        floor = GRID_AUTO_SCALE if self._grid_mode == GRID_AUTO else GRID_MIN_SCALE
        return self.scale >= floor - _EPS

    @property
    def grid_suppressed(self) -> bool:
        """On, but too far out to draw. The frontend says so in the status line:
        a setting that silently does nothing is the failure this project keeps
        rediscovering, and "Auto" is precisely a setting that does nothing most
        of the time.
        """
        return self._grid_mode != GRID_OFF and not self.grid_visible

    def grid_lines(self) -> GridLines | None:
        """Rules for every source-pixel boundary currently on screen.

        `None` when the grid is not visible, or when the count would exceed
        `GRID_MAX_LINES`. Boundaries, not pixel centres, so `center=False` --
        the same coordinates the crop marquee snaps to, which is what makes the
        grid and the crop box agree about where a pixel ends.
        """
        if not self.grid_visible:
            return None
        sw, sh = self._source
        if sw <= 0 or sh <= 0:
            return None
        x0, y0, x1, y1 = self.visible_source_rect()
        if (x1 - x0) + (y1 - y0) + 2 > GRID_MAX_LINES:
            return None
        left, top = self.image_to_display(x0, y0)
        right, bottom = self.image_to_display(x1, y1)
        # Inclusive of both outer boundaries: the rule at the image edge is the
        # one that tells you where the canvas stops, and the border drawn around
        # the whole image is off-screen at any interesting zoom.
        xs = tuple(self.image_to_display(ix, y0)[0] for ix in range(x0, x1 + 1))
        ys = tuple(self.image_to_display(x0, iy)[1] for iy in range(y0, y1 + 1))
        return GridLines(xs=xs, ys=ys, left=left, top=top, right=right, bottom=bottom)

    # ---- internals -------------------------------------------------------

    def _clamp(self) -> None:
        """Pull the stored centre back inside what the viewport can show.

        Applied after every mutation rather than lazily at read time: a stored
        centre that is out of bounds but renders correctly is a trap, because
        the next zoom-out resolves it into a jump the user never asked for.
        """
        if self._center is None:
            return
        vw, vh = self._viewport
        sw, sh = self._source
        if sw <= 0 or sh <= 0 or vw <= 0 or vh <= 0:
            return
        scale = self.scale
        cx, cy = self._center
        self._center = (_clamp_axis(cx, vw, sw, scale),
                        _clamp_axis(cy, vh, sh, scale))


def _clamp_axis(center: float, viewport: int, source: int, scale: float) -> float:
    """Keep `center` such that the viewport stays over the image."""
    if source * scale <= viewport:
        return source / 2          # nothing to pan; sit in the middle
    half = viewport / 2 / scale
    return max(half, min(center, source - half))


def _clip(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))
