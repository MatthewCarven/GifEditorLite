"""The .gifproj project format: the manifest and its frames, zipped.

ARCHITECTURE.md 18 deferred this; 25 quietly built most of it. An exported
image sequence is already "each frame as a lossless PNG plus a JSON manifest"
-- the project format minus the container -- so this module is deliberately
thin: the same `giflite.json` (same `MANIFEST_NAME`, same schema, same
version) and the same PNG frames, inside one file you can move, back up and
attach. What GIF cannot hold survives here exactly: identical consecutive
frames stay separate instead of merging into longer holds (12.4, risk 2),
partial alpha round-trips (the eraser-opacity ramp that a GIF save flattens
to 1 bit), and timing comes back as authored.

A container changes three things, and only these:

- **The manifest is required.** In a folder, absent is normal -- folders of
  PNGs come from everywhere, and the sequence reader improvises order and
  timing for them. A `.gifproj` without a manifest is not a project missing
  its paperwork, it is some other program's zip; improvising a document out
  of it would open the wrong thing convincingly. `ManifestError`, by name.

- **Order comes from the manifest alone.** The folder reader natural-sorts
  because a directory listing has no order worth trusting. A container has
  no such excuse: its manifest names every member, so member order in the
  zip is deliberately meaningless (and one test writes them backwards to
  keep it so).

- **Saving is deterministic.** Members are written in manifest order with a
  fixed timestamp (zip's own 1980 epoch), so saving the same document twice
  produces byte-identical files -- a backup tool or a diff can tell "saved
  again" from "changed". `writestr` given a bare string name stamps the wall
  clock instead, which would make each save unique for no reason; see `_add`
  for why the ZipInfo spelling is the deterministic one.

Everything else is inherited on purpose. The canvas rule (declared size wins,
else the union of what arrived) and top-left placement are the sequence
reader's, imported rather than restated. PNG members are STORED, not
deflated: PNG is already DEFLATE inside, so compressing it again spends CPU
to grow the file slightly. The manifest, being JSON, does deflate.

Reading never extracts to disk -- members are decoded in memory -- so a
hostile manifest naming `../evil` can at worst fail to match a member name.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from PIL import Image

from giflite.core.io.manifest import (
    MANIFEST_NAME,
    ManifestError,
    build_manifest,
    parse_manifest,
)
from giflite.core.io.sequence import place_on_canvas
from giflite.core.model import Document, Frame

EXTENSIONS = (".gifproj",)


def read_gifproj(path: Path) -> Document:
    """Load a project container. Raises rather than guessing.

    Failure modes are kept distinct because they mean different things to the
    person who hits them: not-a-zip is "wrong file", no-manifest is "somebody
    else's zip", and a manifest problem (unknown version, missing member) is
    "a project this build cannot honour" -- all `ManifestError` except the
    first, and all worded to say which one happened.
    """
    path = Path(path)
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError(
            f"{path.name} is not a project file (not a zip archive)"
        ) from exc

    with archive:
        names = set(archive.namelist())
        if MANIFEST_NAME not in names:
            raise ManifestError(
                f"{path.name} has no {MANIFEST_NAME} -- not a GIF Editor Lite project"
            )
        try:
            data = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ManifestError(f"{MANIFEST_NAME} could not be read: {exc}") from exc
        entries, loop, declared = parse_manifest(data)

        images = []
        for name, duration in entries:
            if name not in names:
                # Same wording as the folder reader's version of this: the
                # manifest is the deliberate statement, so a member it names
                # and the zip lacks is the manifest's error to report.
                raise ManifestError(f"{MANIFEST_NAME} lists a missing file: {name}")
            with Image.open(io.BytesIO(archive.read(name))) as handle:
                # load() before convert, same as the folder reader: a lazy
                # image whose buffer has gone raises much later and somewhere
                # confusing.
                handle.load()
                images.append((handle.convert("RGBA"), duration))

    # The canvas: what the manifest declares, or the union of what arrived --
    # the sequence reader's rule, for the sequence reader's reasons.
    if declared is not None:
        size = declared
    else:
        size = (max(im.width for im, _ in images), max(im.height for im, _ in images))

    frames = tuple(
        Frame.new(place_on_canvas(im, size), duration) for im, duration in images
    )
    doc = Document(frames, size, loop=loop, meta={"source_format": "gifproj"})
    doc.validate()
    return doc


def write_gifproj(doc: Document, path: Path) -> None:
    """Write `doc` as a container: manifest first, then frames in order.

    Same member names as a sequence export (`frame_0001.png`, zero-padded to
    the frame count's width) -- not because a zip needs the padding, but so an
    unzipped project *is* a valid exported folder, hand-openable by the
    sequence importer and by anything else that sorts names naively.
    """
    doc.validate()
    path = Path(path)
    width = max(4, len(str(len(doc.frames))))
    names = [f"frame_{i:0{width}d}.png" for i in range(1, len(doc.frames) + 1)]
    manifest = build_manifest(doc, names)

    with zipfile.ZipFile(path, "w") as archive:
        _add(archive, MANIFEST_NAME,
             json.dumps(manifest, indent=2) + "\n", zipfile.ZIP_DEFLATED)
        for name, frame in zip(names, doc.frames):
            buffer = io.BytesIO()
            frame.image.save(buffer, format="PNG")
            _add(archive, name, buffer.getvalue(), zipfile.ZIP_STORED)


def _add(archive: zipfile.ZipFile, name: str, payload, compress_type: int) -> None:
    """writestr through an explicit ZipInfo -- the whole determinism story.

    A `ZipInfo` built here defaults `date_time` to zip's 1980 epoch, which is
    exactly the fixed stamp determinism wants; restating it would be a guard
    in front of something that already decides (27.4 and three friends). The
    trap is the *other* spelling: `writestr` handed a bare string stamps the
    wall clock, and every save becomes unique for no reason. The epoch test
    pins the difference, so that refactor cannot land quietly.
    """
    info = zipfile.ZipInfo(name)
    info.compress_type = compress_type
    archive.writestr(info, payload)
