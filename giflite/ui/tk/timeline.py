"""Timeline: a horizontal thumbnail strip on a single Canvas.

Virtualised -- only the thumbnails currently in view get canvas items, so a
200-frame GIF costs the same to draw as a 20-frame one (ARCHITECTURE.md risk
4). It is emphatically not 200 Label widgets.

A dumb view: the app pushes document, index and selection in, and a click
calls back out with the picked index. It holds no playback or document state
of its own -- that all lives behind the controller.

Cache split (ARCHITECTURE.md risk 6): PIL thumbnails come from the app-layer
ThumbnailCache; the PhotoImages built from them are held here, in the toolkit
layer, keyed by the same frame uid so scrolling reuses them.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from PIL import ImageTk

from giflite.app.cache import ThumbnailCache
from giflite.core.model import Document, Selection

BACKGROUND = "#1b1b1e"
SLOT_BG = "#2c2c31"
NUMBER_FG = "#8b8b93"
CURRENT_BORDER = "#4a9eff"
SELECTED_BG = "#3a3a44"

THUMB_H = 56
GAP = 6
VPAD = 8
NUMBER_H = 14


class Timeline(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        cache: ThumbnailCache,
        on_pick: Callable[[int], None],
    ) -> None:
        super().__init__(master, background=BACKGROUND)
        self._cache = cache
        self._on_pick = on_pick

        self._doc: Document | None = None
        self._index = 0
        self._selection: frozenset[int] = frozenset()
        self._photos: dict[int, ImageTk.PhotoImage] = {}
        self._thumb_w = 0
        self._slot_w = 0

        row_h = NUMBER_H + THUMB_H + 2 * VPAD
        self.canvas = tk.Canvas(
            self, height=row_h, background=BACKGROUND, highlightthickness=0
        )
        self.scroll = ttk.Scrollbar(
            self, orient="horizontal", command=self.canvas.xview
        )
        self.canvas.configure(xscrollcommand=self._on_xscroll)
        self.canvas.pack(side="top", fill="x")
        self.scroll.pack(side="bottom", fill="x")

        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self.canvas.bind("<Button-1>", self._on_click)
        # Horizontal strip, so both plain and shifted wheel scroll sideways.
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda _e: self._scroll_units(-3))
        self.canvas.bind("<Button-5>", lambda _e: self._scroll_units(3))

    # ---- state in --------------------------------------------------------

    def set_document(self, doc: Document | None) -> None:
        self._doc = doc
        self._photos.clear()
        if doc is not None:
            self._thumb_w = self._cache.thumb_width(doc.size)
            self._slot_w = self._thumb_w + GAP
        else:
            self._thumb_w = self._slot_w = 0
        self.canvas.xview_moveto(0.0)
        self._update_scrollregion()
        self._redraw()

    def set_index(self, index: int) -> None:
        self._index = index
        self._ensure_visible(index)
        self._redraw()

    def set_selection(self, selection: Selection) -> None:
        self._selection = selection.indices
        self._redraw()

    # ---- geometry --------------------------------------------------------

    @property
    def _count(self) -> int:
        return len(self._doc) if self._doc else 0

    @property
    def _total_w(self) -> int:
        return self._count * self._slot_w + GAP

    def _update_scrollregion(self) -> None:
        self.canvas.configure(scrollregion=(0, 0, max(self._total_w, 1), 1))

    def _visible_indices(self) -> range:
        width = self.canvas.winfo_width()
        if width <= 1 or self._slot_w == 0:
            return range(0)
        left = self.canvas.canvasx(0)
        right = self.canvas.canvasx(width)
        # One slot of margin each side so a partially-scrolled thumbnail is
        # drawn rather than popping in at the edge.
        first = max(0, int(left // self._slot_w) - 1)
        last = min(self._count - 1, int(right // self._slot_w) + 1)
        return range(first, last + 1)

    # ---- drawing ---------------------------------------------------------

    def _redraw(self) -> None:
        self.canvas.delete("all")
        if self._doc is None:
            return
        top = VPAD + NUMBER_H
        for i in self._visible_indices():
            x = GAP + i * self._slot_w
            self._draw_slot(i, x, top)

    def _draw_slot(self, i: int, x: int, top: int) -> None:
        is_current = i == self._index
        is_selected = i in self._selection

        if is_selected:
            self.canvas.create_rectangle(
                x - 2, top - 2, x + self._thumb_w + 2, top + THUMB_H + 2,
                fill=SELECTED_BG, outline="",
            )

        photo = self._photo_for(i)
        self.canvas.create_image(x, top, image=photo, anchor="nw")

        if is_current:
            self.canvas.create_rectangle(
                x - 1, top - 1, x + self._thumb_w + 1, top + THUMB_H + 1,
                outline=CURRENT_BORDER, width=3,
            )

        self.canvas.create_text(
            x + self._thumb_w // 2,
            VPAD + NUMBER_H // 2,
            text=str(i + 1),
            fill=CURRENT_BORDER if is_current else NUMBER_FG,
            font=("TkDefaultFont", 8),
        )

    def _photo_for(self, index: int) -> ImageTk.PhotoImage:
        frame = self._doc[index]
        photo = self._photos.get(frame.image_uid)
        if photo is None:
            photo = ImageTk.PhotoImage(self._cache.get(frame))
            self._photos[frame.image_uid] = photo
        return photo

    # ---- interaction -----------------------------------------------------

    def _on_click(self, event: tk.Event) -> None:
        if self._doc is None or self._slot_w == 0:
            return
        x = self.canvas.canvasx(event.x)
        index = int((x - GAP) // self._slot_w)
        if 0 <= index < self._count:
            self._on_pick(index)

    def _on_xscroll(self, lo: str, hi: str) -> None:
        self.scroll.set(lo, hi)
        self._redraw()  # view moved -> different thumbnails are now visible

    def _on_wheel(self, event: tk.Event) -> None:
        self._scroll_units(-1 if event.delta > 0 else 1)

    def _scroll_units(self, amount: int) -> None:
        self.canvas.xview_scroll(amount, "units")

    def _ensure_visible(self, index: int) -> None:
        width = self.canvas.winfo_width()
        if width <= 1 or self._total_w <= width:
            return
        x0 = GAP + index * self._slot_w
        x1 = x0 + self._slot_w
        view_left = self.canvas.canvasx(0)
        view_right = view_left + width
        if x0 < view_left:
            self.canvas.xview_moveto(x0 / self._total_w)
        elif x1 > view_right:
            self.canvas.xview_moveto((x1 - width) / self._total_w)
