"""Operation registry and the operation contract.

An operation is a pure function of (document, selection) -> (document,
selection), wrapped in a small object that also carries its id, menu label and
accelerator. Purity is what makes every op testable without a window and makes
snapshot undo cheap (core/history.py).

The two things every op must get right:

1. Never mutate `frame.image` in place. Build new frames and a new document.
   `frozen=True` does not enforce this (ARCHITECTURE.md 5); the byte-identity
   test in tests/test_immutability.py does.
2. Return the selection that should exist *after* the edit. Only the op knows
   the right answer -- what's selected after deleting frames 3-5 is a specific
   thing -- and returning it kills a whole class of stale-index bugs
   (ARCHITECTURE.md 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from giflite.core.model import Document, Selection
from giflite.core.params import Param


@dataclass(frozen=True, slots=True)
class OpResult:
    doc: Document
    selection: Selection
    # Where the playhead belongs afterwards, or None for "wherever the rule
    # puts it" (the controller sends it to `selection.first`).
    #
    # That rule is right for the ops it was written for -- after moving or
    # duplicating frames you want to be looking at what moved -- but it is a
    # rule about *frames that shifted*, and an op that edits pixels in place
    # shifts nothing. The painting ops have been working around it since M4 by
    # returning `Selection.single(index)`, which keeps the playhead put at the
    # cost of throwing away the user's frame selection. That trade is invisible
    # while an op edits one frame and unacceptable once one edits many: pasting
    # into frames 0-20 while standing on frame 7 must not yank the playhead to
    # frame 0, and must not leave only one frame selected either.
    #
    # So an op may now simply say where the playhead goes. Optional, because
    # every existing op is correct without it.
    index: int | None = None


@runtime_checkable
class Operation(Protocol):
    id: str  # dotted: the prefix is the menu group, e.g. "frames.delete"
    label: str  # menu text, e.g. "Delete Frames"
    accel: str | None  # "Ctrl+D" -- frontends translate to their own syntax
    needs_selection: bool  # greys the menu item out when the selection is empty
    in_menu: bool  # False for gesture-only ops like move
    params: tuple[Param, ...]  # inputs the UI collects before running; () = none

    def apply(self, doc: Document, sel: Selection, **params) -> OpResult: ...


def op_params(op: "Operation") -> tuple[Param, ...]:
    """An op's params, tolerant of ops that predate the attribute."""
    return getattr(op, "params", ())


def op_label(op: "Operation", **params) -> str:
    """What to call this run of the op -- in "Undo X", and in its messages.

    An op may expose `label_for(**params)` when the same op does materially
    different things depending on its arguments; otherwise the static `label`
    stands. Same optional-hook shape as `default_params` below, and for the same
    reason: the general case is a constant, and the exception should cost one
    method on the op that has it rather than a mechanism everything pays for.

    Earns its place with erase mode. `paint.fill` filling and `paint.fill`
    clearing are one op with one mask generator -- correctly so -- but "Undo
    Fill" after removing pixels describes the implementation and misdescribes
    what the user did, and the undo menu is the one place they look to find out.
    """
    dynamic = getattr(op, "label_for", None)
    if dynamic is not None:
        return dynamic(**params)
    return op.label


def op_defaults(op: "Operation", doc: Document, sel: Selection) -> dict:
    """Initial values for an op's dialog.

    An op may expose `default_params(doc, sel)` to seed the dialog from the
    current document (e.g. Resize pre-filling the current size); otherwise the
    static `Param.default`s are used.
    """
    dynamic = getattr(op, "default_params", None)
    if dynamic is not None:
        return dynamic(doc, sel)
    return {p.name: p.default for p in op_params(op)}


_OPS: dict[str, Operation] = {}


def register_op(cls):
    """Class decorator: instantiate the op and register it by id.

    Menus, keybindings and any future command palette all read from the
    registry, so adding an op is one decorated class and zero edits elsewhere.
    """
    inst = cls()
    if inst.id in _OPS:
        raise ValueError(f"duplicate operation id {inst.id!r}")
    _OPS[inst.id] = inst
    return cls


def get_op(op_id: str) -> Operation | None:
    return _OPS.get(op_id)


def all_ops() -> list[Operation]:
    """Every registered op, in registration order (which is menu order)."""
    return list(_OPS.values())


def menu_groups() -> dict[str, list[Operation]]:
    """Ops grouped by the prefix of their id, for building menus.

    "frames.delete" and "frames.trim" land together under "frames". Order
    within a group follows registration order.
    """
    groups: dict[str, list[Operation]] = {}
    for op in _OPS.values():
        if getattr(op, "in_menu", True):
            groups.setdefault(op.id.split(".", 1)[0], []).append(op)
    return groups
