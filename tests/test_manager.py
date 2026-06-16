"""
Tests for library/manager.py — the MusicLibrary façade.

Strategy: inject fake/mock collaborators for every boundary.
  - Fake sources: plain classes that return canned Song lists, so we test the
    real aggregation + fallback logic without touching the network.
  - Fake downloader: returns a predetermined Path without writing a file.
  - Fake tagger: records calls without touching disk.
  - Real Database (in-memory): confirms persist logic works end-to-end.
  - Monkeypatch `library.manager._fetch_cover`: simulates cover-art fetch at the
    network boundary without making an HTTP request.

We do NOT mock MusicLibrary methods — we test their real implementations.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from library.database import Database
from library.downloader import Downloader
from library.manager import MusicLibrary, _fetch_cover
from library.models import Song
from library.tagger import Tagger


# ---------------------------------------------------------------------------
# Helpers: fake collaborators
# ---------------------------------------------------------------------------

class FakeSource:
    """Stands in for SpotifySource / YouTubeSource / SoundCloudSource.

    Returns a fixed list of Songs so tests control what 'search' returns
    without any network calls.
    """
    def __init__(self, songs: list[Song], raises: bool = False) -> None:
        self._songs = songs
        self._raises = raises

    def search(self, query: str, limit: int = 5) -> list[Song]:
        if self._raises:
            raise RuntimeError("simulated source failure")
        return self._songs[:limit]


class FakeDownloader:
    """Stands in for Downloader.

    Returns a predetermined Path, simulating a successful yt-dlp download
    without touching the network or file system.
    """
    def __init__(self, return_path: Path) -> None:
        self._path = return_path

    def download(self, song: Song, dest_dir: Any = "downloads") -> Path:
        return self._path


class FakeTagger:
    """Stands in for Tagger.  Records tag() calls for later assertions."""
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def tag(
        self,
        file_path: Any,
        song: Song,
        cover_bytes: bytes | None = None,
    ) -> bool:
        self.calls.append({"file_path": file_path, "song": song, "cover_bytes": cover_bytes})
        return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def spotify_song() -> Song:
    return Song(
        title="HUMBLE.",
        artist="Kendrick Lamar",
        album="DAMN.",
        duration=177,
        platform="spotify",
        source_id="7KXjTSCq5nL1LoYztfXmQk",
        url=None,
        cover_url="https://i.scdn.co/image/large",
    )


@pytest.fixture
def youtube_song() -> Song:
    return Song(
        title="Kendrick Lamar - HUMBLE.",
        artist="KendrickLamarVEVO",
        album="",
        duration=177,
        platform="youtube",
        source_id="tvTRZJ-4EyI",
        url="https://www.youtube.com/watch?v=tvTRZJ-4EyI",
    )


@pytest.fixture
def soundcloud_song() -> Song:
    return Song(
        title="HUMBLE.",
        artist="Kendrick Lamar",
        album="",
        duration=177,
        platform="soundcloud",
        source_id="sc-001",
        url="https://soundcloud.com/kendricklamar/humble",
    )


@pytest.fixture
def real_db() -> Database:
    """In-memory Database — tests real persistence logic at zero cost."""
    db = Database(":memory:")
    yield db
    db.close()


def _make_library(
    *,
    spotify_songs: list[Song] | None = None,
    youtube_songs: list[Song] | None = None,
    soundcloud_songs: list[Song] | None = None,
    spotify_raises: bool = False,
    youtube_raises: bool = False,
    soundcloud_raises: bool = False,
    download_path: Path | None = None,
    db: Database | None = None,
    tagger: FakeTagger | None = None,
    tmp_path: Path | None = None,
) -> tuple[MusicLibrary, FakeTagger, Database]:
    """Build a MusicLibrary with controlled collaborators.

    Returns (library, tagger, database) so tests can inspect calls and state.
    """
    _db = db or Database(":memory:")
    _tagger = tagger or FakeTagger()
    _dl_path = download_path or Path("/tmp/tvTRZJ-4EyI.mp3")

    lib = MusicLibrary(
        spotify_source=FakeSource(spotify_songs or [], raises=spotify_raises),
        youtube_source=FakeSource(youtube_songs or [], raises=youtube_raises),
        soundcloud_source=FakeSource(soundcloud_songs or [], raises=soundcloud_raises),
        downloader=FakeDownloader(_dl_path),
        tagger=_tagger,
        database=_db,
        download_dir=tmp_path or Path("/tmp"),
    )
    return lib, _tagger, _db


# ---------------------------------------------------------------------------
# search_all tests
# ---------------------------------------------------------------------------

class TestSearchAll:
    def test_aggregates_all_platforms(
        self,
        spotify_song: Song,
        youtube_song: Song,
        soundcloud_song: Song,
    ) -> None:
        lib, _, _ = _make_library(
            spotify_songs=[spotify_song],
            youtube_songs=[youtube_song],
            soundcloud_songs=[soundcloud_song],
        )
        results = lib.search_all("kendrick humble")
        assert len(results["spotify"]) == 1
        assert len(results["youtube"]) == 1
        assert len(results["soundcloud"]) == 1
        assert results["spotify"][0].platform == "spotify"
        assert results["youtube"][0].platform == "youtube"
        assert results["soundcloud"][0].platform == "soundcloud"

    def test_failing_source_degrades_to_empty_list(
        self, spotify_song: Song
    ) -> None:
        """If one source raises, its key maps to [] — the others still work.

        This is the key graceful-degradation guarantee: a broken Spotify token
        shouldn't prevent YouTube results from showing.
        """
        lib, _, _ = _make_library(
            spotify_songs=[spotify_song],
            youtube_raises=True,   # YouTube is broken
            soundcloud_songs=[],
        )
        results = lib.search_all("test")
        assert results["spotify"] == [spotify_song]
        assert results["youtube"] == []   # degraded to []
        assert results["soundcloud"] == []

    def test_all_sources_failing_returns_empty_dicts(self) -> None:
        lib, _, _ = _make_library(
            spotify_raises=True,
            youtube_raises=True,
            soundcloud_raises=True,
        )
        results = lib.search_all("crash test")
        assert results == {"spotify": [], "youtube": [], "soundcloud": []}

    def test_result_keys_always_present(self) -> None:
        """All three platform keys should exist even when sources return []."""
        lib, _, _ = _make_library()
        results = lib.search_all("nothing")
        assert set(results.keys()) == {"spotify", "youtube", "soundcloud"}


# ---------------------------------------------------------------------------
# find_youtube_candidates tests
# ---------------------------------------------------------------------------

class TestFindYoutubeCandidates:
    def test_searches_youtube_with_artist_title(
        self, spotify_song: Song, youtube_song: Song
    ) -> None:
        lib, _, _ = _make_library(youtube_songs=[youtube_song])
        candidates = lib.find_youtube_candidates(spotify_song)
        assert len(candidates) == 1
        assert candidates[0].platform == "youtube"

    def test_limit_defaults_to_5(self, spotify_song: Song) -> None:
        """find_youtube_candidates passes limit=5 to the YouTube source."""
        many = [
            dataclasses.replace(
                spotify_song,
                platform="youtube",
                source_id=f"yt-{i}",
                url=f"https://youtube.com/watch?v={i}",
            )
            for i in range(10)
        ]
        lib, _, _ = _make_library(youtube_songs=many)
        candidates = lib.find_youtube_candidates(spotify_song, limit=5)
        assert len(candidates) == 5


# ---------------------------------------------------------------------------
# download tests
# ---------------------------------------------------------------------------

class TestDownload:
    def test_direct_youtube_download_persists_and_returns_song(
        self, youtube_song: Song, real_db: Database, tmp_path: Path
    ) -> None:
        """Direct download (no metadata_song): platform/source_id/url stay as-is."""
        dl_path = tmp_path / f"{youtube_song.source_id}.mp3"
        lib, tagger, db = _make_library(
            download_path=dl_path, db=real_db, tmp_path=tmp_path
        )
        with patch("library.manager._fetch_cover", return_value=None):
            result = lib.download(youtube_song)

        # Result should be a Song with file_path set.
        assert result.file_path == str(dl_path)
        assert result.platform == "youtube"
        assert result.source_id == youtube_song.source_id

        # Should be persisted in the database.
        db_song = db.get_song(youtube_song.source_id)
        assert db_song is not None
        assert db_song.file_path == str(dl_path)

    def test_cross_ref_download_merges_fields(
        self,
        spotify_song: Song,
        youtube_song: Song,
        real_db: Database,
        tmp_path: Path,
    ) -> None:
        """Spotify→YouTube cross-ref: audio identity from YouTube, display from Spotify.

        The final Song must have:
          - platform, source_id, url  from youtube_song (audio identity)
          - title, artist, album, duration, cover_url  from spotify_song (display metadata)
          - file_path set to the written file
        """
        dl_path = tmp_path / f"{youtube_song.source_id}.mp3"
        lib, tagger, db = _make_library(
            download_path=dl_path, db=real_db, tmp_path=tmp_path
        )
        fake_cover = b"\xff\xd8" + b"\x00" * 4  # fake JPEG bytes

        with patch("library.manager._fetch_cover", return_value=fake_cover):
            result = lib.download(youtube_song, metadata_song=spotify_song)

        # Audio identity: from YouTube
        assert result.platform == "youtube"
        assert result.source_id == youtube_song.source_id
        assert result.url == youtube_song.url

        # Display metadata: from Spotify
        assert result.title == spotify_song.title
        assert result.artist == spotify_song.artist
        assert result.album == spotify_song.album
        assert result.duration == spotify_song.duration
        assert result.cover_url == spotify_song.cover_url

        # file_path is set
        assert result.file_path == str(dl_path)

    def test_cross_ref_db_row_has_youtube_identity(
        self,
        spotify_song: Song,
        youtube_song: Song,
        real_db: Database,
        tmp_path: Path,
    ) -> None:
        """The DB row should be keyed by YouTube source_id, not Spotify's."""
        dl_path = tmp_path / f"{youtube_song.source_id}.mp3"
        lib, _, db = _make_library(
            download_path=dl_path, db=real_db, tmp_path=tmp_path
        )
        with patch("library.manager._fetch_cover", return_value=None):
            lib.download(youtube_song, metadata_song=spotify_song)

        # Row is findable by YouTube source_id.
        db_song = db.get_song(youtube_song.source_id)
        assert db_song is not None
        assert db_song.platform == "youtube"
        assert db_song.title == "HUMBLE."  # Spotify display metadata

    def test_tagger_called_with_merged_song_and_cover(
        self,
        spotify_song: Song,
        youtube_song: Song,
        tmp_path: Path,
    ) -> None:
        """Tagger should receive the merged Song and the fetched cover bytes."""
        dl_path = tmp_path / f"{youtube_song.source_id}.mp3"
        fake_cover = b"\xff\xd8" + b"\x00" * 4

        lib, tagger, _ = _make_library(
            download_path=dl_path, tmp_path=tmp_path
        )
        with patch("library.manager._fetch_cover", return_value=fake_cover):
            lib.download(youtube_song, metadata_song=spotify_song)

        assert len(tagger.calls) == 1
        call = tagger.calls[0]
        assert call["song"].title == spotify_song.title  # display from Spotify
        assert call["song"].platform == "youtube"        # identity from YouTube
        assert call["cover_bytes"] == fake_cover

    def test_failed_cover_fetch_does_not_block_download(
        self, youtube_song: Song, real_db: Database, tmp_path: Path
    ) -> None:
        """If _fetch_cover returns None (any failure), download still completes."""
        dl_path = tmp_path / f"{youtube_song.source_id}.mp3"
        lib, tagger, db = _make_library(
            download_path=dl_path, db=real_db, tmp_path=tmp_path
        )
        with patch("library.manager._fetch_cover", return_value=None):
            result = lib.download(youtube_song)

        assert result.file_path is not None
        assert tagger.calls[0]["cover_bytes"] is None


# ---------------------------------------------------------------------------
# Playlist passthrough tests
# ---------------------------------------------------------------------------

class TestPlaylistPassthroughs:
    def test_create_and_get_playlists(self, real_db: Database) -> None:
        lib, _, db = _make_library(db=real_db)
        pl_id = lib.create_playlist("Chill")
        playlists = lib.get_playlists()
        assert len(playlists) == 1
        assert playlists[0]["name"] == "Chill"
        assert playlists[0]["id"] == pl_id

    def test_add_and_get_playlist_songs(
        self, youtube_song: Song, real_db: Database, tmp_path: Path
    ) -> None:
        dl_path = tmp_path / f"{youtube_song.source_id}.mp3"
        lib, _, db = _make_library(
            download_path=dl_path, db=real_db, tmp_path=tmp_path
        )
        with patch("library.manager._fetch_cover", return_value=None):
            stored = lib.download(youtube_song)

        pl_id = lib.create_playlist("Test Playlist")
        lib.add_to_playlist(pl_id, stored)
        songs = lib.get_playlist_songs(pl_id)
        assert len(songs) == 1
        assert songs[0].source_id == youtube_song.source_id

    def test_remove_from_playlist(
        self, youtube_song: Song, real_db: Database, tmp_path: Path
    ) -> None:
        dl_path = tmp_path / f"{youtube_song.source_id}.mp3"
        lib, _, db = _make_library(
            download_path=dl_path, db=real_db, tmp_path=tmp_path
        )
        with patch("library.manager._fetch_cover", return_value=None):
            stored = lib.download(youtube_song)

        pl_id = lib.create_playlist("Temp")
        lib.add_to_playlist(pl_id, stored)
        lib.remove_from_playlist(pl_id, stored)
        assert lib.get_playlist_songs(pl_id) == []


# ---------------------------------------------------------------------------
# _fetch_cover helper tests
# ---------------------------------------------------------------------------

class TestFetchCover:
    def test_returns_none_for_none_url(self) -> None:
        """No URL → no request → None immediately."""
        result = _fetch_cover(None)
        assert result is None

    def test_returns_bytes_on_success(self) -> None:
        fake_bytes = b"\xff\xd8" + b"\x00" * 10
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.content = fake_bytes

        with patch("library.manager.requests.get", return_value=mock_resp):
            result = _fetch_cover("https://example.com/cover.jpg")

        assert result == fake_bytes

    def test_returns_none_on_non_ok_status(self) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("library.manager.requests.get", return_value=mock_resp):
            result = _fetch_cover("https://example.com/cover.jpg")

        assert result is None

    def test_returns_none_on_request_exception(self) -> None:
        """Any requests.RequestException (network, timeout, SSL) → None."""
        import requests as req
        with patch("library.manager.requests.get", side_effect=req.RequestException("timeout")):
            result = _fetch_cover("https://example.com/cover.jpg")

        assert result is None
