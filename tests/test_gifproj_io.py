"""The .gifproj container: lossless round trips, refusals, and the registry.

The format exists for exactly the things GIF cannot hold (ARCHITECTURE.md 18):
identical consecutive frames held separately, partial alpha, timing as
authored. So most checks here assert *exactness* -- byte-identical pixels,
equal durations -- because "close" is what the GIF path already offers, and a
project format that is merely close is a second lossy format wearing a
reassuring name.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from giflite.core.io import (
    format_for,
    formats,
    is_lossless,
    open_filter,
    save_filter,
)
from giflite.core.io.gif_read import read_gif
from giflite.core.io.gif_write import write_gif
from giflite.core.io.gifproj import read_gifproj, write_gifproj
from giflite.core.io.manifest import MANIFEST_NAME, MANIFEST_VERSION, ManifestError
from giflite.core.io.sequence import write_sequence
from giflite.core.model import Document, Frame


def make_doc(count=3, size=(10, 8), durations=None, loop=0):
    durations = durations or [100] * count
    frames = tuple(
        Frame.new(Image.new("RGBA", size, (i * 30 % 256, 17, 80, 255)), d)
        for i, d in enumerate(durations)
    )
    return Document(frames, size, loop=loop)


def frame_bytes(doc: Document) -> list[bytes]:
    return [f.image.tobytes() for f in doc.frames]


class TestRoundTrip:
    def test_pixels_survive_byte_identical(self, tmp_path: Path):
        doc = make_doc(count=4)
        out = tmp_path / "a.gifproj"
        write_gifproj(doc, out)
        back = read_gifproj(out)
        assert back.size == doc.size
        assert len(back) == len(doc)
        assert frame_bytes(back) == frame_bytes(doc)

    def test_durations_and_loop_come_back_as_authored(self, tmp_path: Path):
        """20ms and 30ms are exactly the values a GIF round trip mangles --
        browsers clamp sub-20 and the reader defaults odd values -- so they are
        the ones worth pinning here."""
        doc = make_doc(count=4, durations=[20, 30, 250, 1000], loop=3)
        out = tmp_path / "a.gifproj"
        write_gifproj(doc, out)
        back = read_gifproj(out)
        assert [f.duration_ms for f in back.frames] == [20, 30, 250, 1000]
        assert back.loop == 3

    def test_identical_consecutive_frames_stay_separate(self, tmp_path: Path):
        """Risk 2, retired. A GIF save merges a held duplicate into one longer
        hold (unconditional in Pillow -- 12.4); the whole point of the project
        format is that it does not."""
        base = Image.new("RGBA", (6, 6), (200, 40, 40, 255))
        frames = (
            Frame.new(base.copy(), 100),
            Frame.new(base.copy(), 100),
            Frame.new(Image.new("RGBA", (6, 6), (40, 200, 40, 255)), 100),
        )
        doc = Document(frames, (6, 6))
        out = tmp_path / "held.gifproj"
        write_gifproj(doc, out)
        back = read_gifproj(out)
        assert len(back) == 3
        assert frame_bytes(back)[0] == frame_bytes(back)[1]

        # The contrast that motivates the format: the same document through
        # the GIF writer comes back one frame shorter.
        gif_out = tmp_path / "held.gif"
        write_gif(doc, gif_out)
        assert len(read_gif(gif_out)) == 2

    def test_the_declined_alpha_ramp_survives(self, tmp_path: Path):
        """The eraser-opacity investigation's ramp. Through a GIF save it comes
        back as 255,255,255,255,0,0,0,0 (1-bit alpha, cutoff 128) -- which is
        why that feature was declined. Through the project format it must come
        back untouched, because this format is the standing answer to that
        want."""
        ramp = [255, 223, 191, 159, 127, 95, 63, 31]
        im = Image.new("RGBA", (8, 1))
        for x, alpha in enumerate(ramp):
            im.putpixel((x, 0), (90, 90, 90, alpha))
        doc = Document((Frame.new(im, 100),), (8, 1))
        out = tmp_path / "ramp.gifproj"
        write_gifproj(doc, out)
        back = read_gifproj(out)
        assert [back.frames[0].image.getpixel((x, 0))[3] for x in range(8)] == ramp

    def test_a_reopened_project_reports_its_source_format(self, tmp_path: Path):
        out = tmp_path / "a.gifproj"
        write_gifproj(make_doc(), out)
        assert read_gifproj(out).meta["source_format"] == "gifproj"


class TestDeterminism:
    def test_saving_the_same_document_twice_gives_identical_bytes(self, tmp_path: Path):
        """Zip stamps members with the wall clock by default, which would make
        every save unique for no reason. With the fixed timestamp, a backup
        tool or a diff can tell "saved again" from "changed"."""
        doc = make_doc(count=3)
        a, b = tmp_path / "a.gifproj", tmp_path / "b.gifproj"
        write_gifproj(doc, a)
        write_gifproj(doc, b)
        assert a.read_bytes() == b.read_bytes()

    def test_members_carry_the_fixed_epoch_not_the_clock(self, tmp_path: Path):
        """The double-write check above cannot see wall-clock stamps on its
        own: zip time has 2-second resolution, so two writes in the same
        moment agree by luck. Assert on what was actually stored -- every
        member at zip's 1980 epoch -- which no fast test run can fake."""
        out = tmp_path / "a.gifproj"
        write_gifproj(make_doc(), out)
        with zipfile.ZipFile(out) as archive:
            stamps = {info.date_time for info in archive.infolist()}
        assert stamps == {(1980, 1, 1, 0, 0, 0)}

    def test_png_members_are_stored_not_deflated(self, tmp_path: Path):
        """PNG is already DEFLATE inside; compressing it again spends CPU to
        grow the file slightly. The manifest, being JSON, does deflate. Pinned
        because it is a claim in the writer's docstring, and a refactor that
        flips it would look like an improvement."""
        out = tmp_path / "a.gifproj"
        write_gifproj(make_doc(), out)
        with zipfile.ZipFile(out) as archive:
            kinds = {info.filename: info.compress_type for info in archive.infolist()}
        assert kinds.pop(MANIFEST_NAME) == zipfile.ZIP_DEFLATED
        assert set(kinds.values()) == {zipfile.ZIP_STORED}

    def test_an_unzipped_project_is_a_valid_exported_folder(self, tmp_path: Path):
        """25.4's claim, run in reverse: the container is the sequence export
        plus a zip and nothing else. Extracting it must therefore produce a
        folder the sequence machinery fully understands -- same manifest name,
        same member names, byte-identical manifest."""
        doc = make_doc(count=3, durations=[100, 250, 1000])
        container = tmp_path / "a.gifproj"
        write_gifproj(doc, container)
        unzipped = tmp_path / "unzipped"
        with zipfile.ZipFile(container) as archive:
            archive.extractall(unzipped)

        exported = tmp_path / "exported"
        write_sequence(doc, exported)
        assert (unzipped / MANIFEST_NAME).read_text() == (
            exported / MANIFEST_NAME).read_text()
        assert sorted(p.name for p in unzipped.iterdir()) == sorted(
            p.name for p in exported.iterdir())


class TestRefusals:
    def test_not_a_zip_is_refused_by_name(self, tmp_path: Path):
        bogus = tmp_path / "bogus.gifproj"
        bogus.write_bytes(b"GIF89a not actually a zip")
        with pytest.raises(ValueError, match="bogus.gifproj.*not a zip"):
            read_gifproj(bogus)

    def test_a_zip_without_a_manifest_is_somebody_elses(self, tmp_path: Path):
        """No improvising a document out of a manifest-less zip. A folder
        without a manifest is normal; a container without one is some other
        program's file, and opening it convincingly would be worse than
        refusing it."""
        out = tmp_path / "other.gifproj"
        buffer = io.BytesIO()
        Image.new("RGBA", (4, 4)).save(buffer, format="PNG")
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("frame_0001.png", buffer.getvalue())
        with pytest.raises(ManifestError, match=MANIFEST_NAME):
            read_gifproj(out)

    def test_a_manifest_listing_a_missing_member_names_it(self, tmp_path: Path):
        out = tmp_path / "short.gifproj"
        manifest = {
            "version": MANIFEST_VERSION,
            "loop": 0,
            "frames": [{"file": "ghost.png", "duration_ms": 100}],
        }
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest))
        with pytest.raises(ManifestError, match="ghost.png"):
            read_gifproj(out)

    def test_an_unknown_manifest_version_is_refused(self, tmp_path: Path):
        """A reader must refuse a version it does not know rather than guess --
        half-understanding a manifest produces wrong timing noticed three edits
        later. parse_manifest owns the rule; this pins that the container path
        actually routes through it."""
        out = tmp_path / "future.gifproj"
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr(MANIFEST_NAME, json.dumps({"version": 99, "frames": []}))
        with pytest.raises(ManifestError, match="version"):
            read_gifproj(out)

    def test_a_manifest_that_is_not_json_is_refused(self, tmp_path: Path):
        out = tmp_path / "mangled.gifproj"
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr(MANIFEST_NAME, "{not json")
        with pytest.raises(ManifestError, match="could not be read"):
            read_gifproj(out)


class TestHandMadeContainers:
    """The format is bigger than our writer: the manifest promises that a
    container can name and arrange its members however it likes, so a
    hand-assembled zip exercising those freedoms must read correctly."""

    @staticmethod
    def _png_bytes(color, size=(4, 4)) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGBA", size, color).save(buffer, format="PNG")
        return buffer.getvalue()

    def test_order_comes_from_the_manifest_not_the_zip(self, tmp_path: Path):
        """Members written backwards on purpose. The folder reader natural-sorts
        because a directory has no order worth trusting; a container's manifest
        is the order, and the zip's member sequence must mean nothing."""
        out = tmp_path / "reversed.gifproj"
        manifest = {
            "version": MANIFEST_VERSION,
            "loop": 0,
            "frames": [
                {"file": "first.png", "duration_ms": 100},
                {"file": "second.png", "duration_ms": 100},
            ],
        }
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("second.png", self._png_bytes((0, 255, 0, 255)))
            archive.writestr("first.png", self._png_bytes((255, 0, 0, 255)))
            archive.writestr(MANIFEST_NAME, json.dumps(manifest))
        back = read_gifproj(out)
        assert back.frames[0].image.getpixel((0, 0)) == (255, 0, 0, 255)
        assert back.frames[1].image.getpixel((0, 0)) == (0, 255, 0, 255)

    def test_members_may_live_in_subfolders(self, tmp_path: Path):
        out = tmp_path / "nested.gifproj"
        manifest = {
            "version": MANIFEST_VERSION,
            "loop": 0,
            "frames": [{"file": "frames/a.png", "duration_ms": 100}],
        }
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("frames/a.png", self._png_bytes((9, 9, 9, 255)))
            archive.writestr(MANIFEST_NAME, json.dumps(manifest))
        assert len(read_gifproj(out)) == 1

    def test_a_declared_canvas_pads_smaller_frames_top_left(self, tmp_path: Path):
        """The sequence reader's canvas rule, inherited: declared size wins and
        undersized frames are placed top-left on transparency, never scaled."""
        out = tmp_path / "declared.gifproj"
        manifest = {
            "version": MANIFEST_VERSION,
            "loop": 0,
            "canvas": {"width": 8, "height": 8},
            "frames": [{"file": "small.png", "duration_ms": 100}],
        }
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("small.png", self._png_bytes((7, 7, 7, 255), size=(4, 4)))
            archive.writestr(MANIFEST_NAME, json.dumps(manifest))
        back = read_gifproj(out)
        assert back.size == (8, 8)
        assert back.frames[0].image.getpixel((0, 0)) == (7, 7, 7, 255)
        assert back.frames[0].image.getpixel((7, 7)) == (0, 0, 0, 0)

    def test_without_a_declared_canvas_the_union_wins(self, tmp_path: Path):
        out = tmp_path / "union.gifproj"
        manifest = {
            "version": MANIFEST_VERSION,
            "loop": 0,
            "frames": [
                {"file": "wide.png", "duration_ms": 100},
                {"file": "tall.png", "duration_ms": 100},
            ],
        }
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("wide.png", self._png_bytes((1, 1, 1, 255), size=(9, 2)))
            archive.writestr("tall.png", self._png_bytes((2, 2, 2, 255), size=(3, 7)))
            archive.writestr(MANIFEST_NAME, json.dumps(manifest))
        assert read_gifproj(out).size == (9, 7)


class TestRegistry:
    def test_gifproj_claims_its_extension_both_ways(self):
        fmt = format_for(Path("x.gifproj"), readable=True)
        assert fmt is not None and fmt.id == "gifproj"
        assert format_for(Path("x.gifproj"), writable=True).id == "gifproj"

    def test_gif_still_claims_its_own(self):
        assert format_for(Path("x.gif"), readable=True).id == "gif"

    def test_the_dialogs_learned_it_without_being_told(self):
        """The registry is the one place that knows what exists; the filters
        are generated from it. A new format appearing in both dialogs with no
        UI change is that promise, checked."""
        animations = open_filter()[0][1]
        assert "*.gifproj" in animations
        labels = {label: patterns for label, patterns in save_filter()}
        assert labels.get("GIF Editor Lite project") == "*.gifproj"

    def test_losslessness_is_registry_data(self):
        """Save policy runs on this bit (warn-on-overwrite, the merge message),
        so it is pinned as data: GIF is the lossy one, both PNG-based formats
        are not."""
        flags = {fmt.id: fmt.lossless for fmt in formats()}
        assert flags == {"gif": False, "gifproj": True, "sequence": True}

    def test_is_lossless_answers_by_path(self):
        assert is_lossless(Path("a.gifproj")) is True
        assert is_lossless(Path("a.gif")) is False
        # Nothing claims it -> nothing promises to preserve it.
        assert is_lossless(Path("a.xyz")) is False
