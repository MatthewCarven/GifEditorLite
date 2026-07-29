"""The sidecar manifest: what a folder of PNGs can't say for itself.

A directory of images carries pixels and an accidental ordering, and nothing
else. Durations, loop count and the intended canvas size all have to be written
down somewhere, and this is the somewhere.

**Deliberately the same schema the deferred project format will use**
(ARCHITECTURE.md 18, 25.4). `.gifproj` was always going to be "each frame as a
lossless PNG plus a JSON manifest, in a container"; an exported folder is that
minus the zip. Designing one schema means the project format later is a reader
and a writer over an *existing* manifest rather than a second dialect of the
same idea, and it means a folder exported today stays readable by it.

Which is also why the manifest is versioned from the first line of it existing.
A format meant to outlive the session that wrote it needs somewhere to put
"this is newer than you understand" long before that day arrives; retrofitting a
version field means guessing what unversioned files meant.

Nothing here touches the filesystem or Pillow -- it converts between a
`Document`'s metadata and a plain dict. The reader and writer own the IO, so
this stays testable as pure data and reusable by a container format that keeps
its bytes somewhere other than a directory.
"""

from __future__ import annotations

from typing import Any

from giflite.core.model import Document, quantise_duration

# Bumped only for a *breaking* change. A reader must refuse a version it does
# not know rather than guess: half-understanding a manifest silently produces a
# document with the wrong timing, which is the failure mode that gets noticed
# three edits later.
MANIFEST_VERSION = 1

# Fixed name, so a container format can look in exactly one place.
MANIFEST_NAME = "giflite.json"


class ManifestError(ValueError):
    """A manifest exists but cannot be trusted. Distinct from "absent", which
    is normal -- a folder of PNGs from anywhere else won't have one."""


def build_manifest(doc: Document, filenames) -> dict[str, Any]:
    """Describe `doc` alongside the filenames its frames were written as.

    Filenames are stored per frame rather than implied by position, so a
    container format can name its members however it likes and a hand-edited
    folder can reorder frames without renaming files.
    """
    frames = [
        {"file": str(name), "duration_ms": int(frame.duration_ms)}
        for name, frame in zip(filenames, doc.frames)
    ]
    return {
        "version": MANIFEST_VERSION,
        "canvas": {"width": doc.size[0], "height": doc.size[1]},
        "loop": doc.loop,
        "frames": frames,
    }


def parse_manifest(data: Any) -> tuple[list[tuple[str, int]], int, tuple[int, int] | None]:
    """`(entries, loop, canvas)` from a manifest dict.

    `entries` is `[(filename, duration_ms), ...]` in playback order. `canvas` is
    None when the manifest doesn't state one, which is legal -- the reader can
    derive it from the images, and a hand-written manifest shouldn't have to
    repeat what the pixels already say.

    Raises `ManifestError` rather than returning something half-built. Every
    branch here is a manifest that exists and is wrong, which is a different
    situation from one that is merely absent, and the caller wants to tell the
    user about it rather than quietly fall back to defaults.
    """
    if not isinstance(data, dict):
        raise ManifestError("manifest is not an object")

    version = data.get("version")
    if version != MANIFEST_VERSION:
        raise ManifestError(
            f"manifest version {version!r} is not supported (this build reads "
            f"version {MANIFEST_VERSION})"
        )

    raw_frames = data.get("frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise ManifestError("manifest lists no frames")

    entries: list[tuple[str, int]] = []
    for i, entry in enumerate(raw_frames):
        if not isinstance(entry, dict) or "file" not in entry:
            raise ManifestError(f"frame {i} has no file name")
        name = str(entry["file"])
        # A missing or unusable duration falls back rather than failing: the
        # frame list is the part that must be right, and a manifest someone
        # hand-edited to reorder frames shouldn't be rejected over timing.
        try:
            duration = quantise_duration(float(entry.get("duration_ms", 100)))
        except (TypeError, ValueError):
            duration = quantise_duration(100)
        entries.append((name, duration))

    try:
        loop = int(data.get("loop", 0))
    except (TypeError, ValueError):
        loop = 0

    canvas = data.get("canvas")
    size = None
    if isinstance(canvas, dict):
        try:
            size = (int(canvas["width"]), int(canvas["height"]))
        except (KeyError, TypeError, ValueError):
            size = None
        if size is not None and (size[0] < 1 or size[1] < 1):
            raise ManifestError(f"manifest canvas {size} is not a usable size")

    return entries, loop, size
