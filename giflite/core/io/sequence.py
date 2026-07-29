"""Image-sequence IO: a folder of stills in, a folder of stills out.

The first source that isn't a single file, and therefore the reason the IO dict
became a registry (ARCHITECTURE.md 25). The reader's *signature* is unchanged --
a directory is still a `Path` -- so what a folder actually breaks is dispatch:
`READERS[path.suffix]` can never match a thing with no suffix.

Three problems a single-file reader never has to answer:

**Order.** A directory listing has none worth trusting, and lexicographic order
puts `frame10.png` between `frame1.png` and `frame2.png`. That is not a corner
case -- it is what happens to every sequence with more than nine frames, and it
looks fine on a small test. `_natural_key` sorts digit runs numerically.

**Size.** Stills need not agree on one, while a `Document` has exactly one
canvas. Frames are padded onto the union of their sizes rather than scaled: this
editor is aimed at pixel art, and silently resampling someone's pixels to make
an import succeed is worse than the import looking odd.

**Timing.** Stills carry none at all. The manifest supplies it when there is
one; otherwise the caller passes a delay, and the frame-delay box exists for
fixing it afterwards.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

from giflite.core.io.manifest import (
    MANIFEST_NAME,
    ManifestError,
    build_manifest,
    parse_manifest,
)
from giflite.core.model import Document, Frame, quantise_duration

# What we will pick up out of a folder. PNG first because it is the lossless one
# and the only one we write; the rest are there because people keep frames in
# whatever their capture tool produced.
SEQUENCE_SUFFIXES = (".png", ".gif", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")

_DIGITS = re.compile(r"(\d+)")


def _natural_key(name: str):
    """Sort key that reads digit runs as numbers.

    `frame2.png` before `frame10.png`, which plain string ordering gets exactly
    backwards. Case-folded so a folder mixing `Frame` and `frame` doesn't split
    into two runs.
    """
    return [int(part) if part.isdigit() else part.lower()
            for part in _DIGITS.split(name)]


def sequence_files(folder: Path) -> list[Path]:
    """The image files in `folder`, in playback order. Not recursive.

    Deliberately shallow: a nested folder is somebody's unrelated structure, and
    quietly hoovering up a `thumbnails/` subdirectory would be a surprise that
    is hard to notice and annoying to undo.
    """
    folder = Path(folder)
    files = [p for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() in SEQUENCE_SUFFIXES]
    return sorted(files, key=lambda p: _natural_key(p.name))


def _read_manifest(folder: Path):
    """`(entries, loop, canvas)` or None when the folder has no manifest.

    Absent is normal -- a folder of PNGs from anywhere else won't have one. A
    manifest that exists and is broken raises, because at that point somebody
    meant something and guessing at it is worse than saying so.
    """
    path = Path(folder) / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{MANIFEST_NAME} could not be read: {exc}") from exc
    return parse_manifest(data)


def read_sequence(folder: Path, delay_ms: int = 100, loop: int = 0) -> Document:
    """Load a folder of stills as an animation.

    `delay_ms` and `loop` are the fallbacks used when the folder has no
    manifest; a manifest wins, because it is the folder telling us what it
    actually is rather than us telling it.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")

    manifest = _read_manifest(folder)
    if manifest is not None:
        entries, loop, declared = manifest
        paths = []
        for name, duration in entries:
            candidate = folder / name
            if not candidate.is_file():
                raise ManifestError(f"{MANIFEST_NAME} lists a missing file: {name}")
            paths.append((candidate, duration))
    else:
        found = sequence_files(folder)
        if not found:
            raise ValueError(f"No images found in {folder.name}")
        declared = None
        paths = [(p, quantise_duration(delay_ms)) for p in found]

    images = []
    for path, duration in paths:
        with Image.open(path) as handle:
            # load() before the context closes, then convert -- a lazy Pillow
            # image whose file has been closed raises on first access, which
            # would surface much later and somewhere confusing.
            handle.load()
            images.append((handle.convert("RGBA"), duration))

    # The canvas: what the manifest says, or the union of what arrived. Union
    # rather than "the first frame's", so a sequence whose later frames grew
    # isn't cropped by whichever file happened to sort first.
    if declared is not None:
        size = declared
    else:
        size = (max(im.width for im, _ in images), max(im.height for im, _ in images))

    frames = tuple(Frame.new(_onto_canvas(im, size), duration) for im, duration in images)
    doc = Document(frames, size, loop=loop, meta={"source_format": "sequence"})
    doc.validate()
    return doc


def _onto_canvas(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Place `image` top-left on a transparent canvas of `size`.

    Top-left rather than centred: a sequence with mismatched sizes is nearly
    always one where something was added at the right or bottom, and top-left
    keeps the origin -- and therefore every coordinate the user has in their
    head -- where it was. Centring would shift *every* frame by half the
    difference, including the ones that were already the right size.

    Oversized frames are cropped, which only happens when a manifest declares a
    canvas smaller than its own images; the manifest is the more deliberate
    statement of the two.
    """
    if image.size == size:
        return image
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(image.crop((0, 0, min(image.width, size[0]), min(image.height, size[1]))),
                 (0, 0))
    return canvas


def write_sequence(doc: Document, folder: Path, stem: str = "frame") -> list[Path]:
    """Write every frame as a PNG into `folder`, plus a manifest.

    Filenames are zero-padded to the width of the frame count, so the export
    sorts correctly in file managers, shells and anything else that has no
    natural sort -- including a later import by this module, whose natural sort
    then has nothing to fix. Round-tripping through a tool that sorts naively is
    the common case, so the padding is the part that makes the round trip safe.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    width = max(4, len(str(len(doc.frames))))
    written: list[Path] = []
    names: list[str] = []
    for i, frame in enumerate(doc.frames, start=1):
        name = f"{stem}_{i:0{width}d}.png"
        path = folder / name
        frame.image.save(path, format="PNG")
        written.append(path)
        names.append(name)
    manifest = build_manifest(doc, names)
    (folder / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return written
