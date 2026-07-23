from __future__ import annotations

import pytest
from PIL import Image

from giflite.core.model import (
    MIN_DURATION_MS,
    Document,
    Frame,
    Selection,
    quantise_duration,
)


class TestQuantiseDuration:
    @pytest.mark.parametrize(
        "given,expected",
        [
            (100, 100),
            (125, 120),  # floors, matching Pillow's own behaviour on save
            (33, 30),
            (20, 20),  # the floor itself survives
            (17, MIN_DURATION_MS),  # below floor -> clamp to floor, NOT jump to 100
            (5, MIN_DURATION_MS),
            (0, MIN_DURATION_MS),
        ],
    )
    def test_snaps_to_storable_values(self, given, expected):
        assert quantise_duration(given) == expected

    def test_is_monotonic_never_jumps_up(self):
        """The bug this guards: speeding a frame up (smaller ms) must never
        yield a *larger* duration. A smaller input -> a <= output, always."""
        prev = 0
        for value in range(0, 500, 7):
            out = quantise_duration(value)
            assert out >= prev or out == MIN_DURATION_MS
            prev = out
        # specifically, a value just under the floor clamps down, not up
        assert quantise_duration(12) == MIN_DURATION_MS

    def test_is_idempotent(self):
        for value in (0, 5, 33, 100, 125, 999):
            once = quantise_duration(value)
            assert quantise_duration(once) == once


class TestDocumentValidate:
    def test_accepts_a_well_formed_document(self, solid_doc):
        solid_doc().validate()

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="at least one frame"):
            Document(frames=(), size=(8, 8)).validate()

    def test_rejects_non_rgba(self):
        frame = Frame.new(Image.new("RGB", (8, 8)), 100)
        with pytest.raises(ValueError, match="expected 'RGBA'"):
            Document(frames=(frame,), size=(8, 8)).validate()

    def test_rejects_wrong_size(self):
        frame = Frame.new(Image.new("RGBA", (4, 4)), 100)
        with pytest.raises(ValueError, match="expected canvas size"):
            Document(frames=(frame,), size=(8, 8)).validate()

    def test_meta_cannot_be_mutated_through_the_document(self, solid_doc):
        doc = Document(frames=solid_doc().frames, size=(8, 8), meta={"a": 1})
        with pytest.raises(TypeError):
            doc.meta["b"] = 2  # type: ignore[index]


class TestFrame:
    def test_with_duration_keeps_the_image_uid(self):
        """Same pixels must keep the cache key, or thumbnails re-render."""
        original = Frame.new(Image.new("RGBA", (8, 8)), 100)
        retimed = original.with_duration(250)
        assert retimed.image_uid == original.image_uid
        assert retimed.image is original.image
        assert retimed.duration_ms == 250

    def test_new_frames_get_distinct_uids(self):
        a = Frame.new(Image.new("RGBA", (8, 8)), 100)
        b = Frame.new(Image.new("RGBA", (8, 8)), 100)
        assert a.image_uid != b.image_uid

    def test_sharing_pixels_reuses_the_uid(self):
        original = Frame.new(Image.new("RGBA", (8, 8)), 100)
        copy = original.sharing_pixels()
        assert copy is not original
        assert copy.image_uid == original.image_uid

    def test_equality_is_identity_not_pixels(self):
        """Two frames with identical pixels are still different frames.

        Value equality would make `doc_before == doc_after` a full buffer
        comparison; identity is both correct for undo and vastly cheaper.
        """
        a = Frame.new(Image.new("RGBA", (8, 8), (1, 2, 3, 255)), 100)
        b = Frame.new(Image.new("RGBA", (8, 8), (1, 2, 3, 255)), 100)
        assert a != b
        assert a == a
        assert hash(a)  # identity semantics keep frames hashable


class TestSelection:
    def test_span_covers_both_ends_in_either_order(self):
        assert Selection.span(2, 4).ordered == (2, 3, 4)
        assert Selection.span(4, 2).ordered == (2, 3, 4)

    def test_toggle_adds_and_removes(self):
        sel = Selection.single(1)
        assert sel.toggled(3).ordered == (1, 3)
        assert sel.toggled(1).ordered == ()

    def test_clamped_drops_out_of_range_indices(self):
        assert Selection.span(2, 6).clamped(4).ordered == (2, 3)

    def test_clamped_repairs_a_dangling_anchor(self):
        sel = Selection(frozenset({1, 2}), anchor=9)
        clamped = sel.clamped(3)
        assert clamped.anchor in clamped.indices

    def test_empty_is_falsey(self):
        assert not Selection.empty()
        assert Selection.single(0)
