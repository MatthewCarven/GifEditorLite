"""Preview surface.

Owns zoom and pan entirely, along with the toolkit bitmap cache. The
controller hands over full-resolution pixels and takes no view of how they
are displayed (ARCHITECTURE.md 9).

M0 did fit-to-window. M1 adds a small scaled-frame cache so playback and
scrubbing don't re-run a LANCZOS/NEAREST resize of a big frame on every
redraw. Manual zoom and pan are still deferred; the fit calculation is
factored out so they slot in later.
"""

from __future__ import annotations

import tkinter as tk
from collections import OrderedDict

from PIL import Image, ImageTk

BACKGROUND = "#232326"
PLACEHOLDER_FG = "#8b8b93"

# How many scaled frames to keep. During playback each frame is drawn once at
# the current window size, so a GIF-sized cache lets a second viewing (or a
# scrub back and forth) skip the resize entirely. Bounded because a resize of
# a large frame is a real allocation.
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
        # (key, w, h) -> PhotoImage. Keyed by the caller's frame identity so a
        # redraw at the same size is a dict hit, not a resize.
        self._scaled: "OrderedDict[tuple, ImageTk.PhotoImage]" = OrderedDict()
        self._last_size = (0, 0)
        self.bind("<Configure>", self._on_configure)

    # ---- public ----------------------------------------------------------

    def show(self, image: Image.Image, key: object = None) -> None:
        """Display `image`. `key` identifies the pixels for caching -- pass a
        frame's image_uid so the same frame at the same size isn't re-scaled."""
        self._source = image
        self._source_key = key if key is not None else id(image)
        self._redraw()

    def show_placeholder(self, text: str) -> None:
        self._source = None
        self._source_key = None
        self._placeholder = text
        self._redraw()

    def invalidate(self) -> None:
        """Drop the scaled cache, e.g. when the document closes."""
        self._scaled.clear()

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

    def _photo_for(self, width: int, height: int) -> ImageTk.PhotoImage:
        cache_key = (self._source_key, width, height)
        cached = self._scaled.get(cache_key)
        if cached is not None:
            self._scaled.move_to_end(cache_key)
            return cached

        photo = ImageTk.PhotoImage(self._fit(self._source, width, height))
        self._scaled[cache_key] = photo
        if len(self._scaled) > _SCALED_CACHE_LIMIT:
            self._scaled.popitem(last=False)
        return photo

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

        self._photo = self._photo_for(width, height)
        self.create_image(width // 2, height // 2, image=self._photo, anchor="center")
