"""Preview surface.

Owns zoom and pan entirely, along with the toolkit bitmap cache. The
controller hands over full-resolution pixels and takes no view of how they
are displayed (ARCHITECTURE.md 9).

M0 does fit-to-window only. Manual zoom and pan arrive at M1, which is why
the scale calculation is already factored out.
"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageTk

BACKGROUND = "#232326"
PLACEHOLDER_FG = "#8b8b93"


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
        self._placeholder = ""
        # Strong reference, and the reason this attribute exists at all: Tk
        # garbage-collects a PhotoImage the moment Python drops its last
        # reference, and the canvas then draws nothing at all. A blank window
        # with no error is the classic symptom (ARCHITECTURE.md risk 6).
        self._photo: ImageTk.PhotoImage | None = None
        self._last_size = (0, 0)
        self.bind("<Configure>", self._on_configure)

    # ---- public ----------------------------------------------------------

    def show(self, image: Image.Image) -> None:
        self._source = image
        self._redraw()

    def show_placeholder(self, text: str) -> None:
        self._source = None
        self._placeholder = text
        self._redraw()

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

        self._photo = ImageTk.PhotoImage(self._fit(self._source, width, height))
        self.create_image(width // 2, height // 2, image=self._photo, anchor="center")
