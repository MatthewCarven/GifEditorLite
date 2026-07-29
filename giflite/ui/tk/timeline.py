"""Timeline: a horizontal thumbnail strip on a single Canvas.

Virtualised -- only the thumbnails currently in view get canvas items, so a
200-frame GIF costs the same to draw as a 20-frame one (ARCHITECTURE.md risk
4). It is emphatically not 200 Label widgets.

A dumb view: the app pushes document, index and selection in, and gestures call
back out. It holds no playback or document state of its own -- that lives
behind the controller.

The gesture rule (ARCHITECTURE.md 11.3): drag-to-reorder renders its own
insertion marker locally and commits exactly one `move` op on release. There is
no provisional-transaction plumbing in the core; the preview is a line drawn on
this canvas and nothing more.

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
SELECTED_BG = "#3a4a63"
SELECTED_OUTLINE = "#4a9eff"
NUMBER_FG = "#8b8b93"
DELAY_FG = "#7a7a82"     # dimmer than the frame number: it's reference, not identity
CURRENT_BORDER = "#4a9eff"
DROP_MARKER = "#f0c040"

THUMB_H = 56
GAP = 6
VPAD = 8
NUMBER_H = 14
# Room under each thumbnail for its delay. Uneven timing is invisible until you
# can compare frames side by side, and comparing is the whole reason you go
# looking -- a per-frame number in a dialog tells you about one frame at a time.
DELAY_H = 13
DRAG_THRESHOLD = 5  # px before a press becomes a drag rather than a click


def _format_delay(ms: int) -> str:
    """Compact enough to sit under a thumbnail.

    Milliseconds below a second, seconds above it: "80" and "1.5s" both read at
    a glance, where "1500" next to "80" invites reading the strip as if every
    number were the same magnitude. The unit is only shown where it changes, so
    the common case stays two or three characters wide.
    """
    if ms < 1000:
        return f"{ms}"
    return f"{ms / 1000:g}s"


class Timeline(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        cache: ThumbnailCache,
        on_pick: Callable[[int], None],
        on_extend: Callable[[int], None],
        on_toggle: Callable[[int], None],
        on_reorder: Callable[[int], None],
    ) -> None:
        super().__init__(master, background=BACKGROUND)
        self._cache = cache
        self._on_pick = on_pick
        self._on_extend = on_extend
        self._on_toggle = on_toggle
        self._on_reorder = on_reorder

        self._doc: Document | None = None
        self._index = 0
        self._selection: frozenset[int] = frozenset()
        self._photos: dict[int, ImageTk.PhotoImage] = {}
        self._thumb_w = 0
        self._slot_w = 0

        # drag state
        self._press_index: int | None = None
        self._press_x = 0.0
        self._dragging = False
        self._drop_gap: int | None = None
        self._pending_collapse: int | None = None

        row_h = NUMBER_H + THUMB_H + DELAY_H + 2 * VPAD
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
        # Plain vs modified clicks dispatch to the most specific binding, so
        # <Button-1> only fires with no modifier held.
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<Shift-Button-1>", self._on_shift_press)
        self.canvas.bind("<Control-Button-1>", self._on_ctrl_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda _e: self._scroll_units(-3))
        self.canvas.bind("<Button-5>", lambda _e: self._scroll_units(3))

    # ---- state in --------------------------------------------------------

    def set_document(self, doc: Document | None, reset_view: bool = True) -> None:
        self._doc = doc
        self._photos.clear()
        if doc is not None:
            self._thumb_w = self._cache.thumb_width(doc.size)
            self._slot_w = self._thumb_w + GAP
        else:
            self._thumb_w = self._slot_w = 0
        self._update_scrollregion()
        if reset_view:
            # A fresh document starts at the beginning; an in-place edit keeps
            # the user where they were scrolled to.
            self.canvas.xview_moveto(0.0)
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
        first = max(0, int(left // self._slot_w) - 1)
        last = min(self._count - 1, int(right // self._slot_w) + 1)
        return range(first, last + 1)

    def _index_at(self, canvas_x: float) -> int | None:
        if self._slot_w == 0:
            return None
        i = int((canvas_x - GAP) // self._slot_w)
        return i if 0 <= i < self._count else None

    def _gap_at(self, canvas_x: float) -> int:
        """Nearest insertion boundary, 0..count (count == after the last frame)."""
        if self._slot_w == 0:
            return 0
        gap = round((canvas_x - GAP) / self._slot_w)
        return max(0, min(gap, self._count))

    # ---- drawing ---------------------------------------------------------

    def _redraw(self) -> None:
        self.canvas.delete("all")
        if self._doc is None:
            return
        top = VPAD + NUMBER_H
        for i in self._visible_indices():
            x = GAP + i * self._slot_w
            self._draw_slot(i, x, top)
        if self._dragging and self._drop_gap is not None:
            self._draw_drop_marker(self._drop_gap, top)

    def _draw_slot(self, i: int, x: int, top: int) -> None:
        is_current = i == self._index
        is_selected = i in self._selection

        if is_selected:
            self.canvas.create_rectangle(
                x - 2, top - 2, x + self._thumb_w + 2, top + THUMB_H + 2,
                fill=SELECTED_BG, outline=SELECTED_OUTLINE, width=1,
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

        # The frame's delay, under its thumbnail. Drawn inside _draw_slot so it
        # inherits the virtualisation for free: only slots in view cost anything,
        # which is the property that lets a 200-frame GIF draw like a 20-frame
        # one (risk 4).
        if self._doc is not None:
            self.canvas.create_text(
                x + self._thumb_w // 2,
                top + THUMB_H + DELAY_H // 2 + 2,
                text=_format_delay(self._doc[i].duration_ms),
                fill=CURRENT_BORDER if is_current else DELAY_FG,
                font=("TkDefaultFont", 7),
            )

    def _draw_drop_marker(self, gap: int, top: int) -> None:
        x = GAP + gap * self._slot_w - GAP // 2
        self.canvas.create_line(
            x, top - 4, x, top + THUMB_H + 4, fill=DROP_MARKER, width=3
        )

    def _photo_for(self, index: int) -> ImageTk.PhotoImage:
        frame = self._doc[index]
        photo = self._photos.get(frame.image_uid)
        if photo is None:
            photo = ImageTk.PhotoImage(self._cache.get(frame))
            self._photos[frame.image_uid] = photo
        return photo

    # ---- interaction -----------------------------------------------------

    def _on_press(self, event: tk.Event) -> None:
        i = self._index_at(self.canvas.canvasx(event.x))
        self._dragging = False
        self._drop_gap = None
        if i is None:
            self._press_index = None
            self._pending_collapse = None
            return
        self._press_index = i
        self._press_x = self.canvas.canvasx(event.x)
        if i in self._selection:
            # Pressing an already-selected frame might be the start of a drag
            # of the whole selection, so defer collapsing to a single until we
            # know it was a plain click (handled on release).
            self._pending_collapse = i
        else:
            self._pending_collapse = None
            self._on_pick(i)

    def _on_shift_press(self, event: tk.Event) -> None:
        i = self._index_at(self.canvas.canvasx(event.x))
        self._press_index = None  # modified clicks don't start drags
        if i is not None:
            self._on_extend(i)

    def _on_ctrl_press(self, event: tk.Event) -> None:
        i = self._index_at(self.canvas.canvasx(event.x))
        self._press_index = None
        if i is not None:
            self._on_toggle(i)

    def _on_motion(self, event: tk.Event) -> None:
        if self._press_index is None:
            return
        x = self.canvas.canvasx(event.x)
        if not self._dragging and abs(x - self._press_x) < DRAG_THRESHOLD:
            return
        self._dragging = True
        self._pending_collapse = None  # it's a drag, not a click
        self._drop_gap = self._gap_at(x)
        self._redraw()

    def _on_release(self, _event: tk.Event) -> None:
        if self._dragging and self._drop_gap is not None:
            self._on_reorder(self._drop_gap)
        elif self._pending_collapse is not None:
            # A plain click on an already-selected frame collapses to just it.
            self._on_pick(self._pending_collapse)
        self._press_index = None
        self._dragging = False
        self._drop_gap = None
        self._pending_collapse = None

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
