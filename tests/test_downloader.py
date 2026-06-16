"""
Tests for library/downloader.py.

Strategy: mock `library.downloader.yt_dlp.YoutubeDL` so no real download ever
happens. We verify:
  1. The opts dict requests best audio + MP3 320 kbps.
  2. `download()` calls `ydl.download([song.url])`.
  3. The returned path points to <dest_dir>/<source_id>.mp3.
  4. ValueError is raised when song.url is None (Spotify row).

We do NOT test that ffmpeg actually transcodes — that would require a real binary
and a network connection. What we test is that Downloader asks yt-dlp for the
right things.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from library.downloader import Downloader
from library.models import Song


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def yt_song() -> Song:
    """A YouTube Song with a real URL (as far as Downloader is concerned)."""
    return Song(
        title="HUMBLE.",
        artist="Kendrick Lamar",
        album="DAMN.",
        duration=177,
        platform="youtube",
        source_id="tvTRZJ-4EyI",
        url="https://www.youtube.com/watch?v=tvTRZJ-4EyI",
    )


@pytest.fixture
def spotify_song() -> Song:
    """A Spotify Song — url=None, can't be downloaded directly."""
    return Song(
        title="HUMBLE.",
        artist="Kendrick Lamar",
        album="DAMN.",
        duration=177,
        platform="spotify",
        source_id="7KXjTSCq5nL1LoYztfXmQk",
        url=None,
    )


def _make_mock_ydl() -> MagicMock:
    """Build a mock YoutubeDL context manager whose download() does nothing."""
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    return mock_ydl


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDownloader:
    def test_returns_expected_path(self, yt_song: Song, tmp_path: Path) -> None:
        """download() should return <dest_dir>/<source_id>.mp3."""
        with patch("library.downloader.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value = _make_mock_ydl()
            downloader = Downloader()
            result = downloader.download(yt_song, dest_dir=tmp_path)

        expected = tmp_path / "tvTRZJ-4EyI.mp3"
        assert result == expected

    def test_calls_ydl_download_with_url(self, yt_song: Song, tmp_path: Path) -> None:
        """yt-dlp's download() must be called with the song's URL."""
        with patch("library.downloader.yt_dlp.YoutubeDL") as MockYDL:
            mock_ydl = _make_mock_ydl()
            MockYDL.return_value = mock_ydl
            Downloader().download(yt_song, dest_dir=tmp_path)

        mock_ydl.download.assert_called_once_with([yt_song.url])

    def test_opts_request_best_audio(self, yt_song: Song, tmp_path: Path) -> None:
        """The yt-dlp opts must include `format: "bestaudio/best"`."""
        with patch("library.downloader.yt_dlp.YoutubeDL") as MockYDL:
            mock_ydl = _make_mock_ydl()
            MockYDL.return_value = mock_ydl
            Downloader().download(yt_song, dest_dir=tmp_path)

        opts: dict = MockYDL.call_args[0][0]
        assert opts["format"] == "bestaudio/best"

    def test_opts_request_mp3_320(self, yt_song: Song, tmp_path: Path) -> None:
        """The FFmpegExtractAudio postprocessor must be configured for MP3 @ 320 kbps."""
        with patch("library.downloader.yt_dlp.YoutubeDL") as MockYDL:
            mock_ydl = _make_mock_ydl()
            MockYDL.return_value = mock_ydl
            Downloader().download(yt_song, dest_dir=tmp_path)

        opts: dict = MockYDL.call_args[0][0]
        postprocessors = opts.get("postprocessors", [])
        assert len(postprocessors) == 1
        pp = postprocessors[0]
        assert pp["key"] == "FFmpegExtractAudio"
        assert pp["preferredcodec"] == "mp3"
        assert pp["preferredquality"] == "320"

    def test_opts_outtmpl_uses_source_id(self, yt_song: Song, tmp_path: Path) -> None:
        """outtmpl should embed %(id)s so the output filename matches source_id."""
        with patch("library.downloader.yt_dlp.YoutubeDL") as MockYDL:
            mock_ydl = _make_mock_ydl()
            MockYDL.return_value = mock_ydl
            Downloader().download(yt_song, dest_dir=tmp_path)

        opts: dict = MockYDL.call_args[0][0]
        assert "%(id)s" in opts["outtmpl"]
        assert str(tmp_path) in opts["outtmpl"]

    def test_dest_dir_is_created(self, yt_song: Song, tmp_path: Path) -> None:
        """download() should create the destination directory if it doesn't exist."""
        new_dir = tmp_path / "music" / "downloads"
        assert not new_dir.exists()
        with patch("library.downloader.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value = _make_mock_ydl()
            Downloader().download(yt_song, dest_dir=new_dir)
        assert new_dir.exists()

    def test_raises_value_error_for_spotify_song(self, spotify_song: Song, tmp_path: Path) -> None:
        """Spotify songs have url=None and cannot be downloaded directly."""
        downloader = Downloader()
        with pytest.raises(ValueError, match="no audio URL"):
            downloader.download(spotify_song, dest_dir=tmp_path)

    def test_opts_are_quiet(self, yt_song: Song, tmp_path: Path) -> None:
        """quiet and no_warnings should be True to suppress console output in the TUI."""
        with patch("library.downloader.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value = _make_mock_ydl()
            Downloader().download(yt_song, dest_dir=tmp_path)

        opts: dict = MockYDL.call_args[0][0]
        assert opts.get("quiet") is True
        assert opts.get("no_warnings") is True
