"""Declarative operation parameters.

An operation that needs input (a resize width, a frame delay, a rotation
angle) declares its parameters as data. A frontend walks that data and builds
whatever input UI it likes; the value comes back through `coerce`, which turns
a raw widget string into a clean, bounded, typed value. The op never parses
anything.

Deferred through M0-M3 on purpose -- until several ops had plural options,
this would have been machinery in search of a use (ARCHITECTURE.md 6). M4 is
where it earns its place: resize, set-delay, scale-speed and rotate all need
it, and it retires the one hand-written dialog from M2.

Four types, and no more until an op genuinely can't be expressed in them.
Widening the schema in anticipation is the same mistake as building it early.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Param:
    # Base fields shared by every param. Each concrete subclass adds a
    # `default` field (and type-specific bounds). `default` is deliberately
    # NOT declared here as a property -- a property is a data descriptor and
    # would shadow the subclasses' `default` field, so `param.default` would
    # call the base instead of reading the value. Left to the subclasses.
    name: str  # the keyword passed to Operation.apply
    label: str  # human text for the input's label

    def coerce(self, raw: Any) -> Any:
        raise NotImplementedError


@dataclass(frozen=True)
class IntParam(Param):
    default: int = 0
    min: int | None = None
    max: int | None = None
    unit: str = ""  # e.g. "ms", "px" -- shown by the UI, ignored by logic

    def coerce(self, raw: Any) -> int:
        try:
            value = int(round(float(raw)))
        except (TypeError, ValueError):
            value = self.default
        return _clamp(value, self.min, self.max)


@dataclass(frozen=True)
class FloatParam(Param):
    default: float = 0.0
    min: float | None = None
    max: float | None = None
    unit: str = ""

    def coerce(self, raw: Any) -> float:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = self.default
        return _clamp(value, self.min, self.max)


@dataclass(frozen=True)
class BoolParam(Param):
    default: bool = False

    def coerce(self, raw: Any) -> bool:
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)


@dataclass(frozen=True)
class ChoiceParam(Param):
    # (visible label, actual value) pairs. The value is what apply() receives,
    # so choices can map friendly text to angles, enums, whatever.
    choices: tuple[tuple[str, Any], ...] = ()
    default: Any = None

    def coerce(self, raw: Any) -> Any:
        for label, value in self.choices:
            if raw == label or raw == value:
                return value
        return self.default

    def label_for(self, value: Any) -> str:
        for label, v in self.choices:
            if v == value:
                return label
        return str(value)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(label for label, _ in self.choices)


def _clamp(value, lo, hi):
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def collect_defaults(params: tuple[Param, ...]) -> dict[str, Any]:
    """The starting value set for a params tuple -- what a dialog opens with."""
    return {p.name: p.default for p in params}
