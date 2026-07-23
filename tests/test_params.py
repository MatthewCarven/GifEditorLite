"""Param schema: coercion, clamping, and choice mapping."""

from __future__ import annotations

import pytest

from giflite.core.params import (
    BoolParam,
    ChoiceParam,
    FloatParam,
    IntParam,
    collect_defaults,
)


class TestIntParam:
    def test_parses_and_rounds(self):
        p = IntParam("w", "Width", default=10)
        assert p.coerce("250.7") == 251
        assert p.coerce(42) == 42

    def test_clamps_to_bounds(self):
        p = IntParam("w", "Width", default=10, min=1, max=100)
        assert p.coerce(9999) == 100
        assert p.coerce(-5) == 1

    def test_garbage_falls_back_to_default(self):
        p = IntParam("w", "Width", default=7)
        assert p.coerce("not a number") == 7
        assert p.coerce(None) == 7

    def test_default_is_readable_not_shadowed_by_base(self):
        # regression: a base-class `default` property would shadow this field
        assert IntParam("w", "W", default=123).default == 123


class TestFloatParam:
    def test_parses_and_clamps(self):
        p = FloatParam("s", "Speed", default=1.0, min=0.1, max=10.0)
        assert p.coerce("2.5") == 2.5
        assert p.coerce(99) == 10.0
        assert p.coerce(0.0) == 0.1

    def test_garbage_falls_back(self):
        assert FloatParam("s", "S", default=1.5).coerce("x") == 1.5


class TestBoolParam:
    @pytest.mark.parametrize("raw,expected", [
        ("yes", True), ("on", True), ("1", True), ("true", True),
        ("no", False), ("off", False), ("0", False), ("", False),
        (True, True), (False, False), (1, True), (0, False),
    ])
    def test_coerces(self, raw, expected):
        assert BoolParam("b", "B", default=False).coerce(raw) is expected


class TestChoiceParam:
    def test_maps_label_to_value(self):
        p = ChoiceParam("angle", "Rotate",
                        choices=(("90 CW", 90), ("180", 180)), default=90)
        assert p.coerce("180") == 180

    def test_accepts_the_value_directly(self):
        p = ChoiceParam("angle", "Rotate", choices=(("90 CW", 90),), default=90)
        assert p.coerce(90) == 90

    def test_unknown_falls_back_to_default(self):
        p = ChoiceParam("angle", "Rotate", choices=(("90 CW", 90),), default=90)
        assert p.coerce("nonsense") == 90

    def test_label_for_and_labels(self):
        p = ChoiceParam("a", "A", choices=(("CW", 90), ("Flip", 180)), default=90)
        assert p.label_for(180) == "Flip"
        assert p.labels == ("CW", "Flip")


def test_collect_defaults():
    params = (
        IntParam("w", "W", default=100),
        FloatParam("s", "S", default=2.0),
        BoolParam("keep", "Keep", default=True),
    )
    assert collect_defaults(params) == {"w": 100, "s": 2.0, "keep": True}
