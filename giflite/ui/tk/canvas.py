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
        self.bind("<Configure>", self._on_configure)

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
        self.create_image(cx, cy, image=self._photo, anchor="center")
        # A crisp edge so the canvas bounds read even where the frame's own
        # pixels reach the border.
        self.create_rectangle(
            cx - fw // 2, cy - fh // 2, cx - fw // 2 + fw, cy - fh // 2 + fh,
            outline=CANVAS_BORDER, width=1,
        )
