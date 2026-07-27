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
don't re-run a resize of a big frame on every redraw. Manual zoom and pan are
still deferred; the fit calculation is factored out so they slot in later.
"""

from __future__ import annotations

import tkinter as tk
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageTk

BACKGROUND = "#232326"      # the "pasteboard" outside the canvas
PLACEHOLDER_FG = "#8b8b93"
CHECKER_LIGHT = (58, 58, 64, 255)
CHECKER_DARK = (48, 48, 54, 255)
CHECKER_SQUARE = 8          # displayed pixels per square
CANVAS_BORDER = "#55555c"
MARQUEE = "#4a9eff"         # accent for provisional overlays (crop box, erase)

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
        self.bind("<Configure>", self._on_configure)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", self._on_escape)

    # ---- public ----------------------------------------------------------

    def show(self, image: Image.Image, key: object = None) -> None:
        """Display `image`. `key` identifies the pixels for caching -- pass a
        frame's image_uid so the same frame at the same size isn't rebuilt."""
        self._source = image
        self._source_key = key if key is not None else id(image)
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

    # ---- internals -------------------------------------------------------

    def _on_configure(self, event: tk.Event) -> None:
        # <Configure> fires for moves as well as resizes; only a size change
        # means the fit has to be recomputed.
        size = (event.width, event.height)
        if size == self._last_size:
            return
        self._last_size = size
        # A resize moves and rescales the image, so coordinates a gesture has
        # already collected now map against stale geometry. Cancel rather than
        # commit something that lands in the wrong place. (Crop had this guard
        # from the start; folding painting into the same path gave strokes the
        # same protection, which they were previously missing.)
        if self._tool is not None and self._tool.is_gesturing:
            self._tool.on_cancel(self._tool_ctx)
        self._redraw()

    def _fit(self, image: Image.Image, width: int, height: int) -> Image.Image:
        pad = 16
        avail_w = max(width - pad, 1)
        avail_h = max(height - pad, 1)
        src_w, src_h = image.size
        scale = min(avail_w / src_w, avail_h / src_h)

        if abs(scale - 1.0) < 0.01:
            return image

        target = (max(int(src_w * scale), 1), max(int(src_h * scale), 1))
        # NEAREST when enlarging keeps pixel-art GIFs crisp instead of mushy;
        # LANCZOS when shrinking avoids the aliasing NEAREST would give.
        resample = Image.NEAREST if scale > 1 else Image.LANCZOS
        return image.resize(target, resample)

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

    def _compose(self, width: int, height: int) -> tuple[ImageTk.PhotoImage, tuple[int, int]]:
        cache_key = (self._source_key, width, height)
        cached = self._composed.get(cache_key)
        if cached is not None:
            self._composed.move_to_end(cache_key)
            return cached

        fitted = self._fit(self._source, width, height)
        if fitted.mode != "RGBA":
            fitted = fitted.convert("RGBA")
        board = self._checkerboard(*fitted.size).copy()
        board.alpha_composite(fitted)  # transparent frame pixels reveal the checker
        photo = ImageTk.PhotoImage(board)

        result = (photo, fitted.size)
        self._composed[cache_key] = result
        if len(self._composed) > _SCALED_CACHE_LIMIT:
            self._composed.popitem(last=False)
        return result

    def _redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            return  # not laid out yet; <Configure> will call us again

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

        self._photo, (fw, fh) = self._compose(width, height)
        cx, cy = width // 2, height // 2
        left, top = cx - fw // 2, cy - fh // 2
        self.create_image(cx, cy, image=self._photo, anchor="center")
        # A crisp edge so the canvas bounds read even where the frame's own
        # pixels reach the border.
        self.create_rectangle(
            left, top, left + fw, top + fh,
            outline=CANVAS_BORDER, width=1,
        )
        # Remember exactly where the image landed; tool gestures map widget
        # coordinates back to image pixels through this.
        self._image_geom = (left, top, fw, fh)

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
        handler(self._tool_ctx, *self._display_to_image(event.x, event.y))

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

    def _image_to_display(self, ix: float, iy: float) -> tuple[float, float]:
        """Inverse of _display_to_image: an image pixel -> a widget point."""
        left, top, fw, fh = self._image_geom
        src_w, src_h = self._source.size
        return (left + ix / src_w * fw, top + iy / src_h * fh)

    def _display_to_image(self, dx: float, dy: float) -> tuple[int, int]:
        """Map a widget point to image pixel coordinates, clamped to 0..w / 0..h
        so a drag that overshoots the edge pins to it instead of going negative."""
        left, top, fw, fh = self._image_geom
        src_w, src_h = self._source.size
        ix = round((dx - left) / fw * src_w)
        iy = round((dy - top) / fh * src_h)
        return (max(0, min(ix, src_w)), max(0, min(iy, src_h)))

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
        disp = [self._image_to_display(x, y) for x, y in points]
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
