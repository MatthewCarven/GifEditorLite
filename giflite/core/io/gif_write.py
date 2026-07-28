"""GIF writer: a Document of coalesced RGBA frames back out to a .gif.

The inverse of gif_read. Frames are full-canvas RGBA in memory (ARCHITECTURE.md
5), so we write them as full frames with disposal=2 (restore to background)
and let each frame stand alone -- which is exactly what the reader coalesces
back to, so a save/open round-trips.

Two format realities are baked in here:

- GIF is palette-based: at most 256 colours per frame, and transparency is a
  single fully-transparent palette index, not an alpha channel. Frames with
  transparent pixels reserve one index for it; opaque frames use all 256.

- Pillow's encoder merges identical *consecutive* frames and sums their
  durations. This is unconditional (verified: it happens with optimize=False
  and no disposal too), so a held-duplicate frame becomes one longer frame.
  Playback is pixel- and timing-identical; only the frame count changes on
  reopen. `count_merges` reports how many frames this will fold, so the UI can
  mention it. Faithful frame-count preservation is a deferred project-file
  concern (see TODO / ARCHITECTURE risk 2), not something to fight the encoder
  over here.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from giflite.core.model import Document

EXTENSIONS = (".gif",)

_ALPHA_CUTOFF = 128  # below this, a pixel is written as transparent
_TRANSPARENT_INDEX = 255  # reserved when a frame has transparency


def count_merges(doc: Document) -> int:
    """How many frames the encoder will fold away (identical to their
    predecessor). Equals frames-written-minus-one summed over identical runs,
    i.e. the drop in frame count on reopen."""
    merges = 0
    prev: bytes | None = None
    for frame in doc.frames:
        data = frame.image.tobytes()
        if data == prev:
            merges += 1
        prev = data
    return merges


def _to_palette(image: Image.Image) -> Image.Image:
    """RGBA frame -> P-mode image, handling transparency and dithering."""
    alpha = image.getchannel("A")
    has_transparency = alpha.getextrema()[0] < 255
    rgb = image.convert("RGB")

    if not has_transparency:
        return rgb.convert(
            "P", palette=Image.ADAPTIVE, colors=256, dither=Image.FLOYDSTEINBERG
        )

    # Reserve one index for transparency, so quantise to 255 colours.
    pal = rgb.convert(
        "P", palette=Image.ADAPTIVE, colors=255, dither=Image.FLOYDSTEINBERG
    )
    # ADAPTIVE returns only as many entries as the frame actually needs -- an
    # 8-colour frame comes back with an 8-entry palette. Pasting index 255 into
    # that would reference a slot the palette does not have, and Pillow's
    # optimise pass then sizes the colour table from the (short) palette while
    # writing a transparency index past its end. Decoders disagree about what an
    # out-of-range index means, so the frame renders opaque in some viewers and
    # transparent in others. Padding to a full 256 entries makes index 255 real.
    entries = list(pal.getpalette() or [])
    entries.extend([0] * (768 - len(entries)))
    pal.putpalette(entries)

    # Paste the transparent index everywhere alpha is low.
    transparent_mask = alpha.point(lambda a: 255 if a < _ALPHA_CUTOFF else 0).convert("1")
    pal.paste(_TRANSPARENT_INDEX, mask=transparent_mask)
    pal.info["transparency"] = _TRANSPARENT_INDEX
    return pal


def _loop_kwarg(doc: Document) -> dict:
    # doc.loop: 0 == forever. A finite count is passed through; the common
    # infinite case round-trips cleanly (verified).
    return {"loop": doc.loop}


def write_gif(doc: Document, path: Path) -> None:
    """Encode `doc` to a GIF at `path`."""
    doc.validate()
    path = Path(path)

    frames = [_to_palette(f.image) for f in doc.frames]
    durations = [f.duration_ms for f in doc.frames]

    has_transparency = any("transparency" in f.info for f in frames)
    save_kwargs = dict(
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        disposal=2,  # each frame is complete; clear to background between them
        optimize=True,
        **_loop_kwarg(doc),
    )
    if has_transparency:
        # A frame-level transparency index; the encoder uses it per frame.
        save_kwargs["transparency"] = _TRANSPARENT_INDEX

    frames[0].save(path, format="GIF", **save_kwargs)
