"""
Tests for library/database.py — the sqlite3 wrapper.

Strategy: Use a real in-memory SQLite database (not mocked), which gives us
true SQL semantics while keeping tests fast and stateless. Network/yt-dlp
mocking is not needed here because Database has no network calls.

This replaces the patterns shown in test_example.py:
  - Parameterized queries: covered by every Database method (no f-strings).
  - In-memory DB: we use Database(":memory:") so tests are isolated and fast.
  - Monkeypatch mock: shown here for the `source_id` collision path.
"""

import pytest

from library.database import Database
from library.models import Song


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db() -> Database:
    """A fresh in-memory Database for each test.

    Using Database(":memory:") means the schema is applied in RAM and
    disappears when the fixture tears down — every test starts clean.
    """
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def humble_song() -> Song:
    return Song(
        title="HUMBLE.",
        artist="Kendrick Lamar",
        album="DAMN.",
        duration=177,
        platform="youtube",
        source_id="tvTRZJ-4EyI",
        url="https://www.youtube.com/watch?v=tvTRZJ-4EyI",
        file_path="/music/humble.mp3",
        cover_url="https://i.scdn.co/image/humble",
    )


@pytest.fixture
def dna_song() -> Song:
    return Song(
        title="DNA.",
        artist="Kendrick Lamar",
        album="DAMN.",
        duration=185,
        platform="youtube",
        source_id="NLZRYQMLDW4",
        url="https://www.youtube.com/watch?v=NLZRYQMLDW4",
        file_path="/music/dna.mp3",
        cover_url=None,
    )


# ---------------------------------------------------------------------------
# Song CRUD
# ---------------------------------------------------------------------------

class TestSongPersistence:
    def test_add_and_get_song(self, db: Database, humble_song: Song) -> None:
        db.add_song(humble_song)
        fetched = db.get_song(humble_song.source_id)
        assert fetched is not None
        assert fetched.title == "HUMBLE."
        assert fetched.artist == "Kendrick Lamar"
        assert fetched.platform == "youtube"
        assert fetched.file_path == "/music/humble.mp3"

    def test_get_song_not_found_returns_none(self, db: Database) -> None:
        result = db.get_song("does-not-exist")
        assert result is None

    def test_get_all_songs_empty(self, db: Database) -> None:
        assert db.get_all_songs() == []

    def test_get_all_songs_multiple(
        self, db: Database, humble_song: Song, dna_song: Song
    ) -> None:
        db.add_song(humble_song)
        db.add_song(dna_song)
        songs = db.get_all_songs()
        assert len(songs) == 2
        # Returned alphabetically by title: "DNA." before "HUMBLE."
        assert songs[0].title == "DNA."
        assert songs[1].title == "HUMBLE."

    def test_add_song_upserts_on_conflict(self, db: Database, humble_song: Song) -> None:
        """Re-adding a song with the same source_id updates its fields rather
        than inserting a duplicate, which would violate the UNIQUE constraint.

        This is the path taken when a song is re-downloaded to a new file path.
        """
        import dataclasses
        db.add_song(humble_song)
        updated = dataclasses.replace(humble_song, file_path="/new/path/humble.mp3")
        db.add_song(updated)
        # Only one row should exist.
        songs = db.get_all_songs()
        assert len(songs) == 1
        assert songs[0].file_path == "/new/path/humble.mp3"

    def test_duration_can_be_none(self, db: Database) -> None:
        """Songs with unknown duration (yt-dlp flat entries) round-trip correctly."""
        song = Song(
            title="Live clip",
            artist="Artist",
            album="",
            duration=None,
            platform="youtube",
            source_id="noduration",
            url="https://youtube.com/watch?v=noduration",
        )
        db.add_song(song)
        fetched = db.get_song("noduration")
        assert fetched is not None
        assert fetched.duration is None

    def test_cover_url_persists(self, db: Database, humble_song: Song) -> None:
        db.add_song(humble_song)
        fetched = db.get_song(humble_song.source_id)
        assert fetched is not None
        assert fetched.cover_url == "https://i.scdn.co/image/humble"


# ---------------------------------------------------------------------------
# Playlist CRUD
# ---------------------------------------------------------------------------

class TestPlaylistCRUD:
    def test_create_and_get_playlist(self, db: Database) -> None:
        playlist_id = db.create_playlist("Chill Vibes")
        playlists = db.get_playlists()
        assert len(playlists) == 1
        assert playlists[0]["name"] == "Chill Vibes"
        assert playlists[0]["id"] == playlist_id

    def test_create_duplicate_playlist_raises(self, db: Database) -> None:
        """UNIQUE constraint on playlist name — duplicate names are rejected."""
        import sqlite3
        db.create_playlist("Unique")
        with pytest.raises(sqlite3.IntegrityError):
            db.create_playlist("Unique")

    def test_get_playlists_ordered_alphabetically(self, db: Database) -> None:
        db.create_playlist("Zephyr")
        db.create_playlist("Alpha")
        names = [p["name"] for p in db.get_playlists()]
        assert names == ["Alpha", "Zephyr"]

    def test_get_playlist_by_name(self, db: Database) -> None:
        db.create_playlist("Rock Classics")
        result = db.get_playlist_by_name("Rock Classics")
        assert result is not None
        assert result["name"] == "Rock Classics"

    def test_get_playlist_by_name_not_found(self, db: Database) -> None:
        result = db.get_playlist_by_name("Ghost Playlist")
        assert result is None


# ---------------------------------------------------------------------------
# Playlist ↔ Song join table
# ---------------------------------------------------------------------------

class TestPlaylistSongs:
    def test_add_song_to_playlist(
        self, db: Database, humble_song: Song
    ) -> None:
        pl_id = db.create_playlist("My Mix")
        db.add_song_to_playlist(pl_id, humble_song)
        songs = db.get_playlist_songs(pl_id)
        assert len(songs) == 1
        assert songs[0].title == "HUMBLE."

    def test_playlist_songs_preserve_order(
        self, db: Database, humble_song: Song, dna_song: Song
    ) -> None:
        """Songs must come back in the order they were added, not alphabetically.

        This is the behaviour guaranteed by the `position` column and ORDER BY.
        If we stored no position, ORDER BY would be undefined and playlist
        playback could be shuffled arbitrarily.
        """
        pl_id = db.create_playlist("Ordered")
        db.add_song_to_playlist(pl_id, humble_song)
        db.add_song_to_playlist(pl_id, dna_song)
        songs = db.get_playlist_songs(pl_id)
        assert songs[0].title == "HUMBLE."
        assert songs[1].title == "DNA."

    def test_remove_song_from_playlist(
        self, db: Database, humble_song: Song, dna_song: Song
    ) -> None:
        pl_id = db.create_playlist("Mix")
        db.add_song_to_playlist(pl_id, humble_song)
        db.add_song_to_playlist(pl_id, dna_song)
        db.remove_song_from_playlist(pl_id, humble_song)
        songs = db.get_playlist_songs(pl_id)
        assert len(songs) == 1
        assert songs[0].title == "DNA."

    def test_order_after_remove_and_add(
        self, db: Database, humble_song: Song, dna_song: Song
    ) -> None:
        """After removing a song, newly added songs get a position after the
        highest remaining position — not renumbered — so gaps are expected and
        don't cause conflicts.
        """
        pl_id = db.create_playlist("Gap Test")
        db.add_song_to_playlist(pl_id, humble_song)
        db.add_song_to_playlist(pl_id, dna_song)
        db.remove_song_from_playlist(pl_id, humble_song)

        # Add a third song after the remove — it should still sort last.
        third = Song(
            title="Alright",
            artist="Kendrick Lamar",
            album="To Pimp a Butterfly",
            duration=219,
            platform="youtube",
            source_id="Z-48KAhu3oM",
            url="https://www.youtube.com/watch?v=Z-48KAhu3oM",
        )
        db.add_song_to_playlist(pl_id, third)
        songs = db.get_playlist_songs(pl_id)
        assert len(songs) == 2
        assert songs[0].title == "DNA."
        assert songs[1].title == "Alright"

    def test_add_duplicate_song_to_playlist_is_idempotent(
        self, db: Database, humble_song: Song
    ) -> None:
        """Adding the same song twice results in only one row (INSERT OR IGNORE)."""
        pl_id = db.create_playlist("Once")
        db.add_song_to_playlist(pl_id, humble_song)
        db.add_song_to_playlist(pl_id, humble_song)
        songs = db.get_playlist_songs(pl_id)
        assert len(songs) == 1

    def test_remove_nonexistent_song_is_safe(
        self, db: Database, humble_song: Song
    ) -> None:
        """Removing a song that was never added should not raise."""
        pl_id = db.create_playlist("Empty")
        # Should not raise:
        db.remove_song_from_playlist(pl_id, humble_song)

    def test_get_playlist_songs_empty(self, db: Database) -> None:
        pl_id = db.create_playlist("Empty")
        assert db.get_playlist_songs(pl_id) == []

    def test_songs_across_playlists_are_independent(
        self, db: Database, humble_song: Song, dna_song: Song
    ) -> None:
        """Each playlist maintains its own ordered list — songs in one don't
        appear in another.
        """
        pl1 = db.create_playlist("Playlist A")
        pl2 = db.create_playlist("Playlist B")
        db.add_song_to_playlist(pl1, humble_song)
        db.add_song_to_playlist(pl2, dna_song)
        assert db.get_playlist_songs(pl1)[0].title == "HUMBLE."
        assert db.get_playlist_songs(pl2)[0].title == "DNA."
