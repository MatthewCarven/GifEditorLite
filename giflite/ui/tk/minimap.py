"""Navigator: a fitted thumbnail of the frame with the visible region on it.

Replaces the pan buttons an earlier draft put in the toolbar, and is a better
answer than they were for two reasons.

**It gives position, not just motion.** Buttons move the view but say nothing
about where it is. At 3200% on an 82px GIF you can see about 28 pixels, with
nothing to tell you *which* 28. The rectangle on the map is that answer, and
dragging it is panning, so the control and the readout are the same object.

**It keeps the preview's mouse entirely the tools'.** Panning by dragging *here*
is not a gesture on the preview canvas, so there is still no wheel binding and
no middle-drag that could land inside a stroke -- the property that made
buttons-only attractive in the first place, kept while getting the drag anyway
(ARCHITECTURE.md §20.5, §21).

The mapping is not reimplemented. This widget owns a second `ViewTransform`,
locked to fit, and asks it exactly the questions the preview asks its own: where
the image lands, and which coordinate is under the cursor. Two copies of that
arithmetic is how the half-pixel bugs in §19.1 would come back on one side only.
"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageTk

from .view import ViewTransform

BACKGROUND = "#1c1c1f"
BORDER = "#55555c"
# The visible-region rectangle. Same accent as the crop marquee: both mean
# "this is the part you are working on", and a second accent colour would be
# one more thing to remember.
VIEWPORT_OUTLINE = "#4a9eff"
# Everything outside the rectangle is dimmed with a stippled fill -- Tk canvas
# items have no alpha, and a 50% stipple over a dark colour is the cheap,
# toolkit-native way to say "not this part".
DIM_FILL = "#000000"
DIM_STIPPLE = "gray50"

# The thumbnail sits close to the panel edge; the preview's 16px of breathing
# room would be a tenth of the width here.
MAP_PAD = 6


class MiniMap(tk.Canvas):
    """A navigator thumbnail. `on_center(ix, iy)` is called with an *image*
    coordinate whenever the user points at part of the map."""

    def __init__(self, master: tk.Misc, on_center, height: int = 120, **kwargs) -> None:
        super().__init__(
            master,
            background=BACKGROUND,
            highlightthickness=1,
            highlightbackground=BORDER,
            borderwidth=0,
            height=height,
            **kwargs,
        )
        self._on_center = on_center
        # Fit-locked: a navigator that could itself be panned would need a
        # navigator. Nothing ever calls zoom or pan on this one.
        self.view = ViewTransform(fit_pad=MAP_PAD)
        self._source: Image.Image | None = None
        self._source_key: object = None
        self._visible: tuple[int, int, int, int] | None = None
        # Strong reference or Tk collects it and the map goes blank (risk 6).
        self._photo: ImageTk.PhotoImage | None = None
        self._thumbs: dict[tuple, ImageTk.PhotoImage] = {}
        self._last_size = (0, 0)
        self.bind("<Configure>", self._on_configure)
        self.bind("<ButtonPress-1>", self._on_point)
        self.bind("<B1-Motion>", self._on_point)

    # ---- public ----------------------------------------------------------

    def show(self, image: Image.Image, key: object,
             visible: tuple[int, int, int, int] | None) -> None:
        """Display `image` with `visible` (in source pixels) marked."""
        self._source = image
        self._source_key = key
        self._visible = visible
        self.view.set_source(*image.size)
        self._redraw()

    def clear(self) -> None:
        self._source = None
        self._source_key = None
        self._visible = None
        self._photo = None
        self._thumbs.clear()
        self.delete("all")

    def invalidate(self) -> None:
        self._thumbs.clear()

    # ---- internals -------------------------------------------------------

    def _on_configure(self, event: tk.Event) -> None:
        size = (event.width, event.height)
        if size == self._last_size:
            return
        self._last_size = size
        self.view.set_viewport(*size)
        self._redraw()

    def _on_point(self, event: tk.Event) -> None:
        """Point at the map -> centre the preview there.

        Absolute, not relative: the position you click *is* the position you
        get, which is why grabbing the middle of the rectangle and dragging
        feels right and also why clicking somewhere else jumps straight there.
        A relative drag would need a grab offset and would make a plain click do
        nothing.

        `snap="edge"` because a centre is a point in the image, not a pixel to
        be addressed -- and it clamps, so dragging off the side of the map
        slides the view to the edge and stops instead of flinging it.
        """
        if self._source is None:
            return
        ix, iy = self.view.display_to_image(event.x, event.y, snap="edge")
        self._on_center(ix, iy)

    def _thumbnail(self, size: tuple[int, int]) -> ImageTk.PhotoImage:
        cache_key = (self._source_key, size)
        cached = self._thumbs.get(cache_key)
        if cached is not None:
            return cached
        thumb = self._source.resize(size, Image.LANCZOS)
        if thumb.mode != "RGBA":
            thumb = thumb.convert("RGBA")
        # Flattened onto the panel colour rather than checkerboarded: at this
        # size a checker pattern is visual noise competing with the one thing
        # the map exists to show.
        backing = Image.new("RGBA", size, BACKGROUND)
        backing.alpha_composite(thumb)
        photo = ImageTk.PhotoImage(backing)
        # Bounded by construction: one entry per (frame, panel size), and the
        # panel is only resized by the user dragging the window.
        if len(self._thumbs) > 256:
            self._thumbs.clear()
        self._thumbs[cache_key] = photo
        return photo

    def _redraw(self) -> None:
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if self._source is None or width <= 1 or height <= 1:
            return
        self.view.set_viewport(width, height)
        left, top, fw, fh = self.view.geometry()
        self._photo = self._thumbnail((fw, fh))
        self.create_image(left, top, image=self._photo, anchor="nw")

        if self._visible is None:
            return
        x0, y0, x1, y1 = self._visible
        dx0, dy0 = self.view.image_to_display(x0, y0)
        dx1, dy1 = self.view.image_to_display(x1, y1)
        # Covering the whole image means there is nothing to navigate; drawing a
        # rectangle around everything would be noise claiming to be information.
        if (x0, y0, x1, y1) == (0, 0, *self._source.size):
            return
        for box in (
            (left, top, left + fw, dy0),            # above
            (left, dy1, left + fw, top + fh),       # below
            (left, dy0, dx0, dy1),                  # left
            (dx1, dy0, left + fw, dy1),             # right
        ):
            if box[2] > box[0] and box[3] > box[1]:
                self.create_rectangle(*box, fill=DIM_FILL, stipple=DIM_STIPPLE,
                                      outline="")
        self.create_rectangle(dx0, dy0, dx1, dy1, outline=VIEWPORT_OUTLINE, width=1)
