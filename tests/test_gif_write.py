"""Writer round-trip tests.

Assert what a GIF actually guarantees, not what we wish it did: durations to
10 ms, transparency as a shape (not alpha), and the frame count modulo the
encoder's identical-consecutive merging (ARCHITECTURE.md 12.4).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from giflite.core.io.gif_read import read_gif
from giflite.core.io.gif_write import count_merges, write_gif
from giflite.core.model import Document, Frame


def opaque_doc(n=5, size=(32, 24)) -> Document:
    frames = []
    for i in range(n):
        im = Image.new("RGBA", size, (20, 20, 40, 255))
        ImageDraw.Draw(im).rectangle([i * 3, 4, i * 3 + 8, 16], fill=(240, 200, 40, 255))
        frames.append(Frame.new(im, 100))
    return Document(tuple(frames), size)


def transparent_doc(n=4, size=(32, 24)) -> Document:
    frames = []
    for i in range(n):
        im = Image.new("RGBA", size, (0, 0, 0, 0))  # fully transparent canvas
        ImageDraw.Draw(im).ellipse([i * 4, 4, i * 4 + 10, 16], fill=(200, 60, 60, 255))
        frames.append(Frame.new(im, 100))
    return Document(tuple(frames), size)


class TestRoundTrip:
    def test_frame_count_and_size_survive(self, tmp_path: Path):
        doc = opaque_doc(5)
        out = tmp_path / "o.gif"
        write_gif(doc, out)
        back = read_gif(out)
        assert len(back) == 5
        assert back.size == doc.size

    def test_durations_survive(self, tmp_path: Path):
        frames = tuple(
            Frame.new(Image.new("RGBA", (16, 16), (i * 10, 0, 0, 255)), d)
            for i, d in enumerate([100, 200, 50, 300])
        )
        # 50 is already >= the 20ms floor and a 10ms multiple, so it round-trips
        doc = Document(frames, (16, 16))
        out = tmp_path / "d.gif"
        write_gif(doc, out)
        back = read_gif(out)
        assert [f.duration_ms for f in back] == [100, 200, 50, 300]

    def test_opaque_pixels_are_preserved(self, tmp_path: Path):
        """Few-colour art fits in 256 colours, so the visible pixels are exact."""
        doc = opaque_doc(3)
        out = tmp_path / "o.gif"
        write_gif(doc, out)
        back = read_gif(out)
        for a, b in zip(doc.frames, back.frames):
            assert a.image.convert("RGB").tobytes() == b.image.convert("RGB").tobytes()

    def test_infinite_loop_survives(self, tmp_path: Path):
        doc = Document(opaque_doc(3).frames, (32, 24), loop=0)
        out = tmp_path / "loop.gif"
        write_gif(doc, out)
        assert read_gif(out).loop == 0


class TestTransparency:
    def test_transparent_regions_round_trip_as_a_shape(self, tmp_path: Path):
        doc = transparent_doc(4)
        out = tmp_path / "t.gif"
        write_gif(doc, out)
        back = read_gif(out)
        for a, b in zip(doc.frames, back.frames):
            sa = a.image.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
            sb = b.image.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
            assert sa.tobytes() == sb.tobytes()


class TestMerging:
    def test_held_duplicate_merges_and_sums_duration(self, tmp_path: Path):
        base = opaque_doc(3)
        # duplicate frame 1 (identical pixels) to "hold" it
        held = (base.frames[0], base.frames[1], base.frames[1].sharing_pixels(),
                base.frames[2])
        doc = Document(held, base.size)
        assert count_merges(doc) == 1
        out = tmp_path / "held.gif"
        write_gif(doc, out)
        back = read_gif(out)
        assert len(back) == 3  # the held pair folded into one frame
        assert back.frames[1].duration_ms == 200  # 100 + 100

    def test_no_merges_when_every_frame_differs(self):
        assert count_merges(opaque_doc(5)) == 0
