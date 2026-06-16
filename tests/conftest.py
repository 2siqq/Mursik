"""
Shared pytest fixtures for the test suite.

A "fixture" is pytest's way of providing reusable setup to your tests. Any test
that needs one just lists the fixture's name as a parameter, and pytest passes
in whatever the fixture returns. This keeps setup in one place instead of
copy-pasted into every test.

Why module-level conftest.py rather than per-module fixtures?
  - Fixtures defined here are automatically available to every test file in
    the `tests/` directory and its subdirectories without an explicit import.
    Per-module fixtures (defined in the test file itself) are fine when they're
    only used in one file; shared infrastructure lives here.
"""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from library.models import Song


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_connection():
    """An empty, in-memory SQLite database for a single test.

    ":memory:" tells SQLite to build the database in RAM instead of on disk, so
    every test gets a clean, fast, throwaway database with no leftover state and
    no files to clean up. `yield` hands the connection to the test; the line
    after `yield` runs once the test finishes (teardown), closing the connection.
    """
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A path inside the per-test temp directory for an on-disk SQLite file.

    Use this when you need to test that the database persists across multiple
    Database() object lifetimes (open, close, reopen). tmp_path is a pytest
    built-in that gives you a fresh temporary directory for each test.
    """
    return tmp_path / "test.db"


# ---------------------------------------------------------------------------
# Song / search result fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_song_data():
    """A plain dict of song fields, shaped like one search result.

    Useful for building a Song object (once that class exists) or for inserting
    a known row into the test database.
    """
    return {
        "title": "HUMBLE.",
        "artist": "Kendrick Lamar",
        "album": "DAMN.",
        "duration": 177,
        "platform": "spotify",
        "source_id": "7KXjTSCq5nL1LoYztfXmQk",
        "url": None,
        "file_path": None,
        "cover_url": "https://i.scdn.co/image/test",
    }


@pytest.fixture
def sample_song(sample_song_data) -> Song:
    """A ready-to-use Song built from sample_song_data.

    Individual test files can define their own Song fixtures if they need
    different fields; this one is the "canonical" Spotify track used as a
    reference throughout the suite.
    """
    return Song(**sample_song_data)


@pytest.fixture
def fake_ytdlp_response():
    """A trimmed-down stand-in for what yt-dlp returns from a search.

    The real yt-dlp response is a large nested dict. For tests we only need the
    fields our code actually reads, so we fake a minimal version. This lets us
    test our parsing and cross-referencing logic without ever hitting YouTube.
    """
    return {
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


# ---------------------------------------------------------------------------
# External-client fakes
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_spotify_client():
    """A MagicMock standing in for a spotipy.Spotify instance.

    Why inject rather than patch?
      - SpotifySource takes a client argument in its constructor. Injecting a
        Mock is cleaner than patching `spotipy.Spotify` globally — it's more
        explicit and doesn't rely on import-order assumptions.

    The mock's `search` method is pre-configured to return a minimal Spotify
    track response so tests that don't need a specific payload can use this
    default. Tests that need a particular response should override it with
    `fake_spotify_client.search.return_value = {...}`.
    """
    client = MagicMock()
    client.search.return_value = {
        "tracks": {
            "items": [
                {
                    "id": "7KXjTSCq5nL1LoYztfXmQk",
                    "name": "HUMBLE.",
                    "duration_ms": 177000,
                    "artists": [{"name": "Kendrick Lamar"}],
                    "album": {
                        "name": "DAMN.",
                        "images": [{"url": "https://i.scdn.co/image/test", "width": 640}],
                    },
                }
            ]
        }
    }
    return client


# ---------------------------------------------------------------------------
# Audio file fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_mp3(tmp_path: Path) -> Path:
    """A zero-byte .mp3 file in a temporary directory.

    Used by tagger tests to verify tag read-back without needing a real audio
    payload. Mutagen operates on the ID3 header boundary, not the audio frames,
    so an empty file is a valid test target.
    """
    p = tmp_path / "test_track.mp3"
    p.write_bytes(b"")
    return p
