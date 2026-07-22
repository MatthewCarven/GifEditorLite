"""Reader tests.

Several of these encode measured Pillow behaviour (ARCHITECTURE.md 12) rather
than what the GIF spec might lead you to expect. Where the format is lossy,
the assertion says so instead of pretending otherwise.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from giflite.core.model import DEFAULT_DURATION_MS
from giflite.core.io.gif_read import probe_gif, read_gif
from tests.conftest import make_gif


def test_reads_every_frame(gif_path: Path):
    doc = read_gif(gif_path)
    assert len(doc) == 6
    assert doc.size == (80, 40)


def test_frames_are_normalised_to_rgba(gif_path: Path):
    doc = read_gif(gif_path)
    assert {f.image.mode for f in doc} == {"RGBA"}
    assert {f.image.size for f in doc} == {doc.size}


def test_each_frame_is_a_distinct_object(gif_path: Path):
    """Guards ARCHITECTURE.md 12.1.

    ImageSequence.Iterator yields one object seeked in place, so a reader that
    retains frames without converting ends up with N references to the last
    frame. This is the test that catches that regression.
    """
    doc = read_gif(gif_path)
    assert len({id(f.image) for f in doc}) == len(doc)


def test_frames_are_coalesced(gif_path: Path):
    """Guards ARCHITECTURE.md 12.2.

    The fixture writes an optimised GIF where only the moving dot changes.
    If disposal handling regressed, the static band would be missing from
    every frame after the first.
    """
    doc = read_gif(gif_path)
    band_y = doc.size[1] - 5
    for i, frame in enumerate(doc):
        assert frame.image.getpixel((2, band_y)) == (200, 40, 40, 255), (
            f"frame {i} lost the static background"
        )


def test_durations_are_quantised_on_read(tmp_path: Path):
    """Sub-20ms delays become the viewer-equivalent default, not a silent 0."""
    path = make_gif(tmp_path / "timed.gif", frames=4, durations=[100, 33, 5, 250])
    doc = read_gif(path)
    assert [f.duration_ms for f in doc] == [100, 30, DEFAULT_DURATION_MS, 250]


def test_loop_absent_means_play_once(tmp_path: Path):
    path = make_gif(tmp_path / "noloop.gif", frames=3, loop=None)
    assert read_gif(path).loop == 1


def test_loop_zero_means_forever(tmp_path: Path):
    path = make_gif(tmp_path / "loop.gif", frames=3, loop=0)
    assert read_gif(path).loop == 0


def test_probe_matches_the_real_read(gif_path: Path):
    probe = probe_gif(gif_path)
    doc = read_gif(gif_path)
    assert probe.frame_count == len(doc)
    assert probe.size == doc.size
    assert probe.nbytes_estimate == doc.nbytes_estimate


def test_single_frame_gif_is_valid(tmp_path: Path):
    path = tmp_path / "one.gif"
    Image.new("P", (10, 10)).save(path)
    doc = read_gif(path)
    assert len(doc) == 1
    doc.validate()


def test_read_result_always_validates(gif_path: Path):
    read_gif(gif_path).validate()
