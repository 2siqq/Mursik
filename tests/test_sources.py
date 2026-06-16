"""
Tests for library/sources.py — MusicSource ABC + three source implementations.

Strategy:
  - SpotifySource: inject a Mock as the spotipy client; feed it canned search
    JSON (shaped like the real Spotify API response) and assert our parsing.
  - YouTubeSource / SoundCloudSource: monkeypatch `library.sources.yt_dlp.YoutubeDL`
    so `extract_info` returns canned yt-dlp search JSON. We patch at the point of
    use (`library.sources`) not at the origin (`yt_dlp`) because the import is
    already bound when the module loads.
  - We NEVER mock our own parsing functions — we want the real parsing code to run.
  - We NEVER let a real network call happen.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from library.models import Song
from library.sources import SoundCloudSource, SpotifySource, YouTubeSource


# ---------------------------------------------------------------------------
# Canned API responses (shaped like the real thing, trimmed to what we parse)
# ---------------------------------------------------------------------------

# Spotify search result JSON (real shape, minimal fields)
FAKE_SPOTIFY_RESPONSE: dict[str, Any] = {
    "tracks": {
        "items": [
            {
                "id": "7KXjTSCq5nL1LoYztfXmQk",
                "name": "HUMBLE.",
                "duration_ms": 177000,
                "artists": [{"name": "Kendrick Lamar"}],
                "album": {
                    "name": "DAMN.",
                    "images": [
                        {"url": "https://i.scdn.co/image/large", "width": 640},
                        {"url": "https://i.scdn.co/image/medium", "width": 300},
                    ],
                },
            }
        ]
    }
}

# yt-dlp flat search result (matches the conftest fake_ytdlp_response shape)
FAKE_YTDLP_RESPONSE: dict[str, Any] = {
    "entries": [
        {
            "id": "tvTRZJ-4EyI",
            "title": "Kendrick Lamar - HUMBLE.",
            "uploader": "KendrickLamarVEVO",
            "duration": 177,
            "webpage_url": "https://www.youtube.com/watch?v=tvTRZJ-4EyI",
        }
    ]
}

# SoundCloud flat search result
FAKE_SC_RESPONSE: dict[str, Any] = {
    "entries": [
        {
            "id": "sc-humble-001",
            "title": "HUMBLE.",
            "uploader": "Kendrick Lamar",
            "duration": 177,
            "webpage_url": "https://soundcloud.com/kendricklamar/humble",
        }
    ]
}


# ---------------------------------------------------------------------------
# SpotifySource tests
# ---------------------------------------------------------------------------

class TestSpotifySource:
    @pytest.fixture
    def fake_client(self) -> MagicMock:
        """A Mock standing in for a `spotipy.Spotify` instance.

        The Mock records calls so we can assert `search()` was called; its
        return value is the canned response above.
        """
        client = MagicMock()
        client.search.return_value = FAKE_SPOTIFY_RESPONSE
        return client

    def test_returns_list_of_songs(self, fake_client: MagicMock) -> None:
        source = SpotifySource(client=fake_client)
        songs = source.search("kendrick humble")
        assert isinstance(songs, list)
        assert len(songs) == 1
        assert all(isinstance(s, Song) for s in songs)

    def test_correct_title_and_artist(self, fake_client: MagicMock) -> None:
        source = SpotifySource(client=fake_client)
        song = source.search("kendrick humble")[0]
        assert song.title == "HUMBLE."
        assert song.artist == "Kendrick Lamar"
        assert song.album == "DAMN."

    def test_duration_converted_from_ms_to_seconds(self, fake_client: MagicMock) -> None:
        """Spotify reports duration_ms; Song.duration must be seconds.

        177000 ms / 1000 = 177 s. A naive copy would give 177000 here.
        """
        source = SpotifySource(client=fake_client)
        song = source.search("kendrick humble")[0]
        assert song.duration == 177

    def test_platform_is_spotify(self, fake_client: MagicMock) -> None:
        source = SpotifySource(client=fake_client)
        song = source.search("kendrick humble")[0]
        assert song.platform == "spotify"

    def test_url_is_none(self, fake_client: MagicMock) -> None:
        """Spotify is metadata-only — url should always be None."""
        source = SpotifySource(client=fake_client)
        song = source.search("kendrick humble")[0]
        assert song.url is None

    def test_source_id_set(self, fake_client: MagicMock) -> None:
        source = SpotifySource(client=fake_client)
        song = source.search("kendrick humble")[0]
        assert song.source_id == "7KXjTSCq5nL1LoYztfXmQk"

    def test_cover_url_picks_largest_image(self, fake_client: MagicMock) -> None:
        """images[0] is largest (Spotify orders largest-first)."""
        source = SpotifySource(client=fake_client)
        song = source.search("kendrick humble")[0]
        assert song.cover_url == "https://i.scdn.co/image/large"

    def test_cover_url_none_when_no_images(self) -> None:
        client = MagicMock()
        client.search.return_value = {
            "tracks": {
                "items": [
                    {
                        "id": "abc",
                        "name": "No Art",
                        "duration_ms": 120000,
                        "artists": [{"name": "Artist"}],
                        "album": {"name": "Album", "images": []},
                    }
                ]
            }
        }
        source = SpotifySource(client=client)
        song = source.search("no art")[0]
        assert song.cover_url is None

    def test_limit_passed_to_client(self, fake_client: MagicMock) -> None:
        """The `limit` arg should flow through to the spotipy call."""
        source = SpotifySource(client=fake_client)
        source.search("query", limit=3)
        fake_client.search.assert_called_once_with(q="query", type="track", limit=3)

    def test_client_exception_returns_empty_list(self) -> None:
        """If the Spotify client raises (rate limit, network, auth), we return []."""
        client = MagicMock()
        client.search.side_effect = Exception("rate limit")
        source = SpotifySource(client=client)
        songs = source.search("whatever")
        assert songs == []


# ---------------------------------------------------------------------------
# YouTubeSource tests
# ---------------------------------------------------------------------------

class TestYouTubeSource:
    def _mock_ydl(self, response: dict) -> MagicMock:
        """Build a mock YoutubeDL context manager returning `response`."""
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = response
        return mock_ydl

    def test_returns_list_of_songs(self) -> None:
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value = self._mock_ydl(FAKE_YTDLP_RESPONSE)
            source = YouTubeSource()
            songs = source.search("kendrick humble")
        assert len(songs) == 1
        assert isinstance(songs[0], Song)

    def test_correct_fields(self) -> None:
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value = self._mock_ydl(FAKE_YTDLP_RESPONSE)
            source = YouTubeSource()
            song = source.search("kendrick humble")[0]
        assert song.title == "Kendrick Lamar - HUMBLE."
        assert song.artist == "KendrickLamarVEVO"
        assert song.duration == 177
        assert song.platform == "youtube"
        assert song.source_id == "tvTRZJ-4EyI"
        assert "youtube.com" in song.url

    def test_cover_url_is_none(self) -> None:
        """Flat search doesn't return thumbnails."""
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value = self._mock_ydl(FAKE_YTDLP_RESPONSE)
            song = YouTubeSource().search("x")[0]
        assert song.cover_url is None

    def test_limit_embedded_in_search_prefix(self) -> None:
        """limit=3 should produce `ytsearch3:` prefix in the extract_info call."""
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            mock = self._mock_ydl(FAKE_YTDLP_RESPONSE)
            MockYDL.return_value = mock
            YouTubeSource().search("query", limit=3)
        # extract_info must have been called with the ytsearch3: prefix
        call_args = mock.extract_info.call_args
        assert "ytsearch3:" in call_args[0][0]

    def test_duration_none_tolerates_missing_field(self) -> None:
        """yt-dlp flat entries may omit duration — should produce Song with None."""
        no_duration = {
            "entries": [
                {
                    "id": "abc",
                    "title": "Live Clip",
                    "uploader": "Artist",
                    "webpage_url": "https://youtube.com/watch?v=abc",
                    # 'duration' key intentionally absent
                }
            ]
        }
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value = self._mock_ydl(no_duration)
            song = YouTubeSource().search("live clip")[0]
        assert song.duration is None

    def test_ydl_exception_returns_empty_list(self) -> None:
        """yt-dlp raising any exception should degrade to []."""
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            mock_ydl = MagicMock()
            mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
            mock_ydl.__exit__ = MagicMock(return_value=False)
            mock_ydl.extract_info.side_effect = Exception("network error")
            MockYDL.return_value = mock_ydl
            songs = YouTubeSource().search("anything")
        assert songs == []

    def test_empty_entries_returns_empty_list(self) -> None:
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value = self._mock_ydl({"entries": []})
            songs = YouTubeSource().search("nothing")
        assert songs == []

    def test_opts_do_not_trigger_download(self) -> None:
        """YoutubeDL must be constructed with extract_flat; download=False must
        be passed to extract_info.  We verify both to guard against accidental
        download in the search path.
        """
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            mock = self._mock_ydl(FAKE_YTDLP_RESPONSE)
            MockYDL.return_value = mock
            YouTubeSource().search("test")
        # Opts passed to constructor
        constructor_opts: dict = MockYDL.call_args[0][0]
        assert constructor_opts.get("extract_flat") == "in_playlist"
        # extract_info called with download=False
        info_kwargs = mock.extract_info.call_args[1]
        assert info_kwargs.get("download") is False


# ---------------------------------------------------------------------------
# SoundCloudSource tests
# ---------------------------------------------------------------------------

class TestSoundCloudSource:
    def _mock_ydl(self, response: dict) -> MagicMock:
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = response
        return mock_ydl

    def test_returns_songs(self) -> None:
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value = self._mock_ydl(FAKE_SC_RESPONSE)
            songs = SoundCloudSource().search("humble")
        assert len(songs) == 1
        assert songs[0].platform == "soundcloud"

    def test_correct_fields(self) -> None:
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value = self._mock_ydl(FAKE_SC_RESPONSE)
            song = SoundCloudSource().search("humble")[0]
        assert song.title == "HUMBLE."
        assert song.source_id == "sc-humble-001"
        assert "soundcloud.com" in song.url

    def test_limit_embedded_in_scsearch_prefix(self) -> None:
        """limit=2 should produce `scsearch2:` prefix."""
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            mock = self._mock_ydl(FAKE_SC_RESPONSE)
            MockYDL.return_value = mock
            SoundCloudSource().search("query", limit=2)
        call_args = mock.extract_info.call_args
        assert "scsearch2:" in call_args[0][0]

    def test_exception_returns_empty_list(self) -> None:
        with patch("library.sources.yt_dlp.YoutubeDL") as MockYDL:
            mock_ydl = MagicMock()
            mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
            mock_ydl.__exit__ = MagicMock(return_value=False)
            mock_ydl.extract_info.side_effect = Exception("sc down")
            MockYDL.return_value = mock_ydl
            songs = SoundCloudSource().search("whatever")
        assert songs == []
