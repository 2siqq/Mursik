"""
Example tests demonstrating the patterns this project uses.

These run and pass right now, before any application code exists, so you can run
`pytest` immediately and watch the suite go green. They demonstrate three things
you'll lean on throughout the build:

  1. Using a fixture (the in-memory database) for setup.
  2. Testing database logic without touching a real file.
  3. Mocking an external call with monkeypatch so tests stay offline.

Delete or replace these once the real test files exist.
"""

import sys

import pytest


# --- Patterns 1 & 2: testing database logic with the in-memory fixture --------

def test_insert_and_read_back(db_connection, sample_song_data):
    """Create a table, insert a row, read it back, and assert.

    Note the PARAMETERIZED query — the ? placeholders with a tuple of values.
    Never build SQL with f-strings or concatenation; placeholders let SQLite
    handle escaping and prevent malformed/injected input. The whole test runs in
    RAM via the db_connection fixture and disappears when it finishes.
    """
    db_connection.execute(
        "CREATE TABLE songs (title TEXT, artist TEXT, duration INTEGER)"
    )
    db_connection.execute(
        "INSERT INTO songs (title, artist, duration) VALUES (?, ?, ?)",
        (
            sample_song_data["title"],
            sample_song_data["artist"],
            sample_song_data["duration"],
        ),
    )

    row = db_connection.execute(
        "SELECT title, artist, duration FROM songs WHERE artist = ?",
        (sample_song_data["artist"],),
    ).fetchone()

    assert row == ("HUMBLE.", "Kendrick Lamar", 177)


# --- Pattern 3: mocking an external call with monkeypatch ---------------------

def fetch_search_results(query):
    """A throwaway stand-in for "code that calls the network".

    In the real app this lives inside a source class and calls yt-dlp or spotipy.
    Here it's a placeholder so we can show how to replace it in a test. If a test
    ever lets the real version run, it raises — proving the mock is doing its job.
    """
    raise RuntimeError("real network call — should never run during tests")


def test_mocking_replaces_network_call(monkeypatch, fake_ytdlp_response):
    """`monkeypatch` temporarily swaps a function for one test, then restores it.

    We replace fetch_search_results with a fake that returns canned data, so the
    test never makes a real request. This is exactly how you'll test the source
    classes and the Downloader without hitting Spotify or YouTube. The
    `sys.modules[__name__]` argument is just "this module" — the object whose
    attribute we're swapping.
    """
    def fake_fetch(query):
        return fake_ytdlp_response

    monkeypatch.setattr(sys.modules[__name__], "fetch_search_results", fake_fetch)

    result = fetch_search_results("kendrick humble")
    first_entry = result["entries"][0]

    assert first_entry["title"] == "Kendrick Lamar - HUMBLE."
    assert first_entry["duration"] == 177


# --- Template: testing a real class once Claude Code builds it -----------------

def test_song_model_template():
    """A template for testing the real Song class.

    `importorskip` tries to import the module and, if it doesn't exist yet, marks
    this test skipped instead of failing the suite. Once library/models.py is
    built, replace the skip with real assertions.
    """
    pytest.importorskip("library.models")

    # Example of what you might assert once Song exists:
    #   from library.models import Song
    #   song = Song(title="HUMBLE.", artist="Kendrick Lamar", duration=177, ...)
    #   assert song.artist == "Kendrick Lamar"
    pytest.skip("Song not implemented yet — remove this skip once it is.")
