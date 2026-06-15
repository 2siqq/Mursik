"""
Shared pytest fixtures for the test suite.

A "fixture" is pytest's way of providing reusable setup to your tests. Any test
that needs one just lists the fixture's name as a parameter, and pytest passes
in whatever the fixture returns. This keeps setup in one place instead of
copy-pasted into every test. Fixtures and mocking go a bit beyond what CS50P
covered, so this file is commented heavily.
"""

import sqlite3

import pytest


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
        "file_path": None,
    }


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
