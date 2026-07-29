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
    ("canvas.crop", {"x": 1, "y": 1, "width": 4, "height": 4}),
    ("paint.stroke", {"index": 1, "points": ((2, 2), (5, 5)), "size": 2, "color": (255, 0, 0, 255)}),
    ("paint.erase", {"index": 2, "points": ((3, 3), (6, 6)), "size": 2}),
    # Fill is the one op that builds its mask by *reading* the frame it is about
    # to paint, so "did the source survive" is a sharper question here than for
    # a stroke, which never looks at the pixels underneath.
    ("paint.fill", {"index": 1, "x": 2, "y": 2, "color": (255, 0, 0, 255)}),
    ("paint.fill", {"index": 1, "x": 2, "y": 2, "color": (255, 0, 0, 255), "tolerance": 32}),
    ("paint.shape", {"index": 1, "kind": "rect", "x0": 1, "y0": 1, "x1": 5, "y1": 5,
                     "size": 2, "color": (255, 0, 0, 255)}),
    ("paint.shape", {"index": 2, "kind": "ellipse", "x0": 1, "y0": 1, "x1": 5, "y1": 5,
                     "size": 1, "color": (0, 255, 0, 255), "filled": True}),
    ("paint.shape", {"index": 2, "kind": "line", "x0": 0, "y0": 0, "x1": 6, "y1": 6,
                     "size": 3, "color": (0, 0, 255, 255)}),
    ("paint.cut", {"index": 1, "x": 2, "y": 2, "width": 4, "height": 4}),
    # Paste is the first op to write pixels into *several* frames from one call,
    # so "did every source frame survive" is a question with more than one
    # answer here -- and the clipboard image is a source too. Both frames in the
    # selection are targets, deliberately.
    ("paint.paste", {"index": 1, "frames": (1, 2),
                     "image": Image.new("RGBA", (3, 3), (9, 9, 9, 255)),
                     "x": 2, "y": 2}),
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
