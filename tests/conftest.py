"""Shared fixtures. Everything here runs headless -- no display required."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw


def make_gif(
    path: Path,
    frames: int = 6,
    size: tuple[int, int] = (80, 40),
    durations: list[int] | None = None,
    loop: int | None = 0,
    optimize: bool = True,
) -> Path:
    """Write a delta-optimisable GIF: static background plus a moving dot.

    The static band matters -- if coalescing regresses, it vanishes from every
    frame after the first, which is what `test_gif_read` checks for.
    """
    width, height = size
    images = []
    for i in range(frames):
        im = Image.new("RGB", size, (20, 20, 60))
        draw = ImageDraw.Draw(im)
        draw.rectangle([0, height - 10, width - 1, height - 1], fill=(200, 40, 40))
        x = int(i * (width - 12) / max(frames - 1, 1))
        draw.ellipse([x, 5, x + 10, 15], fill=(250, 230, 40))
        images.append(im.convert("P", palette=Image.ADAPTIVE))

    kwargs = dict(
        save_all=True,
        append_images=images[1:],
        duration=durations or [100] * frames,
        optimize=optimize,
        disposal=2,
    )
    if loop is not None:
        kwargs["loop"] = loop
    images[0].save(path, **kwargs)
    return path


@pytest.fixture
def gif_path(tmp_path: Path) -> Path:
    return make_gif(tmp_path / "sample.gif")


@pytest.fixture
def solid_doc():
    """A Document of distinct solid colours -- cheap and easy to assert on."""
    from giflite.core.model import Document, Frame

    def build(count: int = 5, size: tuple[int, int] = (8, 8)):
        frames = tuple(
            Frame.new(Image.new("RGBA", size, (i * 20, 0, 0, 255)), 100)
            for i in range(count)
        )
        return Document(frames=frames, size=size)

    return build
