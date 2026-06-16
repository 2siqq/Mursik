"""
Tests for library/tagger.py.

Strategy: use a REAL temp file (not mocked) because the point is to verify
that mutagen can write and read back the tags. We write a minimal MP3-like
file, apply tags, then re-read with mutagen to assert the frames are there.

What "minimal MP3 file" means:
  - ID3 tagging doesn't depend on the audio payload — mutagen operates on the
    tag headers at the file boundary. A zero-byte or tiny file works fine for
    tagging tests (yt-dlp writes the real audio; we don't test that here).
  - `tmp_path` is a pytest built-in fixture that provides a per-test temporary
    directory that's deleted after the test. We write files there.

No mocking here: the real Tagger + real mutagen + real temp files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.id3 import APIC, ID3

from library.models import Song
from library.tagger import Tagger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal valid JPEG: just the SOI marker (FF D8) — enough for mutagen to
# accept as APIC data and for our mime-detection to return "image/jpeg".
FAKE_JPEG = b"\xff\xd8" + b"\x00" * 10

# Minimal valid PNG: 4-byte PNG signature prefix.
FAKE_PNG = b"\x89PNG" + b"\x00" * 10


@pytest.fixture
def empty_mp3(tmp_path: Path) -> Path:
    """A zero-byte file with a .mp3 extension.

    Tagger creates a fresh ID3 header when the file has none, so a zero-byte
    file is a valid test target for the 'new file' code path.
    """
    p = tmp_path / "test.mp3"
    p.write_bytes(b"")
    return p


@pytest.fixture
def song() -> Song:
    return Song(
        title="HUMBLE.",
        artist="Kendrick Lamar",
        album="DAMN.",
        duration=177,
        platform="youtube",
        source_id="tvTRZJ-4EyI",
        url="https://www.youtube.com/watch?v=tvTRZJ-4EyI",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTagger:
    def test_tags_text_frames(self, empty_mp3: Path, song: Song) -> None:
        """Title, artist, and album should be readable back from the file."""
        tagger = Tagger()
        result = tagger.tag(empty_mp3, song)
        assert result is True

        tags = ID3(empty_mp3)
        assert str(tags["TIT2"]) == "HUMBLE."
        assert str(tags["TPE1"]) == "Kendrick Lamar"
        assert str(tags["TALB"]) == "DAMN."

    def test_tags_cover_art_jpeg(self, empty_mp3: Path, song: Song) -> None:
        """APIC frame should be written with image/jpeg mime and type=3 (front cover)."""
        Tagger().tag(empty_mp3, song, cover_bytes=FAKE_JPEG)

        tags = ID3(empty_mp3)
        # ID3 key for front-cover APIC frame includes the description
        apic: APIC = tags.get("APIC:Cover")  # type: ignore[assignment]
        assert apic is not None
        assert apic.mime == "image/jpeg"
        assert apic.type == 3  # 3 = Front cover per ID3 spec
        assert apic.data == FAKE_JPEG

    def test_tags_cover_art_png(self, empty_mp3: Path, song: Song) -> None:
        """PNG cover bytes should produce mime=image/png."""
        Tagger().tag(empty_mp3, song, cover_bytes=FAKE_PNG)

        tags = ID3(empty_mp3)
        apic: APIC = tags.get("APIC:Cover")  # type: ignore[assignment]
        assert apic is not None
        assert apic.mime == "image/png"

    def test_no_cover_bytes_skips_apic(self, empty_mp3: Path, song: Song) -> None:
        """When cover_bytes=None, no APIC frame should be written."""
        Tagger().tag(empty_mp3, song, cover_bytes=None)

        tags = ID3(empty_mp3)
        assert tags.get("APIC:Cover") is None

    def test_returns_true_on_success(self, empty_mp3: Path, song: Song) -> None:
        assert Tagger().tag(empty_mp3, song) is True

    def test_returns_false_for_missing_file(self, tmp_path: Path, song: Song) -> None:
        """If the file doesn't exist, tag() should return False (not raise).

        This covers the case where yt-dlp reported success but the file wasn't
        actually written (a known yt-dlp edge case).
        """
        missing = tmp_path / "ghost.mp3"
        result = Tagger().tag(missing, song)
        assert result is False

    def test_overwrites_existing_tags(self, empty_mp3: Path, song: Song) -> None:
        """Tagging a file twice should overwrite the old values, not duplicate them."""
        import dataclasses
        Tagger().tag(empty_mp3, song)
        updated = dataclasses.replace(song, title="DNA.")
        Tagger().tag(empty_mp3, updated)

        tags = ID3(empty_mp3)
        assert str(tags["TIT2"]) == "DNA."

    def test_idempotent_on_repeated_calls(self, empty_mp3: Path, song: Song) -> None:
        """Multiple tag() calls on the same file should not accumulate APIC frames."""
        Tagger().tag(empty_mp3, song, cover_bytes=FAKE_JPEG)
        Tagger().tag(empty_mp3, song, cover_bytes=FAKE_JPEG)

        tags = ID3(empty_mp3)
        # There should still be exactly one APIC frame.
        apic_frames = [k for k in tags.keys() if k.startswith("APIC")]
        assert len(apic_frames) == 1
