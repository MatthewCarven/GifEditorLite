"""Image-sequence IO: a folder of stills in, a folder of stills out.

The first source that isn't a single file. Most of what is checked here is the
three questions a single-file reader never has to answer -- what order, what
canvas, what timing -- because each of them has an answer that looks right on a
small tidy folder and is wrong on a real one.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from giflite.core.io import format_for, formats, open_filter, reader_for, writer_for
from giflite.core.io.manifest import MANIFEST_NAME, MANIFEST_VERSION, ManifestError
from giflite.core.io.sequence import read_sequence, sequence_files, write_sequence
from giflite.core.model import Document, Frame


def make_folder(path, count=3, size=(10, 8), stem="frame", start=1):
    path.mkdir(parents=True, exist_ok=True)
    for i in range(start, start + count):
        Image.new("RGBA", size, (i * 10 % 256, 40, 80, 255)).save(path / f"{stem}{i}.png")
    return path


def make_doc(count=3, size=(10, 8), durations=None):
    durations = durations or [100] * count
    frames = tuple(
        Frame.new(Image.new("RGBA", size, (i * 30 % 256, 0, 0, 255)), d)
        for i, d in enumerate(durations)
    )
    return Document(frames, size)


class TestOrdering:
    def test_ten_frames_sort_numerically_not_lexicographically(self, tmp_path):
        """The failure this whole module is most likely to have shipped with.
        Plain string order puts frame10 between frame1 and frame2, which looks
        fine on a nine-frame test folder and scrambles every real one."""
        folder = make_folder(tmp_path / "seq", count=12)
        names = [p.name for p in sequence_files(folder)]
        assert names[:3] == ["frame1.png", "frame2.png", "frame3.png"]
        assert names[-1] == "frame12.png"

    def test_zero_padded_names_also_sort_correctly(self, tmp_path):
        folder = tmp_path / "seq"
        folder.mkdir()
        for i in (1, 2, 10, 11):
            Image.new("RGBA", (4, 4)).save(folder / f"shot_{i:04d}.png")
        assert [p.name for p in sequence_files(folder)] == [
            "shot_0001.png", "shot_0002.png", "shot_0010.png", "shot_0011.png",
        ]

    def test_mixed_case_names_do_not_split_into_two_runs(self, tmp_path):
        folder = tmp_path / "seq"
        folder.mkdir()
        for name in ("Frame2.png", "frame1.png", "FRAME3.png"):
            Image.new("RGBA", (4, 4)).save(folder / name)
        assert [p.name for p in sequence_files(folder)] == [
            "frame1.png", "Frame2.png", "FRAME3.png",
        ]

    def test_non_images_are_ignored(self, tmp_path):
        folder = make_folder(tmp_path / "seq", count=2)
        (folder / "notes.txt").write_text("not a frame")
        assert len(sequence_files(folder)) == 2

    def test_subfolders_are_not_walked(self, tmp_path):
        """Shallow on purpose: a nested folder is somebody's unrelated
        structure, and hoovering up a thumbnails/ directory is hard to notice
        and annoying to undo."""
        folder = make_folder(tmp_path / "seq", count=2)
        make_folder(folder / "thumbnails", count=5)
        assert len(sequence_files(folder)) == 2


class TestReading:
    def test_it_reads_a_folder_as_frames(self, tmp_path):
        doc = read_sequence(make_folder(tmp_path / "seq", count=4), delay_ms=80)
        assert len(doc.frames) == 4
        assert doc.size == (10, 8)
        assert all(f.duration_ms == 80 for f in doc.frames)

    def test_frames_are_rgba_whatever_arrived(self, tmp_path):
        folder = tmp_path / "seq"
        folder.mkdir()
        Image.new("RGB", (6, 6), (10, 20, 30)).save(folder / "a.png")
        Image.new("L", (6, 6), 128).save(folder / "b.png")
        doc = read_sequence(folder)
        assert all(f.image.mode == "RGBA" for f in doc.frames)

    def test_mismatched_sizes_pad_to_the_union(self, tmp_path):
        """Padded, not scaled. This editor is aimed at pixel art, and silently
        resampling someone's pixels to make an import succeed is worse than the
        import looking odd."""
        folder = tmp_path / "seq"
        folder.mkdir()
        Image.new("RGBA", (10, 6), (255, 0, 0, 255)).save(folder / "a.png")
        Image.new("RGBA", (4, 12), (0, 255, 0, 255)).save(folder / "b.png")
        doc = read_sequence(folder)
        assert doc.size == (10, 12)

    def test_a_padded_frame_keeps_its_origin_and_its_pixels(self, tmp_path):
        folder = tmp_path / "seq"
        folder.mkdir()
        Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(folder / "a.png")
        Image.new("RGBA", (4, 4), (0, 255, 0, 255)).save(folder / "b.png")
        doc = read_sequence(folder)
        small = doc.frames[1].image
        assert small.getpixel((0, 0)) == (0, 255, 0, 255)    # top-left preserved
        assert small.getpixel((3, 3)) == (0, 255, 0, 255)    # its own last pixel
        assert small.getpixel((5, 5)) == (0, 0, 0, 0)        # padding is transparent

    def test_the_delay_is_quantised_like_every_other_duration(self, tmp_path):
        doc = read_sequence(make_folder(tmp_path / "seq", count=2), delay_ms=103)
        assert doc.frames[0].duration_ms == 100

    def test_an_empty_folder_is_an_error_not_an_empty_document(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ValueError):
            read_sequence(empty)

    def test_a_file_is_not_a_folder(self, tmp_path):
        target = tmp_path / "a.png"
        Image.new("RGBA", (4, 4)).save(target)
        with pytest.raises(ValueError):
            read_sequence(target)


class TestWriting:
    def test_it_writes_one_png_per_frame(self, tmp_path):
        out = tmp_path / "out"
        written = write_sequence(make_doc(3), out)
        assert len(written) == 3
        assert all(p.exists() and p.suffix == ".png" for p in written)

    def test_names_are_zero_padded_so_naive_sorts_agree(self, tmp_path):
        """Our own reader natural-sorts, so this isn't for us -- it is for every
        file manager, shell and script that doesn't."""
        written = write_sequence(make_doc(12), tmp_path / "out")
        assert written[0].name == "frame_0001.png"
        assert written[-1].name == "frame_0012.png"
        assert [p.name for p in written] == sorted(p.name for p in written)

    def test_it_creates_the_folder_if_needed(self, tmp_path):
        out = tmp_path / "nested" / "deeper"
        write_sequence(make_doc(2), out)
        assert out.is_dir()

    def test_it_writes_a_manifest(self, tmp_path):
        out = tmp_path / "out"
        write_sequence(make_doc(2, durations=[100, 250]), out)
        data = json.loads((out / MANIFEST_NAME).read_text())
        assert data["version"] == MANIFEST_VERSION
        assert [f["duration_ms"] for f in data["frames"]] == [100, 250]


class TestRoundTrip:
    def test_pixels_and_timing_survive_a_round_trip(self, tmp_path):
        """The property GIF cannot offer (§18): identical frames stay separate,
        and durations come back exactly as authored."""
        doc = make_doc(4, durations=[100, 100, 250, 40])
        write_sequence(doc, tmp_path / "out")
        back = read_sequence(tmp_path / "out")
        assert [f.duration_ms for f in back.frames] == [100, 100, 250, 40]
        assert [f.image.tobytes() for f in back.frames] == \
            [f.image.tobytes() for f in doc.frames]

    def test_identical_consecutive_frames_are_not_merged(self, tmp_path):
        """A GIF encoder folds these into one longer frame (risk 2). A folder
        has no reason to, which is the whole point of having the format."""
        image = Image.new("RGBA", (6, 6), (1, 2, 3, 255))
        doc = Document(tuple(Frame.new(image.copy(), 100) for _ in range(4)), (6, 6))
        write_sequence(doc, tmp_path / "out")
        assert len(read_sequence(tmp_path / "out").frames) == 4

    def test_loop_survives(self, tmp_path):
        doc = Document(make_doc(2).frames, (10, 8), loop=3)
        write_sequence(doc, tmp_path / "out")
        assert read_sequence(tmp_path / "out").loop == 3

    def test_the_manifest_beats_the_supplied_default(self, tmp_path):
        """A manifest is the folder telling us what it is; the delay argument is
        us telling the folder. The folder wins."""
        write_sequence(make_doc(2, durations=[500, 500]), tmp_path / "out")
        back = read_sequence(tmp_path / "out", delay_ms=20)
        assert [f.duration_ms for f in back.frames] == [500, 500]


class TestManifestFailures:
    def test_a_future_version_is_refused_rather_than_guessed(self, tmp_path):
        out = tmp_path / "out"
        write_sequence(make_doc(2), out)
        data = json.loads((out / MANIFEST_NAME).read_text())
        data["version"] = MANIFEST_VERSION + 1
        (out / MANIFEST_NAME).write_text(json.dumps(data))
        with pytest.raises(ManifestError):
            read_sequence(out)

    def test_a_manifest_naming_a_missing_file_is_an_error(self, tmp_path):
        out = tmp_path / "out"
        write_sequence(make_doc(2), out)
        data = json.loads((out / MANIFEST_NAME).read_text())
        data["frames"].append({"file": "ghost.png", "duration_ms": 100})
        (out / MANIFEST_NAME).write_text(json.dumps(data))
        with pytest.raises(ManifestError):
            read_sequence(out)

    def test_unparseable_json_is_an_error_not_a_silent_fallback(self, tmp_path):
        folder = make_folder(tmp_path / "seq", count=2)
        (folder / MANIFEST_NAME).write_text("{ not json")
        with pytest.raises(ManifestError):
            read_sequence(folder)

    def test_no_manifest_at_all_is_perfectly_normal(self, tmp_path):
        """The common case: a folder of PNGs from anywhere else."""
        doc = read_sequence(make_folder(tmp_path / "seq", count=3), delay_ms=60)
        assert len(doc.frames) == 3
        assert doc.frames[0].duration_ms == 60


class TestRegistry:
    def test_a_folder_dispatches_to_the_sequence_format(self, tmp_path):
        folder = make_folder(tmp_path / "seq", count=1)
        assert format_for(folder, readable=True).id == "sequence"

    def test_a_gif_still_dispatches_to_gif(self, tmp_path):
        assert format_for(tmp_path / "x.gif", readable=True).id == "gif"

    def test_an_unknown_extension_dispatches_to_nothing(self, tmp_path):
        assert format_for(tmp_path / "x.psd", readable=True) is None

    def test_a_suffixless_path_that_does_not_exist_reads_as_a_folder(self, tmp_path):
        """An export target the user is about to create."""
        assert format_for(tmp_path / "new_frames", writable=True).id == "sequence"

    def test_an_extension_wins_over_the_folder_format(self, tmp_path):
        """`out.gif` is a file even though it doesn't exist yet, or the suffix
        the user typed would mean nothing."""
        assert format_for(tmp_path / "out.gif", writable=True).id == "gif"

    def test_reader_and_writer_lookups_still_work(self, tmp_path):
        assert reader_for(tmp_path / "x.gif") is not None
        assert writer_for(make_folder(tmp_path / "seq", count=1)) is not None

    def test_the_open_filter_lists_only_file_formats(self):
        """A folder is chosen through a directory picker, which has no use for
        patterns -- the same fact as the old dict not being able to key one."""
        patterns = open_filter()[0][1]
        assert "*.gif" in patterns

    def test_an_unavailable_format_is_hidden_everywhere(self, monkeypatch):
        """The guarantee carried over from the dict: a format whose optional
        dependency is missing must not break startup, and must not advertise
        itself either. M5's video import is the first real customer."""
        import giflite.core.io as io_mod
        from dataclasses import replace as dc_replace

        from pathlib import Path

        missing = dc_replace(
            io_mod.FORMATS[0], id="ghost", extensions=(".ghost",),
            available=lambda: False)
        monkeypatch.setattr(io_mod, "FORMATS", io_mod.FORMATS + (missing,))
        assert all(f.id != "ghost" for f in formats(readable=True))
        assert ".ghost" not in open_filter()[0][1]
        assert format_for(Path("x.ghost"), readable=True) is None

    def test_an_available_format_is_found_by_the_same_route(self, monkeypatch):
        """The other half of the check above: without it, the previous test
        passes just as well against a registry that finds nothing at all."""
        import giflite.core.io as io_mod
        from dataclasses import replace as dc_replace
        from pathlib import Path

        present = dc_replace(io_mod.FORMATS[0], id="ghost", extensions=(".ghost",))
        monkeypatch.setattr(io_mod, "FORMATS", io_mod.FORMATS + (present,))
        assert format_for(Path("x.ghost"), readable=True).id == "ghost"
        assert ".ghost" in open_filter()[0][1]

    def test_read_params_are_declared_by_the_format_not_the_ui(self, tmp_path):
        """So a frontend can generate an options dialog without knowing what
        format it is talking to -- and a video importer's fps arrives free."""
        fmt = format_for(make_folder(tmp_path / "seq", count=1), readable=True)
        assert [p.name for p in fmt.read_params] == ["delay_ms", "loop"]
