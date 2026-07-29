"""A synchronous publish/subscribe bus.

No threads, no queue, no priorities. Callbacks run in subscription order,
inside the emitting call. Exceptions are deliberately not swallowed -- a
frontend bug that eats an event should be loud, not mysterious.

Event names are constants because a typo in a string literal is a silent
no-op subscription, which is exactly the kind of bug that costs an afternoon.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

# Emitted exactly once per mutation (op, undo, redo, open), carrying document,
# selection and playhead together. The single-event rule is part of the
# contract in ARCHITECTURE.md 9: if selection arrived separately, a frontend
# could restyle the timeline against the previous document and index past the
# end of it.
DOC_CHANGED = "doc_changed"  # (doc, selection, index, reason)

SELECTION_CHANGED = "selection_changed"  # (selection)  -- selection-only edits
# (region) -- the rectangular pixel selection, or None. Its own event rather
# than a field on SELECTION_CHANGED because the two are independent: a region
# outlives every frame you scrub past, and a frame selection outlives every
# region you draw. Folding them together would mean each one redrawing for the
# other's changes.
REGION_CHANGED = "region_changed"
PLAYHEAD_MOVED = "playhead_moved"  # (index)  -- playback tick or scrub
PLAYBACK_STATE = "playback_state"  # (playing)  -- play/pause, incl. auto-stop
TITLE_CHANGED = "title_changed"  # (path, dirty)  -- frontend formats the string
STATUS = "status"  # (message)
ERROR = "error"  # (exception, context)

Callback = Callable[..., None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callback]] = defaultdict(list)

    def on(self, event: str, callback: Callback) -> Callback:
        """Subscribe. Returns the callback so it can be used as a decorator."""
        self._subscribers[event].append(callback)
        return callback

    def off(self, event: str, callback: Callback) -> None:
        try:
            self._subscribers[event].remove(callback)
        except ValueError:
            pass

    def emit(self, event: str, **payload: Any) -> None:
        # Iterate a copy: a callback is allowed to unsubscribe itself.
        for callback in list(self._subscribers[event]):
            callback(**payload)

    def clear(self) -> None:
        self._subscribers.clear()
