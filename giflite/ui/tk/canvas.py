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
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

BACKGROUND = "#232326"      # the "pasteboard" outside the canvas
PLACEHOLDER_FG = "#8b8b93"
CHECKER_LIGHT = (58, 58, 64, 255)
CHECKER_DARK = (48, 48, 54, 255)
CHECKER_SQUARE = 8          # displayed pixels per square
CANVAS_BORDER = "#55555c"
CROP_MARQUEE = "#4a9eff"    # accent used for the rubber-band crop rectangle

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
        # in widget pixels -- the reference frame the crop gesture maps against.
        # None whenever no image is shown. Recomputed on every _redraw.
        self._image_geom: tuple[int, int, int, int] | None = None
        # Crop-mode (rubber-band) state; see begin_crop. Inert until then, so the
        # mouse bindings below no-op during normal viewing.
        self._crop_mode = False
        self._crop_on_commit: "Callable[[int, int, int, int], None] | None" = None
        self._crop_on_end: "Callable[[bool], None] | None" = None
        self._crop_start: tuple[int, int] | None = None
        self._crop_items: list[int] = []
        self.bind("<Configure>", self._on_configure)
        self.bind("<ButtonPress-1>", self._crop_press)
        self.bind("<B1-Motion>", self._crop_drag)
        self.bind("<ButtonRelease-1>", self._crop_release)
        self.bind("<Escape>", self._crop_escape)

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
        # A resize moves and rescales the image, so an in-progress crop box would
        # now map against stale geometry -- cancel it rather than crop wrongly.
        if self._crop_mode:
            self._end_crop(committed=False)
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
        # Remember exactly where the image landed; the crop gesture maps widget
        # coordinates back to image pixels through this.
        self._image_geom = (left, top, fw, fh)

    # ---- crop mode (rubber-band selection) -------------------------------
    #
    # The canvas analog of the timeline's drag-to-reorder (ARCHITECTURE.md 11.3):
    # it draws its own marquee locally and commits exactly one `canvas.crop` op
    # on release. No provisional state reaches the core -- the preview is just a
    # rectangle drawn on this canvas.

    @property
    def is_cropping(self) -> bool:
        return self._crop_mode

    def begin_crop(
        self,
        on_commit: Callable[[int, int, int, int], None],
        on_end: Callable[[bool], None],
    ) -> bool:
        """Enter crop mode. The next click-drag draws a rectangle that becomes
        an image-space crop box.

        Returns False if there's nothing to crop (no image shown). `on_commit(x,
        y, w, h)` fires with an image-pixel box on a valid drag; `on_end(
        committed)` fires exactly once when the mode exits either way, so the
        frontend can restore its cursor and status line.
        """
        if self._source is None or self._image_geom is None:
            return False
        self._clear_crop_items()
        self._crop_mode = True
        self._crop_on_commit = on_commit
        self._crop_on_end = on_end
        self._crop_start = None
        self.configure(cursor="crosshair")
        self.focus_set()  # so <Escape> reaches this widget before the global one
        return True

    def _end_crop(self, committed: bool) -> None:
        on_end = self._crop_on_end
        self._crop_mode = False
        self._crop_start = None
        self._crop_on_commit = None
        self._crop_on_end = None
        self._clear_crop_items()
        self.configure(cursor="")
        if on_end is not None:
            on_end(committed)

    def _clear_crop_items(self) -> None:
        for item in self._crop_items:
            self.delete(item)
        self._crop_items = []

    def _clamp_to_image(self, dx: float, dy: float) -> tuple[int, int]:
        """A widget point pinned inside the drawn image rectangle."""
        left, top, fw, fh = self._image_geom
        return (int(max(left, min(dx, left + fw))),
                int(max(top, min(dy, top + fh))))

    def _display_to_image(self, dx: float, dy: float) -> tuple[int, int]:
        """Map a widget point to image pixel coordinates (0..w, 0..h)."""
        left, top, fw, fh = self._image_geom
        src_w, src_h = self._source.size
        ix = round((dx - left) / fw * src_w)
        iy = round((dy - top) / fh * src_h)
        return (max(0, min(ix, src_w)), max(0, min(iy, src_h)))

    def _crop_press(self, event: tk.Event) -> None:
        if not self._crop_mode or self._image_geom is None:
            return
        self._crop_start = self._clamp_to_image(event.x, event.y)
        self._clear_crop_items()

    def _crop_drag(self, event: tk.Event) -> None:
        if not self._crop_mode or self._crop_start is None:
            return
        x0, y0 = self._crop_start
        x1, y1 = self._clamp_to_image(event.x, event.y)
        self._clear_crop_items()
        self._crop_items.append(self.create_rectangle(
            x0, y0, x1, y1, outline=CROP_MARQUEE, width=1, dash=(4, 3),
        ))
        ix0, iy0 = self._display_to_image(x0, y0)
        ix1, iy1 = self._display_to_image(x1, y1)
        self._crop_items.append(self.create_text(
            x1 + 5, y1 + 5, text=f"{abs(ix1 - ix0)}×{abs(iy1 - iy0)}",
            fill=CROP_MARQUEE, anchor="nw", font=("TkDefaultFont", 8),
        ))

    def _crop_release(self, event: tk.Event) -> None:
        if not self._crop_mode or self._crop_start is None:
            return
        ix0, iy0 = self._display_to_image(*self._crop_start)
        ix1, iy1 = self._display_to_image(event.x, event.y)
        left, right = sorted((ix0, ix1))
        top, bottom = sorted((iy0, iy1))
        width, height = right - left, bottom - top
        commit = self._crop_on_commit
        if width >= 1 and height >= 1 and commit is not None:
            commit(left, top, width, height)
            self._end_crop(committed=True)
        else:
            # A stray click or zero-area drag: cancel rather than crop nothing.
            self._end_crop(committed=False)

    def _crop_escape(self, _event: tk.Event | None = None) -> str | None:
        if not self._crop_mode:
            return None  # not our key -- let the global Esc (deselect) handle it
        self._end_crop(committed=False)
        return "break"
