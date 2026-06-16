"""
Tests for library/models.py — Song dataclass and Playlist class.

Strategy: construct real objects (no mocks needed — these are pure in-memory
data structures with no network, disk, or third-party dependencies). Assert
field values, immutability, and Playlist operations.
"""

import dataclasses

import pytest

from library.models import Playlist, Song


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_song() -> Song:
    """A canonical Song for reuse across tests.

    Using a fixture (instead of repeating the constructor in each test) keeps
    tests DRY and makes it easy to change the shape of the canonical Song in
    one place.
    """
    return Song(
        title="HUMBLE.",
        artist="Kendrick Lamar",
        album="DAMN.",
        duration=177,
        platform="spotify",
        source_id="7KXjTSCq5nL1LoYztfXmQk",
        url=None,
        file_path=None,
        cover_url="https://i.scdn.co/image/test",
    )


@pytest.fixture
def youtube_song() -> Song:
    """A Song representing a YouTube search result.

    Cover_url is None because YouTube flat-search entries don't include album art.
    """
    return Song(
        title="Kendrick Lamar - HUMBLE.",
        artist="KendrickLamarVEVO",
        album="",
        duration=177,
        platform="youtube",
        source_id="tvTRZJ-4EyI",
        url="https://www.youtube.com/watch?v=tvTRZJ-4EyI",
        file_path=None,
        cover_url=None,
    )


# ---------------------------------------------------------------------------
# Song tests
# ---------------------------------------------------------------------------

class TestSong:
    def test_fields_set_correctly(self, sample_song: Song) -> None:
        assert sample_song.title == "HUMBLE."
        assert sample_song.artist == "Kendrick Lamar"
        assert sample_song.album == "DAMN."
        assert sample_song.duration == 177
        assert sample_song.platform == "spotify"
        assert sample_song.source_id == "7KXjTSCq5nL1LoYztfXmQk"
        assert sample_song.url is None
        assert sample_song.file_path is None
        assert sample_song.cover_url == "https://i.scdn.co/image/test"

    def test_frozen_prevents_mutation(self, sample_song: Song) -> None:
        """frozen=True means any attempt to set an attribute raises FrozenInstanceError."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            sample_song.title = "Changed"  # type: ignore[misc]

    def test_replace_produces_new_song(self, sample_song: Song) -> None:
        """dataclasses.replace() is the correct way to 'update' a frozen instance.

        It builds a brand-new Song, copying all fields except those you specify.
        The original is untouched — both objects coexist in memory.
        """
        updated = dataclasses.replace(sample_song, file_path="/music/humble.mp3")
        assert updated.file_path == "/music/humble.mp3"
        # Original unchanged — replace returns a new object.
        assert sample_song.file_path is None

    def test_hashable(self, sample_song: Song, youtube_song: Song) -> None:
        """frozen dataclasses are hashable and can be used in sets/dicts.

        This matters for the candidate-selection flow where we might deduplicate
        search results.
        """
        seen: set[Song] = {sample_song, youtube_song}
        assert len(seen) == 2
        # The same Song object hashes consistently.
        assert sample_song in seen

    def test_optional_fields_default_to_none(self) -> None:
        """file_path and cover_url have defaults; they're optional on construction."""
        song = Song(
            title="Test",
            artist="Artist",
            album="Album",
            duration=120,
            platform="youtube",
            source_id="abc123",
            url="https://youtube.com/watch?v=abc123",
        )
        assert song.file_path is None
        assert song.cover_url is None

    def test_duration_can_be_none(self) -> None:
        """yt-dlp flat-search entries sometimes omit duration — we tolerate that."""
        song = Song(
            title="Live clip",
            artist="Someone",
            album="",
            duration=None,
            platform="youtube",
            source_id="xyz",
            url="https://youtube.com/watch?v=xyz",
        )
        assert song.duration is None


# ---------------------------------------------------------------------------
# Playlist tests
# ---------------------------------------------------------------------------

class TestPlaylist:
    def test_empty_playlist(self) -> None:
        pl = Playlist("Chill")
        assert pl.name == "Chill"
        assert len(pl) == 0

    def test_add_song(self, sample_song: Song) -> None:
        pl = Playlist("My Mix")
        pl.add(sample_song)
        assert len(pl) == 1

    def test_remove_song(self, sample_song: Song) -> None:
        pl = Playlist("My Mix")
        pl.add(sample_song)
        pl.remove(sample_song)
        assert len(pl) == 0

    def test_remove_nonexistent_raises(self, sample_song: Song) -> None:
        """Removing a song that isn't in the playlist raises ValueError.

        Same contract as list.remove() — callers should guard against this.
        """
        pl = Playlist("Empty")
        with pytest.raises(ValueError):
            pl.remove(sample_song)

    def test_order_preserved(self, sample_song: Song, youtube_song: Song) -> None:
        """Songs come back in insertion order — important for playlist playback."""
        pl = Playlist("Ordered")
        pl.add(sample_song)
        pl.add(youtube_song)
        songs = list(pl)
        assert songs[0] == sample_song
        assert songs[1] == youtube_song

    def test_iter(self, sample_song: Song, youtube_song: Song) -> None:
        pl = Playlist("Test")
        pl.add(sample_song)
        pl.add(youtube_song)
        iterated = [s for s in pl]
        assert len(iterated) == 2

    def test_constructor_with_songs(self, sample_song: Song) -> None:
        """Playlist can be initialized with an existing list of Songs."""
        pl = Playlist("Preloaded", songs=[sample_song])
        assert len(pl) == 1

    def test_constructor_defensive_copy(self, sample_song: Song) -> None:
        """Mutating the original list after passing it to Playlist doesn't affect it.

        The constructor copies the list so external changes can't sneak in.
        """
        original: list[Song] = [sample_song]
        pl = Playlist("Copy Test", songs=original)
        original.clear()
        assert len(pl) == 1

    def test_repr(self) -> None:
        pl = Playlist("Vibes")
        assert "Vibes" in repr(pl)
        assert "0" in repr(pl)
