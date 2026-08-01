"""Core data model: Frame, Document, Selection.

Pure Python + Pillow. Nothing in this module knows a UI exists.

The load-bearing invariant of the whole design lives here, and the type
system cannot enforce it, so it is stated plainly:

    Operations NEVER mutate `frame.image` in place. They allocate a new
    image and a new Frame.

`Document` is frozen, but `Frame.image` is a mutable Pillow object shared by
reference across every history snapshot (that sharing is exactly what makes
undo cheap -- see core/history.py). One in-place `paste` or `ImageDraw` call
would silently rewrite history. `tests/test_immutability.py` guards this.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from PIL import Image

EMPTY_MAP: Mapping[str, Any] = MappingProxyType({})

# GIF stores frame delays in centiseconds, so anything we hold must be a
# multiple of 10ms or we would be lying to the user about timing (see
# ARCHITECTURE.md 12.3). Pillow *floors* on save, so we floor too -- rounding
# would show 40ms in the timeline for a value stored as 30ms.
DURATION_STEP_MS = 10

# The floor for reliable playback. Browsers treat a delay below 2 centiseconds
# as ~100ms, so 20ms is the smallest value that plays as authored. Editing ops
# (e.g. Scale Speed) clamp here rather than letting a frame drop below it --
# going lower doesn't play faster, it plays unpredictably slower.
MIN_DURATION_MS = 20

# What an *unknown* or sub-threshold delay becomes on read -- mirrors the
# browser clamp so the editor shows real playback, not an optimistic value.
# This is a reader concern, applied in gif_read; quantise_duration does NOT
# jump here (that would turn "speed up" into "slow down").
DEFAULT_DURATION_MS = 100

BYTES_PER_PIXEL = 4  # RGBA

_uid_counter = itertools.count(1)


def next_image_uid() -> int:
    """Allocate an id for a distinct set of pixels.

    Used as a cache key for thumbnails and scaled previews. Deliberately not
    `id(image)`: CPython reuses addresses after garbage collection, so an
    evicted image's id can collide with a new one and serve wrong pixels.

    The rule: reuse the uid when you reuse the image object (duplicating a
    frame, changing only its duration), allocate a new one when you produce
    new pixels (crop, resize, draw).
    """
    return next(_uid_counter)


def quantise_duration(ms: float) -> int:
    """Snap a delay to a storable 10ms multiple, floored at MIN_DURATION_MS.

    Pure and monotonic: a smaller input never yields a larger output. That is
    why sub-floor values clamp to 20ms rather than jumping to the 100ms
    default -- otherwise "make this frame faster" could make it slower. The
    "unknown delay -> 100ms" mapping is the reader's job (gif_read), not this
    function's.
    """
    stepped = (int(ms) // DURATION_STEP_MS) * DURATION_STEP_MS
    return max(stepped, MIN_DURATION_MS)


@dataclass(frozen=True, slots=True, eq=False)
class Frame:
    """One fully-composited frame.

    `eq=False` gives identity semantics, which is both cheap and what we
    actually want: Pillow's `Image.__eq__` compares pixel data, so value
    equality would turn "did undo restore the original?" into a full buffer
    comparison of every frame. Undo restores the *same objects*, so identity
    is the correct and fast check.
    """

    image: Image.Image
    duration_ms: int
    image_uid: int = field(default_factory=next_image_uid)

    @classmethod
    def new(cls, image: Image.Image, duration_ms: float) -> "Frame":
        """Build a frame around freshly-allocated pixels."""
        return cls(image, quantise_duration(duration_ms))

    def with_duration(self, duration_ms: float) -> "Frame":
        """Same pixels, new timing -- keeps the uid so caches still hit."""
        return Frame(self.image, quantise_duration(duration_ms), self.image_uid)

    def sharing_pixels(self) -> "Frame":
        """A distinct Frame over the same image (used by duplicate)."""
        return Frame(self.image, self.duration_ms, self.image_uid)


@dataclass(frozen=True, slots=True)
class Document:
    """An animation: an ordered tuple of frames on a fixed canvas."""

    frames: tuple[Frame, ...]
    size: tuple[int, int]
    loop: int = 0  # 0 == forever; 1 == play once
    # A bare `= EMPTY_MAP` is what this wants to say, and it is what it said
    # until Python 3.11: the default is a *read-only* proxy, so sharing it
    # between instances is safe. But 3.11 replaced dataclasses' "is it a list,
    # dict or set" check with "is its type unhashable", and `mappingproxy` is
    # unhashable -- so the class body itself raised, and the whole package
    # failed to import on 3.11+. The factory hands back the same singleton;
    # only the spelling changed.
    meta: Mapping[str, Any] = field(default_factory=lambda: EMPTY_MAP)

    def __post_init__(self) -> None:
        # A plain dict handed in by a caller would be a mutable hole in an
        # otherwise frozen object, so wrap it once, here, rather than trusting
        # every construction site to remember.
        if not isinstance(self.meta, MappingProxyType):
            object.__setattr__(self, "meta", MappingProxyType(dict(self.meta)))

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self) -> Iterator[Frame]:
        return iter(self.frames)

    def __getitem__(self, index: int) -> Frame:
        return self.frames[index]

    @property
    def total_duration_ms(self) -> int:
        return sum(f.duration_ms for f in self.frames)

    @property
    def nbytes_estimate(self) -> int:
        w, h = self.size
        return w * h * BYTES_PER_PIXEL * len(self.frames)

    def validate(self) -> None:
        """Assert the invariants. Called by readers, tests and debug builds.

        A zero-frame Document is rejected on purpose: "nothing is loaded" is
        represented by `AppController.doc is None`, not by an empty document.
        Allowing both would leak the ambiguity into every operation.
        """
        if not self.frames:
            raise ValueError("Document must have at least one frame")
        w, h = self.size
        if w <= 0 or h <= 0:
            raise ValueError(f"Invalid canvas size {self.size!r}")
        for i, frame in enumerate(self.frames):
            if frame.image.mode != "RGBA":
                raise ValueError(
                    f"Frame {i} has mode {frame.image.mode!r}, expected 'RGBA'"
                )
            if frame.image.size != self.size:
                raise ValueError(
                    f"Frame {i} is {frame.image.size}, expected canvas size {self.size}"
                )
            if frame.duration_ms < MIN_DURATION_MS:
                raise ValueError(
                    f"Frame {i} duration {frame.duration_ms}ms is below the "
                    f"{MIN_DURATION_MS}ms floor"
                )


@dataclass(frozen=True, slots=True)
class Selection:
    """Which frames the next operation applies to.

    Holds no UI state. `anchor` exists so shift-click can extend a range from
    a fixed point, which is selection semantics rather than toolkit detail.
    """

    indices: frozenset[int] = frozenset()
    anchor: int | None = None

    @classmethod
    def single(cls, index: int) -> "Selection":
        return cls(frozenset({index}), index)

    @classmethod
    def span(cls, start: int, end: int, anchor: int | None = None) -> "Selection":
        lo, hi = (start, end) if start <= end else (end, start)
        return cls(frozenset(range(lo, hi + 1)), anchor if anchor is not None else start)

    @classmethod
    def empty(cls) -> "Selection":
        return cls()

    def __bool__(self) -> bool:
        return bool(self.indices)

    def __len__(self) -> int:
        return len(self.indices)

    def __contains__(self, index: int) -> bool:
        return index in self.indices

    @property
    def ordered(self) -> tuple[int, ...]:
        return tuple(sorted(self.indices))

    @property
    def first(self) -> int | None:
        return min(self.indices) if self.indices else None

    def toggled(self, index: int) -> "Selection":
        if index in self.indices:
            return Selection(self.indices - {index}, self.anchor)
        return Selection(self.indices | {index}, index)

    def clamped(self, frame_count: int) -> "Selection":
        """Drop out-of-range indices after the frame count changes."""
        kept = frozenset(i for i in self.indices if 0 <= i < frame_count)
        anchor = self.anchor if self.anchor in kept else (min(kept) if kept else None)
        return Selection(kept, anchor)


@dataclass(frozen=True, slots=True)
class Region:
    """Which *pixels* the next operation applies to -- Selection's other axis.

    Selection answers "which frames", this answers "which part of the canvas",
    and the two are deliberately independent: copying a sprite out of frame 7
    and stamping it into frames 0-20 is one region and twenty-one frames.

    **Edge coordinates, like a crop box and unlike a shape.** `x`/`y` are the
    lines between pixels, and `width`/`height` count pixels, so a region is
    exactly the argument list `canvas.crop` already takes and `SelectTool`
    can declare `coords = "edge"` for the same reason `CropTool` does
    (ARCHITECTURE.md 19.1.1). The conversion to the pixel-inclusive convention
    the shape masks use happens in one named place, `_region_mask`, rather than
    being open-coded wherever a region meets a drawing call.

    Lives in core beside Selection rather than in the frontend because it is
    session state a second frontend would otherwise re-derive -- and because
    clamping it to a canvas that just got smaller is arithmetic, not UI.
    """

    x: int
    y: int
    width: int
    height: int

    def __bool__(self) -> bool:
        return self.width > 0 and self.height > 0

    @property
    def box(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) with right/bottom *exclusive* -- what
        `Image.crop` wants, and what the marquee is drawn corner to corner."""
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    @classmethod
    def from_corners(cls, x0: int, y0: int, x1: int, y1: int) -> "Region | None":
        """Build from two dragged corners in either order. None for a click.

        A zero-width or zero-height drag is not a tiny region, it is *no*
        region -- the same call `CropTool` makes about a stray click, for the
        same reason: an empty rectangle is never what someone meant.
        """
        left, right = sorted((int(x0), int(x1)))
        top, bottom = sorted((int(y0), int(y1)))
        if right - left < 1 or bottom - top < 1:
            return None
        return cls(left, top, right - left, bottom - top)

    def clamped(self, size: tuple[int, int]) -> "Region | None":
        """Trim to a canvas of `size`, or None if nothing is left inside it.

        Called whenever the canvas changes shape -- crop, resize, rotate -- so
        a region selected before the edit cannot outlive the pixels it named.
        Returning None rather than an empty Region keeps "there is no region"
        a single representation instead of two that have to agree.
        """
        width, height = size
        left = max(0, min(self.x, width))
        top = max(0, min(self.y, height))
        right = max(left, min(self.x + self.width, width))
        bottom = max(top, min(self.y + self.height, height))
        if right - left < 1 or bottom - top < 1:
            return None
        return Region(left, top, right - left, bottom - top)
