"""Thumbnail cache -- PIL images only, no toolkit types.

Lives in `app/` on purpose. The Tk timeline turns these into PhotoImages and
holds *those* itself, because ImageTk pulls in tkinter and this layer must
stay toolkit-free (ARCHITECTURE.md 11.4, risk 6).

Keyed by `frame.image_uid`, never `id(image)`: CPython reuses addresses after
GC, so an evicted image's id can collide with a new one and serve the wrong
picture (ARCHITECTURE.md risk 6). The uid is stable for the life of a set of
pixels and shared when pixels are shared (a duplicated frame hits the cache).

No LRU. A GIF's worth of 56px thumbnails is a couple of megabytes; eviction
machinery would be more code than the thing it manages. `retain()` drops
whatever is no longer in the document, which is all the bounding this needs
until a real GIF proves otherwise.
"""

from __future__ import annotations

from typing import Iterable

from PIL import Image

from giflite.core.model import Frame

DEFAULT_THUMB_HEIGHT = 56


class ThumbnailCache:
    def __init__(self, height: int = DEFAULT_THUMB_HEIGHT) -> None:
        self.height = height
        self._thumbs: dict[int, Image.Image] = {}

    def get(self, frame: Frame) -> Image.Image:
        """Return a cached thumbnail for the frame, building it on first ask."""
        thumb = self._thumbs.get(frame.image_uid)
        if thumb is None:
            thumb = self._render(frame.image)
            self._thumbs[frame.image_uid] = thumb
        return thumb

    def thumb_width(self, frame_size: tuple[int, int]) -> int:
        """The width a thumbnail will have, without building it.

        All frames share the canvas size, so the timeline can lay out uniform
        slots from this alone -- no need to render every frame to measure it.
        """
        w, h = frame_size
        return max(1, round(w * self.height / h))

    def retain(self, uids: Iterable[int]) -> None:
        """Forget every thumbnail whose frame is no longer present."""
        keep = set(uids)
        for uid in [u for u in self._thumbs if u not in keep]:
            del self._thumbs[uid]

    def clear(self) -> None:
        self._thumbs.clear()

    def __len__(self) -> int:
        return len(self._thumbs)

    # ---- internals -------------------------------------------------------

    def _render(self, image: Image.Image) -> Image.Image:
        w, h = image.size
        target = (self.thumb_width((w, h)), self.height)
        # LANCZOS: thumbnails are almost always a downscale, and a crisp small
        # image reads better in a dense strip than a blocky NEAREST one.
        return image.resize(target, Image.LANCZOS)
