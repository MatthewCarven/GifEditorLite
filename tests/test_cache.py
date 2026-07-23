from __future__ import annotations

from PIL import Image

from giflite.app.cache import ThumbnailCache
from giflite.core.model import Frame


def frame(color: int, size=(80, 40)) -> Frame:
    return Frame.new(Image.new("RGBA", size, (color, 0, 0, 255)), 100)


class TestThumbnailCache:
    def test_thumbnail_has_the_requested_height_and_kept_aspect(self):
        cache = ThumbnailCache(height=20)
        thumb = cache.get(frame(10, size=(80, 40)))
        assert thumb.height == 20
        assert thumb.width == 40  # 80*20/40

    def test_second_get_returns_the_same_object(self):
        cache = ThumbnailCache()
        f = frame(10)
        assert cache.get(f) is cache.get(f)

    def test_frames_sharing_pixels_share_a_thumbnail(self):
        """A duplicated frame reuses the uid, so it must hit the cache."""
        cache = ThumbnailCache()
        original = frame(10)
        duplicate = original.sharing_pixels()
        assert cache.get(duplicate) is cache.get(original)
        assert len(cache) == 1

    def test_retimed_frame_shares_a_thumbnail(self):
        cache = ThumbnailCache()
        original = frame(10)
        retimed = original.with_duration(500)
        cache.get(original)
        cache.get(retimed)
        assert len(cache) == 1

    def test_distinct_frames_get_distinct_thumbnails(self):
        cache = ThumbnailCache()
        cache.get(frame(10))
        cache.get(frame(20))
        assert len(cache) == 2

    def test_retain_drops_absent_frames(self):
        cache = ThumbnailCache()
        keep = frame(10)
        drop = frame(20)
        cache.get(keep)
        cache.get(drop)
        cache.retain([keep.image_uid])
        assert len(cache) == 1
        assert cache.get(keep) is not None

    def test_thumb_width_matches_the_rendered_width(self):
        cache = ThumbnailCache(height=56)
        f = frame(10, size=(100, 50))
        assert cache.thumb_width((100, 50)) == cache.get(f).width

    def test_clear_empties_the_cache(self):
        cache = ThumbnailCache()
        cache.get(frame(10))
        cache.clear()
        assert len(cache) == 0
