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


@dataclass(frozen=True, slots=True)
class OpResult:
    doc: Document
    selection: Selection


@runtime_checkable
class Operation(Protocol):
    id: str  # dotted: the prefix is the menu group, e.g. "frames.delete"
    label: str  # menu text, e.g. "Delete Frames"
    accel: str | None  # "Ctrl+D" -- frontends translate to their own syntax
    needs_selection: bool  # greys the menu item out when the selection is empty
    in_menu: bool  # False for gesture-only ops like move

    def apply(self, doc: Document, sel: Selection, **params) -> OpResult: ...


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
