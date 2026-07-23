"""Playback through the controller, driven by the fake frontend.

The controller is the thing the frontend timer talks to, so these assert the
play/pause/tick/seek contract and the events that go with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from giflite.app import events as ev
from giflite.app.controller import AppController
from tests.conftest import make_gif
from tests.fake_frontend import FakeFrontend


@pytest.fixture
def loaded(tmp_path: Path):
    controller = AppController()
    frontend = FakeFrontend().attach(controller)
    # 4 frames, 100ms each, looping forever
    make_gif(tmp_path / "a.gif", frames=4, durations=[100, 100, 100, 100], loop=0)
    controller.open(tmp_path / "a.gif")
    frontend.clear()
    return controller, frontend


class TestPlayPause:
    def test_play_sets_playing_and_announces_it(self, loaded):
        controller, frontend = loaded
        controller.play()
        assert controller.playing is True
        assert frontend.last(ev.PLAYBACK_STATE).payload == {"playing": True}

    def test_pause_stops_and_announces(self, loaded):
        controller, frontend = loaded
        controller.play()
        frontend.clear()
        controller.pause()
        assert controller.playing is False
        assert frontend.last(ev.PLAYBACK_STATE).payload == {"playing": False}

    def test_toggle_flips_state(self, loaded):
        controller, _ = loaded
        controller.toggle_play()
        assert controller.playing
        controller.toggle_play()
        assert not controller.playing

    def test_play_is_idempotent(self, loaded):
        controller, frontend = loaded
        controller.play()
        controller.play()
        assert frontend.count(ev.PLAYBACK_STATE) == 1

    def test_cannot_play_a_single_frame(self, tmp_path: Path):
        controller = AppController()
        frontend = FakeFrontend().attach(controller)
        make_gif(tmp_path / "one.gif", frames=1)
        controller.open(tmp_path / "one.gif")
        frontend.clear()
        controller.play()
        assert controller.playing is False
        assert frontend.count(ev.PLAYBACK_STATE) == 0


class TestTick:
    def test_a_paused_tick_does_nothing(self, loaded):
        controller, frontend = loaded
        controller.tick(1000)
        assert controller.index == 0
        assert frontend.count(ev.PLAYHEAD_MOVED) == 0

    def test_playing_ticks_advance_the_playhead(self, loaded):
        controller, frontend = loaded
        controller.play()
        controller.tick(100)
        assert controller.index == 1
        assert frontend.last(ev.PLAYHEAD_MOVED).payload == {"index": 1}

    def test_playhead_event_only_on_change(self, loaded):
        controller, frontend = loaded
        controller.play()
        frontend.clear()
        controller.tick(40)  # still on frame 0
        controller.tick(40)  # 80ms, still frame 0
        assert frontend.count(ev.PLAYHEAD_MOVED) == 0
        controller.tick(40)  # 120ms -> frame 1
        assert frontend.count(ev.PLAYHEAD_MOVED) == 1

    def test_a_stall_does_not_fast_forward_the_whole_gif(self, loaded):
        controller, _ = loaded
        controller.play()
        # 10 seconds in one tick would cross 100 frames uncapped. The 250ms cap
        # keeps it to ~2.5 frames of travel -> lands on frame 2, not frame 99.
        controller.tick(10_000)
        assert controller.index == 2


class TestAutoStop:
    def test_a_non_looping_gif_stops_on_the_last_frame(self, tmp_path: Path):
        controller = AppController()
        frontend = FakeFrontend().attach(controller)
        make_gif(tmp_path / "once.gif", frames=3, durations=[100, 100, 100], loop=None)
        controller.open(tmp_path / "once.gif")
        assert controller._clock.loop == 1  # play once
        frontend.clear()

        controller.play()
        for _ in range(6):
            controller.tick(100)

        assert controller.index == 2
        assert controller.playing is False
        # the final PLAYBACK_STATE the frontend sees is "stopped"
        assert frontend.last(ev.PLAYBACK_STATE).payload == {"playing": False}

    def test_pressing_play_after_the_end_rewinds(self, tmp_path: Path):
        controller = AppController()
        FakeFrontend().attach(controller)
        make_gif(tmp_path / "once.gif", frames=3, loop=None)
        controller.open(tmp_path / "once.gif")
        controller.play()
        for _ in range(6):
            controller.tick(100)
        assert controller.index == 2  # parked at the end

        controller.play()
        assert controller.playing is True
        assert controller.index == 0  # rewound


class TestScrubInteraction:
    def test_seek_keeps_the_clock_in_step(self, loaded):
        controller, _ = loaded
        controller.play()
        controller.seek(2)
        # after scrubbing to frame 2, the next 100ms should land on 3, not
        # somewhere stale from the clock's previous position
        controller.tick(100)
        assert controller.index == 3

    def test_step_pauses_and_moves_one_frame(self, loaded):
        controller, frontend = loaded
        controller.play()
        controller.step(1)
        assert controller.playing is False
        assert controller.index == 1

    def test_step_clamps_at_the_ends(self, loaded):
        controller, _ = loaded
        controller.step(-5)
        assert controller.index == 0
        controller.seek(3)
        controller.step(10)
        assert controller.index == 3


class TestOpenResetsPlayback:
    def test_opening_while_playing_stops_playback(self, loaded, tmp_path: Path):
        controller, frontend = loaded
        controller.play()
        controller.open(make_gif(tmp_path / "b.gif", frames=3))
        assert controller.playing is False
