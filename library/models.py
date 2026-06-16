"""
Core domain objects — Song and Playlist.

Why a frozen dataclass for Song?
  - `frozen=True` makes instances immutable and automatically hashable, so Songs
    can be keys in dicts or members of sets without custom __hash__/__eq__.
  - It enforces the invariant that "a Song is a snapshot of what the API
    returned." Once you have a Song, you know it won't mutate under you.
  - When a download finishes we need to record the file path. Because Song is
    frozen we can't do `song.file_path = path`; instead we call
    `dataclasses.replace(song, file_path=path)`, which builds a new Song with
    all fields copied except the ones you name. This is a common functional-
    programming pattern — produce new values rather than mutating old ones.

Why duration: int | None?
  - yt-dlp flat (search-only) entries often omit or null out duration, so we
    can't safely assume it's always present. Allowing None means we never crash
    on an incomplete search result; callers that need duration just check first.

Why cover_url: str | None?
  - Spotify provides album art URLs; YouTube/SoundCloud searches generally don't.
    Keeping the field on Song keeps the shape uniform — callers don't need to
    know which platform they got the Song from.
"""

from __future__ import annotations

import dataclasses
from typing import Iterator


@dataclasses.dataclass(frozen=True)
class Song:
    """A single track, as returned by any search source.

    The shape is the same regardless of which platform it came from, so the TUI
    and the database don't care where the Song originated. Spotify rows set
    url=None because Spotify provides metadata only, not a real audio URL.
    """

    title: str
    artist: str
    album: str
    duration: int | None          # seconds (None if the source didn't provide it)
    platform: str                 # "spotify" | "youtube" | "soundcloud"
    source_id: str                # the platform's own track identifier
    url: str | None               # playback URL; None for Spotify rows
    file_path: str | None = None  # local path once downloaded; None until then
    cover_url: str | None = None  # album-art image URL; set by Spotify, usually None elsewhere


class Playlist:
    """An ordered, named collection of Songs.

    Why a plain class rather than a dataclass?
    - The list of songs is mutable (you add/remove over the playlist's lifetime),
      so frozen= wouldn't fit. A regular dataclass (mutable) could work, but the
      add/remove helpers and __iter__/__len__ benefit from being explicit methods
      rather than just a bare list attribute, which would require callers to know
      the internal field name and bypass the ordering guarantee.
    - Persistence is NOT this class's job — Database handles that. Playlist is
      a pure in-memory model the TUI can iterate and display.
    """

    def __init__(self, name: str, songs: list[Song] | None = None) -> None:
        self.name = name
        # Defensive copy so the caller's list can't mutate this one.
        self._songs: list[Song] = list(songs) if songs else []

    def add(self, song: Song) -> None:
        """Append a song to the end of the playlist."""
        self._songs.append(song)

    def remove(self, song: Song) -> None:
        """Remove the first occurrence of a song.

        Raises ValueError if the song isn't in the playlist — same contract as
        list.remove(), so callers can catch that if needed.
        """
        self._songs.remove(song)

    def __iter__(self) -> Iterator[Song]:
        return iter(self._songs)

    def __len__(self) -> int:
        return len(self._songs)

    def __repr__(self) -> str:
        return f"Playlist(name={self.name!r}, songs={len(self._songs)})"
