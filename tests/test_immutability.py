"""The one invariant the type system can't enforce (ARCHITECTURE.md 5, risk 3).

`frozen=True` on Document and Frame protects the containers, but `Frame.image`
is a mutable Pillow object shared by reference across every history snapshot.
An operation that mutates an image in place would silently rewrite history.
These tests apply every op and assert the source frames' pixels are
byte-for-byte unchanged -- the cheapest high-value check in the suite.
"""

from __future__ import annotations

import pytest
from PIL import Image

from giflite.core.model import Document, Frame, Selection
from giflite.core.ops import all_ops, get_op


def doc(n: int = 5, size=(8, 8)) -> Document:
    frames = tuple(
        Frame.new(Image.new("RGBA", size, (i * 10, 20, 30, 255)), 100) for i in range(n)
    )
    return Document(frames=frames, size=size)


# (op_id, params) for every op, with a selection that actually exercises it.
CASES = [
    ("frames.delete", {}),
    ("frames.duplicate", {}),
    ("frames.duplicate", {"copies": 3}),
    ("frames.move", {"to": 4}),
    ("frames.reverse", {}),
    ("frames.trim", {}),
    ("timing.set_delay", {"delay_ms": 200}),
    ("timing.scale_speed", {"factor": 2.0}),
    ("canvas.resize", {"width": 4, "height": 4, "keep_aspect": False}),
    ("canvas.rotate", {"angle": "cw"}),
    ("canvas.flip", {"direction": "horizontal"}),
]


@pytest.mark.parametrize("op_id,params", CASES)
def test_op_does_not_mutate_source_pixels(op_id, params):
    d = doc(5)
    before = [f.image.tobytes() for f in d.frames]
    get_op(op_id).apply(d, Selection(frozenset({1, 2})), **params)
    after = [f.image.tobytes() for f in d.frames]
    assert before == after, f"{op_id} mutated a source frame's pixels in place"


@pytest.mark.parametrize("op_id,params", CASES)
def test_source_document_is_left_intact(op_id, params):
    d = doc(5)
    original_frames = d.frames
    get_op(op_id).apply(d, Selection(frozenset({1, 2})), **params)
    # The source tuple and its frame objects are unchanged; the op built new ones.
    assert d.frames is original_frames
    assert len(d.frames) == 5


def test_reverse_whole_document_leaves_source_pixels_intact():
    d = doc(4)
    before = [f.image.tobytes() for f in d.frames]
    get_op("frames.reverse").apply(d, Selection.empty())
    assert [f.image.tobytes() for f in d.frames] == before


def test_every_registered_op_is_covered_here():
    """If someone adds an op, this fails until they add it to CASES."""
    covered = {op_id for op_id, _ in CASES}
    registered = {op.id for op in all_ops()}
    assert registered <= covered, f"uncovered ops: {registered - covered}"
