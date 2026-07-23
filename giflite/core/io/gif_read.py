"""GIF reader: decode to fully-coalesced RGBA frames.

Two pieces of measured Pillow behaviour drive this module (ARCHITECTURE.md 12):

12.1  `ImageSequence.Iterator` yields the *same object*, seeked in place. Six
      iterations return one distinct object, so retaining frames without
      copying gives you N references to the last frame. `.convert("RGBA")`
      allocates a new image, so the correct loop is also the natural one.

12.2  Pillow composites disposal methods for us on seek, so frames arrive
      full-canvas rather than as partial deltas. We do not hand-roll disposal.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageSequence

from giflite.core.model import (
    BYTES_PER_PIXEL,
    DEFAULT_DURATION_MS,
    MIN_DURATION_MS,
    Document,
    Frame,
    quantise_duration,
)

EXTENSIONS = (".gif",)


@dataclass(frozen=True, slots=True)
class Probe:
    """Cheap look at a file, so callers can warn before committing memory."""

    size: tuple[int, int]
    frame_count: int

    @property
    def nbytes_estimate(self) -> int:
        w, h = self.size
        return w * h * BYTES_PER_PIXEL * self.frame_count


def probe_gif(path: Path) -> Probe:
    """Read dimensions and frame count without building RGBA copies."""
    with Image.open(path) as im:
        return Probe(size=im.size, frame_count=getattr(im, "n_frames", 1))


def _read_loop(info: dict) -> int:
    """GIF loop semantics: 0 means forever, absent means play once."""
    if "loop" not in info:
        return 1
    try:
        return max(int(info["loop"]), 0)
    except (TypeError, ValueError):
        return 0


def read_gif(path: Path) -> Document:
    """Load a GIF into a validated Document of RGBA frames."""
    path = Path(path)
    frames: list[Frame] = []

    with Image.open(path) as im:
        size = im.size
        loop = _read_loop(im.info)

        for raw in ImageSequence.Iterator(im):
            # convert() allocates -- this is what saves us from 12.1
            image = raw.convert("RGBA")

            # Defensive: post-coalesce frames should already be canvas-sized,
            # but a malformed file can carry an oversized or offset frame and
            # we would rather pad than fail validation later.
            if image.size != size:
                canvas = Image.new("RGBA", size, (0, 0, 0, 0))
                canvas.paste(image, (0, 0))
                image = canvas

            # An unknown (0) or sub-threshold delay is what browsers bump to
            # ~100ms, so mirror that here -- this is the reader's clamp, kept
            # out of quantise_duration so editing math stays monotonic.
            raw_duration = raw.info.get("duration") or 0
            if raw_duration < MIN_DURATION_MS:
                duration = DEFAULT_DURATION_MS
            else:
                duration = quantise_duration(raw_duration)
            frames.append(Frame.new(image, duration))

    document = Document(
        frames=tuple(frames),
        size=size,
        loop=loop,
        meta={"source_format": "gif"},
    )
    document.validate()
    return document
