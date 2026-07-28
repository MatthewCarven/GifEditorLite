"""Saving through the controller: dirty clears, path updates, events fire."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from giflite.app import events as ev
from giflite.app.controller import AppController
from giflite.core.io.gif_read import read_gif
from giflite.core.model import Selection
from tests.conftest import make_gif
from tests.fake_frontend import FakeFrontend


def _write_many_colour_gif(path: Path, frames: int = 3, size: int = 48) -> Path:
    """A GIF our own writer cannot reproduce byte-for-byte.

    `make_gif` art is a handful of flat colours, and re-encoding it lands on the
    same palette and the same LZW stream -- fine for most tests, useless for
    proving a write did *not* happen. Per-pixel gradients re-quantise differently.
    """
    images = []
    for f in range(frames):
        im = Image.new("RGB", (size, size))
        for y in range(size):
            for x in range(size):
                im.putpixel((x, y), ((x * 5 + f * 9) % 256, (y * 5) % 256, (x * y) % 256))
        images.append(im.convert("P", palette=Image.ADAPTIVE))
    images[0].save(path, save_all=True, append_images=images[1:], duration=100, loop=0)
    return path


@pytest.fixture
def loaded(tmp_path: Path):
    controller = AppController()
    frontend = FakeFrontend().attach(controller)
    make_gif(tmp_path / "a.gif", frames=5)
    controller.open(tmp_path / "a.gif")
    frontend.clear()
    return controller, frontend, tmp_path


class TestSaveAs:
    def test_writes_a_file(self, loaded):
        controller, _, tmp_path = loaded
        out = tmp_path / "out.gif"
        assert controller.save_as(out) is True
        assert out.exists()
        assert len(read_gif(out)) == 5

    def test_updates_the_path(self, loaded):
        controller, _, tmp_path = loaded
        out = tmp_path / "renamed.gif"
        controller.save_as(out)
        assert controller.path == out

    def test_announces_the_save(self, loaded):
        controller, frontend, tmp_path = loaded
        controller.save_as(tmp_path / "out.gif")
        assert "Saved out.gif" in frontend.last(ev.STATUS).payload["message"]


class TestDirtyOnSave:
    def test_saving_clears_dirty(self, loaded):
        controller, _, tmp_path = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        assert controller.dirty
        controller.save_as(tmp_path / "out.gif")
        assert not controller.dirty

    def test_title_event_reflects_clean_after_save(self, loaded):
        controller, frontend, tmp_path = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        frontend.clear()
        controller.save_as(tmp_path / "out.gif")
        assert frontend.last(ev.TITLE_CHANGED).payload["dirty"] is False

    def test_editing_after_save_is_dirty_again(self, loaded):
        controller, _, tmp_path = loaded
        controller.save_as(tmp_path / "out.gif")
        assert not controller.dirty
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        assert controller.dirty

    def test_undo_back_to_saved_state_is_clean(self, loaded):
        controller, _, tmp_path = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        controller.save_as(tmp_path / "out.gif")  # saved after one edit
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.delete")
        assert controller.dirty
        controller.undo()  # back to the saved state
        assert not controller.dirty


class TestSaveInPlace:
    def test_save_uses_the_current_path(self, loaded):
        controller, _, tmp_path = loaded
        assert controller.has_path
        assert controller.save() is True

    def test_save_without_a_document_is_a_noop(self):
        controller = AppController()
        assert controller.has_path is False
        assert controller.save() is False

    def test_merge_is_reported_when_saving_a_held_duplicate(self, loaded):
        controller, frontend, tmp_path = loaded
        controller.set_selection(Selection.single(2))
        controller.run_op("frames.duplicate")  # creates an identical held pair
        frontend.clear()
        controller.save_as(tmp_path / "held.gif")
        assert "merged" in frontend.last(ev.STATUS).payload["message"]


class TestBadTarget:
    def test_unknown_extension_reports_an_error(self, loaded):
        controller, frontend, tmp_path = loaded
        assert controller.save_as(tmp_path / "out.xyz") is False
        assert frontend.count(ev.ERROR) == 1


class TestOverwritesSource:
    """The fact a frontend needs to warn before Save re-encodes the original.

    GIF write-back rebuilds the palette and merges identical consecutive frames,
    so an in-place save destroys the file that was opened. The controller only
    reports whether that's what a Save would do; the warning itself is UI policy.
    """

    def test_a_freshly_opened_file_is_the_source(self, loaded):
        controller, _, _ = loaded
        assert controller.overwrites_source is True

    def test_saving_elsewhere_clears_it(self, loaded):
        controller, _, tmp_path = loaded
        controller.save_as(tmp_path / "out.gif")
        assert controller.overwrites_source is False

    def test_overwriting_the_source_clears_it_too(self, loaded):
        """Warn once, not on every Ctrl+S: after the first overwrite there is no
        untouched original left to protect."""
        controller, _, tmp_path = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")  # a clean Save writes nothing
        controller.save()
        assert controller.overwrites_source is False

    def test_a_skipped_save_leaves_the_source_flag_alone(self, loaded):
        """The flag tracks whether an untouched original is still out there, so
        a Save that wrote nothing must not clear it."""
        controller, _, _ = loaded
        controller.save()
        assert controller.overwrites_source is True

    def test_no_document_never_overwrites_anything(self):
        assert AppController().overwrites_source is False

    def test_closing_resets_it(self, loaded):
        controller, _, _ = loaded
        controller.close()
        assert controller.overwrites_source is False

    def test_reopening_makes_the_new_file_a_source_again(self, loaded):
        controller, _, tmp_path = loaded
        controller.save_as(tmp_path / "out.gif")
        assert controller.overwrites_source is False
        make_gif(tmp_path / "b.gif", frames=3)
        controller.open(tmp_path / "b.gif")
        assert controller.overwrites_source is True


class TestCleanSaveIsSkipped:
    """Ctrl+S with nothing to save used to re-encode the file anyway.

    Over an untouched original that is pure loss -- rebuilt palette, merged
    holds -- paid for no change at all. The controller declines rather than
    leaving it to each frontend to remember (ARCHITECTURE.md 19.2).
    """

    def test_a_freshly_opened_document_has_nothing_to_save(self, loaded):
        controller, _, _ = loaded
        assert controller.save_would_change_nothing is True

    def test_an_edit_gives_it_something_to_save(self, loaded):
        controller, _, _ = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        assert controller.save_would_change_nothing is False

    def test_the_writer_is_never_reached(self, loaded, monkeypatch):
        """The assertion that can't be fooled: no encode is attempted at all.

        Comparing bytes is not enough on its own -- see the test below for why.
        """
        controller, _, _ = loaded
        calls = []
        monkeypatch.setattr(
            "giflite.app.controller.writer_for",
            lambda path: lambda doc, p: calls.append(p),
        )
        assert controller.save() is True  # reported as success, not failure
        assert calls == []

    def test_the_original_bytes_survive_a_pointless_save(self, tmp_path: Path):
        """And the file on disk is genuinely untouched.

        Deliberately not the shared `loaded` fixture. Its GIF is simple enough
        that our writer reproduces it byte-for-byte, so this assertion would pass
        against a broken build -- decoration. A many-colour frame re-quantises to
        different bytes, which is the case the skip actually protects.
        """
        source = tmp_path / "grad.gif"
        _write_many_colour_gif(source)
        before = source.read_bytes()
        controller = AppController()
        controller.open(source)
        assert controller.save() is True
        assert source.read_bytes() == before

    def test_it_says_so(self, loaded):
        controller, frontend, _ = loaded
        controller.save()
        assert frontend.last(ev.STATUS).payload["message"] == "No changes to save"

    def test_an_edited_document_really_is_written(self, loaded):
        controller, _, tmp_path = loaded
        source = tmp_path / "a.gif"
        before = source.read_bytes()
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        assert controller.save() is True
        assert source.read_bytes() != before
        # The duplicate is identical to its neighbour, so the encoder folds the
        # pair into one longer hold rather than a sixth frame (ARCHITECTURE 12.4).
        back = read_gif(source)
        assert len(back) == 5
        assert back.frames[0].duration_ms == 200

    def test_saving_again_right_after_a_real_save_is_skipped(self, loaded):
        """Save clears dirty, so the second Ctrl+S has nothing left to do."""
        controller, _, tmp_path = loaded
        controller.set_selection(Selection.single(0))
        controller.run_op("frames.duplicate")
        out = tmp_path / "out.gif"
        controller.save_as(out)
        after_first = out.read_bytes()
        assert controller.save_would_change_nothing is True
        controller.save()
        assert out.read_bytes() == after_first

    def test_save_as_always_writes_even_with_no_edits(self, loaded):
        """Save As is a different request -- the user named a new destination."""
        controller, _, tmp_path = loaded
        out = tmp_path / "copy.gif"
        assert controller.save_as(out) is True
        assert out.exists()

    def test_nothing_to_save_needs_a_path(self):
        """With nowhere to write, Save must still fall through to Save As."""
        controller = AppController()
        assert controller.save_would_change_nothing is False
        assert controller.save() is False


class TestSuggestedSaveName:
    def test_steers_away_from_the_opened_original(self, loaded):
        controller, _, _ = loaded
        assert controller.suggested_save_name == "a_edited.gif"

    def test_keeps_the_name_once_it_is_ours(self, loaded):
        controller, _, tmp_path = loaded
        controller.save_as(tmp_path / "out.gif")
        assert controller.suggested_save_name == "out.gif"

    def test_does_not_stack_the_suffix(self, loaded):
        """Open a file already called *_edited and Save As must not offer
        'a_edited_edited.gif'."""
        controller, _, tmp_path = loaded
        make_gif(tmp_path / "a_edited.gif", frames=3)
        controller.open(tmp_path / "a_edited.gif")
        assert controller.suggested_save_name == "a_edited.gif"

    def test_preserves_the_extension(self, loaded):
        controller, _, tmp_path = loaded
        make_gif(tmp_path / "clip.gif", frames=2)
        controller.open(tmp_path / "clip.gif")
        assert controller.suggested_save_name.endswith(".gif")

    def test_falls_back_with_no_path(self):
        assert AppController().suggested_save_name == "untitled.gif"
