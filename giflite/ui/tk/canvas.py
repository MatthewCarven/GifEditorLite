"""Preview surface.

Owns zoom and pan entirely, along with the toolkit bitmap cache. The
controller hands over full-resolution pixels and takes no view of how they
are displayed (ARCHITECTURE.md 9).

Renders the frame over a checkerboard sized to the *fitted image*, with a thin
border around it. That backing is not decoration: a GIF frame can be mostly
transparent (a small sprite on a wide canvas), and without it the empty canvas
area is indistinguishable from the letterbox, so a correctly-scaled wide-but-
sparse GIF reads as a small square floating in the void. The checkerboard shows
exactly where the canvas is and which pixels are transparent -- the same reason
every image editor does it.

M0 did fit-to-window. M1 added a scaled-frame cache so playback and scrubbing
don't re-run a resize of a big frame on every redraw. Manual zoom and pan
arrived later and live in `view.py`: this module asks a `ViewTransform` where
the image goes and renders it, and owns no scale arithmetic of its own.

**Rendering is crop-then-scale, not scale-then-crop.** Composing the whole image
at 32x would be gigabytes of RGBA for a modest GIF; instead only the part inside
the viewport is cropped from the source and resampled, so cost is bounded by the
window rather than by the zoom. At fit the visible rectangle is the whole image,
which makes the fit path exactly what it was before zoom existed -- including
the cache keys, so playback still runs off cached bitmaps.
"""

from __future__ import annotations

import tkinter as tk
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageTk

from .tools import Mods, NO_MODS
from .view import ViewTransform

# Tk's event.state bits for the modifier keys, identical on Windows, X11 and
# macOS (unlike Alt, which is a different bit on each -- one reason `Mods`
# only carries what something uses).
_SHIFT_MASK = 0x0001


def _mods_from(event: tk.Event) -> Mods:
    """The `Mods` for a mouse event, from its toolkit state bitmask.

    This translation is the only place tools' modifier handling touches Tk,
    which is the same seam discipline as coordinates: tools see image pixels
    and `Mods`, never event.x or a state bit. `state` is an int for every
    mouse event; anything else (a handful of exotic X events say "??") means
    no modifier information, and no-modifiers is the honest reading of that.
    """
    state = getattr(event, "state", 0)
    if not isinstance(state, int):
        return NO_MODS
    return Mods(shift=bool(state & _SHIFT_MASK))

BACKGROUND = "#232326"      # the "pasteboard" outside the canvas
PLACEHOLDER_FG = "#8b8b93"
CHECKER_LIGHT = (58, 58, 64, 255)
CHECKER_DARK = (48, 48, 54, 255)
CHECKER_SQUARE = 8          # displayed pixels per square
CANVAS_BORDER = "#55555c"
MARQUEE = "#4a9eff"         # accent for provisional overlays (crop box, erase)
# The committed region marquee. Two offset dashed rectangles, dark under light,
# which is the "marching ants" look standing still -- the alternation is what
# makes the outline legible over both a dark sprite and a light one, and the
# animation was never the part doing that work. A different colour from
# MARQUEE on purpose: a rectangle you are still dragging and a rectangle you
# have already committed are different things, and a preview that looked
# identical to a selection would leave you unable to tell whether releasing the
# mouse had done anything.
ANTS_DARK = "#1b1b1e"
ANTS_LIGHT = "#f2f2f5"
ANTS_DASH = (4, 4)
# The pixel grid. Stippled rather than solid: Tk canvas items have no alpha, and
# a 50% dither is the only way to get a rule that reads as a guide over both a
# dark sprite and a light one without picking a colour that vanishes into one of
# them. A mid grey at full strength was legible over the artwork and *louder*
# than the artwork, which is the wrong way round for a guide.
GRID_COLOR = "#c8c8d0"
GRID_STIPPLE = "gray50"

# How many composed frames to keep. During playback each frame is drawn once at
# the current window size, so a GIF-sized cache lets a second viewing (or a
# scrub back and forth) skip the resize+compose entirely. Bounded because a
# resize of a large frame is a real allocation.
_SCALED_CACHE_LIMIT = 240


class PreviewCanvas(tk.Canvas):
    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(
            master,
            background=BACKGROUND,
            highlightthickness=0,
            borderwidth=0,
            **kwargs,
        )
        self._source: Image.Image | None = None
        self._source_key: object = None
        self._placeholder = ""
        # Scale and pan. Pure arithmetic, no toolkit, tested headlessly in
        # tests/test_view.py -- this widget asks it where the image goes and
        # does no scale arithmetic of its own.
        self.view = ViewTransform()
        # Strong reference, and the reason this attribute exists at all: Tk
        # garbage-collects a PhotoImage the moment Python drops its last
        # reference, and the canvas then draws nothing. A blank window with no
        # error is the classic symptom (ARCHITECTURE.md risk 6).
        self._photo: ImageTk.PhotoImage | None = None
        # (key, w, h) -> (PhotoImage, fitted_size). Keyed by the caller's frame
        # identity so a redraw at the same size is a dict hit, not a rebuild.
        self._composed: "OrderedDict[tuple, tuple]" = OrderedDict()
        # Checkerboard tiles are shared across frames of the same fitted size.
        self._boards: dict[tuple[int, int], Image.Image] = {}
        self._last_size = (0, 0)
        # Where the fitted image currently sits on the canvas, (left, top, w, h)
        # in widget pixels -- the reference frame every tool gesture maps
        # against. None whenever no image is shown. Recomputed on every _redraw.
        self._image_geom: tuple[int, int, int, int] | None = None
        # The active tool (a ui.tk.tools.Tool) and its context, or None for plain
        # viewing. This is the *only* mouse path on the canvas: crop, pencil,
        # eraser and eyedropper all arrive as tools, so there is one dispatch to
        # reason about rather than a mode flag racing a tool.
        self._tool = None
        self._tool_ctx = None
        self._overlay_items: list[int] = []
        # The committed region, (x0, y0, x1, y1) in image pixels, or None.
        #
        # **The first thing on this canvas that a redraw must rebuild rather
        # than discard.** Every overlay until now belonged to a gesture, so
        # `_draw`'s `delete("all")` taking them was correct and the tool
        # redrew on the next mouse event. A region has no next mouse event: it
        # persists while you scrub, play, zoom and paint, so it is drawn from
        # state inside `_draw` alongside the grid, and is deliberately *not* in
        # `_overlay_items` -- `clear_overlay()` must not touch it.
        self._region: tuple[int, int, int, int] | None = None
        # Called after every redraw so a frontend can keep zoom controls in step.
        # A plain attribute rather than an event: this is one widget telling its
        # own window something, not application state, and the controller's bus
        # is for things a *second frontend* would also need to hear (§9). A
        # resize re-fits without anyone pressing anything, which is exactly the
        # case a command-driven refresh would miss.
        self.on_view_change = None
        self.bind("<Configure>", self._on_configure)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", self._on_escape)
        # Ctrl+wheel zooms at the pointer. The *bare* wheel stays unbound on
        # purpose -- §20.5's "no view gesture on the preview" was about the
        # wheel the tools might one day want and about a stroke being disturbed
        # mid-gesture; the Ctrl chord leaves the first untouched, and every
        # zoom goes through `_apply_view`, whose §20.4 gesture-cancel is what
        # has made view changes safe mid-stroke since Ctrl+`-` could fire
        # there. Windows and macOS deliver wheels as <MouseWheel> with a
        # signed delta; X11 delivers buttons 4 and 5. Binding all three is the
        # portable spelling of one gesture.
        self.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.bind("<Control-Button-4>", lambda e: self._on_ctrl_wheel(e, 1))
        self.bind("<Control-Button-5>", lambda e: self._on_ctrl_wheel(e, -1))

    # ---- public ----------------------------------------------------------

    def show(self, image: Image.Image, key: object = None) -> None:
        """Display `image`. `key` identifies the pixels for caching -- pass a
        frame's image_uid so the same frame at the same size isn't rebuilt."""
        self._source = image
        self._source_key = key if key is not None else id(image)
        # Tell the transform how big the image is. It deliberately keeps the
        # current zoom and only re-clamps the pan, so a crop or a canvas resize
        # leaves you looking at the same magnification -- you cropped in order
        # to look closely, and being thrown back to fit at that moment is the
        # wrong answer.
        self.view.set_source(*image.size)
        self._redraw()

    def show_placeholder(self, text: str) -> None:
        self._source = None
        self._source_key = None
        self._placeholder = text
        self._redraw()

    def invalidate(self) -> None:
        """Drop cached bitmaps, e.g. when the document changes."""
        self._composed.clear()
        self._boards.clear()

    def reset_view(self) -> None:
        """Back to fit, centred. For a genuinely new document only -- the same
        distinction the timeline makes with `reset_view` on open/close."""
        self.view.reset()
        self._redraw()

    # ---- zoom and pan ----------------------------------------------------
    #
    # Each of these is "change the transform, then redraw through the one path
    # that knows a view change invalidates a gesture in progress". They return
    # whether anything moved, which is how a frontend keeps a control's enabled
    # state honest without tracking the view itself.

    def zoom_in(self) -> bool:
        return self._apply_view(self.view.zoom_in())

    def zoom_out(self) -> bool:
        return self._apply_view(self.view.zoom_out())

    def zoom_in_at(self, dx: float, dy: float) -> bool:
        """Zoom one rung in, anchored at display point (dx, dy) -- the wheel's
        entry. Same funnel as every other view change."""
        return self._apply_view(self.view.zoom_in_at(dx, dy))

    def zoom_out_at(self, dx: float, dy: float) -> bool:
        return self._apply_view(self.view.zoom_out_at(dx, dy))

    def zoom_fit(self) -> bool:
        self.view.fit()
        return self._apply_view(True)

    def zoom_actual(self) -> bool:
        self.view.actual_size()
        return self._apply_view(True)

    def pan(self, dx: float, dy: float) -> bool:
        return self._apply_view(self.view.nudge(dx, dy))

    # The grid changes nothing about where the image sits, so cancelling a
    # gesture for it looks like overkill. It isn't: `_draw` starts with
    # `delete("all")`, which takes the overlay items with it while
    # `_overlay_items` goes on holding their ids -- a gesture that survives a
    # redraw is a gesture whose preview has silently disappeared. Same funnel,
    # same guarantee.

    def set_grid_mode(self, mode: str) -> bool:
        return self._apply_view(self.view.set_grid_mode(mode))

    def cycle_grid_mode(self) -> str:
        mode = self.view.cycle_grid_mode()
        self._apply_view(True)
        return mode

    def center_view_on(self, ix: float, iy: float) -> bool:
        """Centre on an image coordinate. What the navigator drags against."""
        return self._apply_view(self.view.center_on(ix, iy))

    def _apply_view(self, changed: bool) -> bool:
        """The single funnel for every view change.

        A zoom or a pan moves and rescales the image, which is *exactly* the
        staleness a window resize causes: coordinates a gesture has already
        collected now map somewhere else. `<Configure>` has cancelled gestures
        for that reason since crop existed; routing view changes through the
        same guard closes the hole rather than rediscovering it. Reachable
        today via the keyboard shortcuts, which fire happily mid-stroke.
        """
        if not changed:
            return False
        if self._tool is not None and self._tool.is_gesturing:
            self._tool.on_cancel(self._tool_ctx)
        self._redraw()
        return True

    # ---- internals -------------------------------------------------------

    def _on_configure(self, event: tk.Event) -> None:
        # <Configure> fires for moves as well as resizes; only a size change
        # means the fit has to be recomputed.
        size = (event.width, event.height)
        if size == self._last_size:
            return
        self._last_size = size
        self.view.set_viewport(*size)
        # A resize moves and rescales the image, so coordinates a gesture has
        # already collected now map against stale geometry. Cancel rather than
        # commit something that lands in the wrong place. (Crop had this guard
        # from the start; folding painting into the same path gave strokes the
        # same protection, which they were previously missing.)
        if self._tool is not None and self._tool.is_gesturing:
            self._tool.on_cancel(self._tool_ctx)
        self._redraw()

    def _checkerboard(self, width: int, height: int) -> Image.Image:
        cached = self._boards.get((width, height))
        if cached is not None:
            return cached
        sq = CHECKER_SQUARE
        # Build one 2x2-square tile, then stamp it across -- far fewer paste
        # calls than one per square.
        tile = Image.new("RGBA", (sq * 2, sq * 2), CHECKER_LIGHT)
        draw = ImageDraw.Draw(tile)
        draw.rectangle([0, 0, sq - 1, sq - 1], fill=CHECKER_DARK)
        draw.rectangle([sq, sq, sq * 2 - 1, sq * 2 - 1], fill=CHECKER_DARK)
        board = Image.new("RGBA", (width, height), CHECKER_LIGHT)
        for y in range(0, height, sq * 2):
            for x in range(0, width, sq * 2):
                board.paste(tile, (x, y))
        self._boards[(width, height)] = board
        return board

    def _backing(self, size: tuple[int, int], phase: tuple[int, int]) -> Image.Image:
        """A checkerboard of `size` whose pattern is offset by `phase`.

        The phase exists so the checker stays locked to the *image* rather than
        to whatever sub-rectangle happens to be on screen. Without it the
        pattern shifts underneath a transparent GIF every time you pan, which
        reads as the artwork moving rather than the view -- and with 25%-of-a-
        viewport button steps it is a jump, not a drift. One oversized board,
        cached, cropped to phase; the crop replaces the copy the composite
        needed anyway, so it costs nothing extra.
        """
        sq = CHECKER_SQUARE * 2
        px, py = phase[0] % sq, phase[1] % sq
        board = self._checkerboard(size[0] + sq, size[1] + sq)
        return board.crop((px, py, px + size[0], py + size[1]))

    def _compose(self, rect: tuple[int, int, int, int],
                 out_size: tuple[int, int],
                 phase: tuple[int, int]) -> ImageTk.PhotoImage:
        """Crop `rect` out of the source, scale it to `out_size`, put it on a
        checkerboard. `rect` is in source pixels and `out_size` in display
        pixels; at fit they are the whole image and its fitted size, which is
        precisely what this method used to be handed.
        """
        cache_key = (self._source_key, rect, out_size, phase)
        cached = self._composed.get(cache_key)
        if cached is not None:
            self._composed.move_to_end(cache_key)
            return cached

        region = self._source
        if rect != (0, 0, *region.size):
            region = region.crop(rect)
        if region.size != out_size:
            # NEAREST when enlarging keeps pixel-art GIFs crisp instead of
            # mushy; LANCZOS when shrinking avoids the aliasing NEAREST would
            # give. Frames are RGBA by model invariant, so this can't hit the
            # palette-interpolation trap a P-mode LANCZOS resize would.
            resample = Image.NEAREST if out_size[0] >= region.size[0] else Image.LANCZOS
            region = region.resize(out_size, resample)
        if region.mode != "RGBA":
            region = region.convert("RGBA")
        board = self._backing(out_size, phase)
        board.alpha_composite(region)  # transparent frame pixels reveal the checker
        photo = ImageTk.PhotoImage(board)

        self._composed[cache_key] = photo
        if len(self._composed) > _SCALED_CACHE_LIMIT:
            self._composed.popitem(last=False)
        return photo

    def _redraw(self) -> None:
        self._draw()
        if self.on_view_change is not None:
            self.on_view_change()

    def _draw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            return  # not laid out yet; <Configure> will call us again

        # This is a viewport, not a scrollable surface. A tk.Canvas with no
        # scrollregion happily scrolls itself over the bounding box of its items
        # -- a stray mouse wheel or arrow key is enough -- and that silently
        # breaks every gesture, because widget coordinates then no longer equal
        # canvas coordinates. Pinning the region to the visible area makes such a
        # scroll a no-op, and moving the view back recovers from one that already
        # happened. Panning, when it arrives, will be an explicit transform, not
        # an accidental one. (_dispatch converts coordinates properly regardless;
        # this keeps the *view* from drifting in the first place.)
        self.configure(scrollregion=(0, 0, width, height))
        if self.canvasx(0) or self.canvasy(0):
            self.xview_moveto(0)
            self.yview_moveto(0)

        if self._source is None:
            self._photo = None
            self._image_geom = None
            self.create_text(
                width // 2,
                height // 2,
                text=self._placeholder,
                fill=PLACEHOLDER_FG,
                font=("TkDefaultFont", 11),
                justify="center",
            )
            return

        self.view.set_viewport(width, height)
        left, top, fw, fh = self.view.geometry()
        src_w, src_h = self._source.size
        scale_x, scale_y = fw / src_w, fh / src_h

        # Only the visible part gets composed. Zoomed in, that is a
        # viewport-sized bitmap however deep the zoom; at fit it is the whole
        # image, so nothing about the pre-zoom path changes.
        x0, y0, x1, y1 = rect = self.view.visible_source_rect()
        out_w = max(int(round((x1 - x0) * scale_x)), 1)
        out_h = max(int(round((y1 - y0) * scale_y)), 1)
        # The crop is on whole source pixels, so the fraction of a pixel between
        # the image origin and the crop origin has to be carried by *placement*.
        # Folding it into the resample instead is what makes upscaled pixel art
        # shimmer as it moves.
        px = int(round(left + x0 * scale_x))
        py = int(round(top + y0 * scale_y))
        self._photo = self._compose(rect, (out_w, out_h), (px - left, py - top))
        self.create_image(px, py, image=self._photo, anchor="nw")
        self._draw_grid()
        self._draw_region()
        # A crisp edge so the canvas bounds read even where the frame's own
        # pixels reach the border. Drawn at the *whole* image's bounds, which
        # when zoomed in are mostly off-screen; Tk clips it for free.
        self.create_rectangle(
            left, top, left + fw, top + fh,
            outline=CANVAS_BORDER, width=1,
        )
        # Remember exactly where the image landed; tool gestures map widget
        # coordinates back to image pixels through this. Note it describes the
        # entire image, not the visible slice -- a stroke that runs off the edge
        # has to keep making sense.
        self._image_geom = (left, top, fw, fh)

    def _draw_grid(self) -> None:
        """Rule every source-pixel boundary on screen.

        Canvas items, not pixels baked into the composed bitmap. Three reasons,
        in order of how much they would have cost to learn the hard way:

        1. The bitmap cache stays pure. Toggling the grid invalidates no frame,
           and nothing that reads pixels can ever read a grid line -- the
           eyedropper samples the source, but the invariant is worth having
           unconditionally rather than per-caller.
        2. The rules come from `image_to_display`, the same mapping every tool
           uses. Baking them would mean deriving their positions a second way,
           from the crop rectangle and the resample, and 19.1 is the record of
           what two derivations of the same coordinate cost.
        3. The count is bounded by the viewport, not the image: `grid_lines`
           walks the *visible* source rect, so 32x on a 2000px image is the
           same few dozen items as 32x on a 40px one.

        `_apply_view` -> `_redraw` is the only way the grid changes, so there is
        no separate invalidation path to get wrong.
        """
        lines = self.view.grid_lines()
        if lines is None:
            return
        for x in lines.xs:
            self.create_line(x, lines.top, x, lines.bottom,
                             fill=GRID_COLOR, stipple=GRID_STIPPLE, width=1)
        for y in lines.ys:
            self.create_line(lines.left, y, lines.right, y,
                             fill=GRID_COLOR, stipple=GRID_STIPPLE, width=1)

    def set_region(self, region: tuple[int, int, int, int] | None) -> None:
        """Show a committed selection marquee, `(x, y, w, h)` in image pixels.

        A plain setter plus a redraw, rather than creating canvas items here:
        the region has to be redrawn on every `_draw` anyway (zoom, pan, scrub,
        window resize all move it), so having one drawing path means a marquee
        cannot be correct at the moment it is set and wrong a frame later.
        """
        box = None if region is None else (
            int(region[0]), int(region[1]),
            int(region[0]) + int(region[2]), int(region[1]) + int(region[3]),
        )
        if box == self._region:
            return
        self._region = box
        self._draw()  # not _redraw: nothing about the *view* changed

    def _draw_region(self) -> None:
        """Rule the committed region, in the same mapping every tool uses.

        Edge coordinates map straight through `image_to_display(center=False)`,
        which returns a pixel's top-left corner -- exactly right here, and the
        reason `SelectTool` declares `coords = "edge"`. This is the one overlay
        that needs no `preview_box`-style adjustment (ARCHITECTURE.md 23.3).
        """
        if self._region is None or self._image_geom is None or self._source is None:
            return
        x0, y0, x1, y1 = self._region
        dx0, dy0 = self._image_to_display(x0, y0)
        dx1, dy1 = self._image_to_display(x1, y1)
        self.create_rectangle(dx0, dy0, dx1, dy1, outline=ANTS_DARK, width=1)
        self.create_rectangle(dx0, dy0, dx1, dy1, outline=ANTS_LIGHT, width=1,
                              dash=ANTS_DASH)

    # ---- mouse dispatch: one path, straight to the active tool ------------
    #
    # Every gesture on the preview is a tool (ARCHITECTURE.md 19), crop included,
    # so there is exactly one place mouse events are routed and exactly one
    # coordinate mapping. Tools receive image pixels and never see a widget
    # coordinate; the canvas never learns what a tool does with them.

    def _dispatch(self, handler_name: str, event: tk.Event) -> None:
        if self._tool is None or self._image_geom is None or self._source is None:
            return
        handler = getattr(self._tool, handler_name)
        # canvasx/canvasy, not event.x/event.y: a mouse event carries *widget*
        # coordinates while items (and so `_image_geom`) live in *canvas*
        # coordinates, and the two differ by however far the view has scrolled.
        # They coincide while the view sits at the origin -- which _redraw now
        # enforces -- so this is belt and braces rather than the live fix.
        #
        # `snap` is the tool's call: a brush wants the pixel under the cursor, a
        # crop box wants the nearest pixel boundary. See _display_to_image.
        handler(self._tool_ctx,
                *self._display_to_image(self.canvasx(event.x),
                                        self.canvasy(event.y),
                                        snap=getattr(self._tool, "coords", "pixel")),
                _mods_from(event))

    def _on_ctrl_wheel(self, event: tk.Event, direction: int | None = None) -> str:
        """One wheel notch = one ladder rung, at the pointer.

        `direction` is passed by the X11 button bindings (4 up, 5 down); the
        <MouseWheel> path reads the sign of `delta` instead. The magnitude is
        ignored on purpose: high-resolution wheels report many small deltas
        per detent, and stepping a rung per *event* rather than per notch
        would fly through the whole ladder in one flick.
        """
        if direction is None:
            direction = 1 if getattr(event, "delta", 0) > 0 else -1
        x, y = self.canvasx(event.x), self.canvasy(event.y)
        if direction > 0:
            self.zoom_in_at(x, y)
        else:
            self.zoom_out_at(x, y)
        return "break"

    def _on_press(self, event: tk.Event) -> None:
        self._dispatch("on_press", event)

    def _on_drag(self, event: tk.Event) -> None:
        self._dispatch("on_drag", event)

    def _on_release(self, event: tk.Event) -> None:
        self._dispatch("on_release", event)

    def _on_escape(self, _event: tk.Event | None = None) -> "str | None":
        """Two-stage Esc, and only ours while a tool is active.

        Mid-gesture it abandons the gesture but keeps the tool (you meant to
        redraw the box, not to leave crop). Otherwise it puts the tool away.
        With no tool it returns None so the global Esc still deselects frames --
        this widget's bindtag runs before `bind_all`, so returning "break"
        unconditionally would swallow that.
        """
        if self._tool is None:
            return None
        if self._tool.is_gesturing:
            self._tool.on_cancel(self._tool_ctx)
        elif self._tool_ctx is not None:
            self._tool_ctx.end_tool()
        return "break"

    # ---- active tool -----------------------------------------------------

    def set_tool(self, tool, ctx) -> None:
        """Make `tool` the active tool (its `cursor` shows over the image).
        Pass tool=None to clear back to plain viewing."""
        if self._tool is not None and self._tool.is_gesturing:
            self._tool.on_cancel(self._tool_ctx)  # don't leave a gesture dangling
        self.clear_overlay()
        self._tool = tool
        self._tool_ctx = ctx
        self.configure(cursor=(tool.cursor if tool is not None else ""))
        if tool is not None:
            self.focus_set()  # so <Escape> reaches this widget before the global one

    def clear_tool(self) -> None:
        self.set_tool(None, None)

    @property
    def has_tool(self) -> bool:
        return self._tool is not None

    @property
    def active_tool(self):
        return self._tool

    # ---- provisional overlays --------------------------------------------
    #
    # A tool renders its gesture locally and the real pixels land on commit (the
    # gesture rule, ARCHITECTURE.md 11.3 / 19). Overlays are plain canvas items,
    # so no provisional state ever reaches the core.

    # The mapping itself lives on the transform (view.py): it is pure arithmetic
    # over `geometry()` and the source size, the navigator needs the identical
    # logic against a thumbnail's geometry, and duplicating it is precisely how
    # the two half-pixel bugs in ARCHITECTURE 19.1 would come back on one side
    # only. These remain as the canvas's names for it -- the tool dispatch and
    # the overlay code read better for them.

    def _image_to_display(self, ix: float, iy: float,
                          center: bool = False) -> tuple[float, float]:
        return self.view.image_to_display(ix, iy, center=center)

    def _display_to_image(self, dx: float, dy: float,
                          snap: str = "pixel") -> tuple[int, int]:
        return self.view.display_to_image(dx, dy, snap=snap)

    def clear_overlay(self) -> None:
        for item in self._overlay_items:
            self.delete(item)
        self._overlay_items = []

    def show_stroke_overlay(self, points, color: str, size: int, erase: bool) -> None:
        """Draw the in-progress stroke as scaled canvas items. `color` is a Tk
        colour string; `size` is the brush diameter in image pixels."""
        self.clear_overlay()
        if self._image_geom is None or self._source is None or not points:
            return
        _, _, fw, _ = self._image_geom
        width = max(1, int(round(size * fw / self._source.size[0])))
        # center=True: the preview must sit *on* the pixels the brush will paint,
        # not on their top-left corners.
        disp = [self._image_to_display(x, y, center=True) for x, y in points]
        if len(disp) == 1:
            x, y = disp[0]
            r = max(1, width // 2)
            self._overlay_items.append(self.create_oval(
                x - r, y - r, x + r, y + r,
                outline=(MARQUEE if erase else color),
                fill=("" if erase else color), width=1,
            ))
        else:
            flat = [c for pt in disp for c in pt]
            if erase:
                self._overlay_items.append(self.create_line(
                    *flat, fill=MARQUEE, width=width, dash=(3, 2),
                    capstyle="round", joinstyle="round"))
            else:
                self._overlay_items.append(self.create_line(
                    *flat, fill=color, width=width,
                    capstyle="round", joinstyle="round"))

    def show_rect_overlay(self, box: tuple[int, int, int, int]) -> None:
        """Draw a dashed marquee for `box` = (x0, y0, x1, y1) in *image* pixels,
        with a live size label. Used by the crop gesture; any future rectangular
        tool (rect select, shapes) gets it for free."""
        self.clear_overlay()
        if self._image_geom is None or self._source is None:
            return
        x0, y0, x1, y1 = box
        dx0, dy0 = self._image_to_display(x0, y0)
        dx1, dy1 = self._image_to_display(x1, y1)
        self._overlay_items.append(self.create_rectangle(
            dx0, dy0, dx1, dy1, outline=MARQUEE, width=1, dash=(4, 3),
        ))
        # Label in image pixels -- the number that matters is the crop size, not
        # however many screen pixels it happens to occupy at this zoom.
        self._overlay_items.append(self.create_text(
            dx1 + 5, dy1 + 5, text=f"{abs(x1 - x0)}×{abs(y1 - y0)}",
            fill=MARQUEE, anchor="nw", font=("TkDefaultFont", 8),
        ))
