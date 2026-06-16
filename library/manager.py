"""
MusicLibrary — the façade the TUI talks to.

Why a façade?
  - The TUI should know exactly one object and call methods on it. Without a
    façade, the TUI would need to import and coordinate spotipy, yt-dlp, mutagen,
    and sqlite3 itself. That's four responsibilities in one file, makes testing
    hard, and means any library swap (e.g. a new search source) touches the UI.
  - With a façade: the TUI calls `library.download(song)` and doesn't care that
    internally this involves a YouTubeSource search, a yt-dlp download, a
    `requests.get` for cover art, a mutagen write, and an sqlite3 insert.
  - Each collaborator (sources, downloader, tagger, database) is independently
    testable and swappable.

Spotify→YouTube cross-reference ("you pick") flow
  1. User picks a Spotify row in the TUI.
  2. TUI calls `library.find_youtube_candidates(spotify_song)`.
  3. MusicLibrary calls YouTubeSource.search("{artist} {title}") → list[Song].
  4. TUI shows the candidates; user picks one → `yt_song`.
  5. TUI calls `library.download(yt_song, metadata_song=spotify_song)`.
  6. MusicLibrary downloads yt_song.url (a real YouTube URL) but tags and stores
     the result with Spotify's display metadata — the cross-ref "merge".

Persisted identity rules on cross-ref download
  - Audio identity (platform, source_id, url) comes from the YouTube song — it
    points at the actual file we downloaded.
  - Display metadata (title, artist, album, duration, cover_url) comes from the
    Spotify metadata_song — cleaner, richer info.
  - When metadata_song is None (direct YouTube/SoundCloud download) the audio
    song provides everything.

_fetch_cover
  - A small inline helper that does one HTTP GET for an image URL.
  - It lives here (not in Tagger) because it's a network call — Tagger is meant
    to be an offline bytes-to-file writer. The boundary is: anything that touches
    the network lives in manager.py or sources.py.
  - Catches broadly because we can't enumerate all requests exceptions (timeout,
    connection error, SSL failure, non-200 status). A missing cover should never
    block a download.

graceful_search_all degradation
  - Each source's search is wrapped in try/except → []. If Spotify's token
    expires mid-session or YouTube's extractor breaks, the other sources still
    work. The broad catch is intentional: we can't enumerate all possible
    source-level errors and crashing the whole search for a partial failure
    would be a worse UX than returning fewer results.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import requests

from library.database import Database
from library.downloader import Downloader
from library.models import Song
from library.sources import MusicSource, SpotifySource, YouTubeSource
from library.tagger import Tagger


class MusicLibrary:
    """Coordinator / façade.  The TUI imports and instantiates one of these."""

    def __init__(
        self,
        spotify_source: MusicSource,
        youtube_source: MusicSource,
        soundcloud_source: MusicSource,
        downloader: Downloader,
        tagger: Tagger,
        database: Database,
        download_dir: str | Path = "downloads",
    ) -> None:
        self._spotify = spotify_source
        self._youtube = youtube_source
        self._soundcloud = soundcloud_source
        self._downloader = downloader
        self._tagger = tagger
        self._db = database
        self._download_dir = Path(download_dir)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_all(self, query: str) -> dict[str, list[Song]]:
        """Search all three platforms simultaneously; return grouped results.

        Per-source failures degrade to [] — one broken platform doesn't crash
        the whole search. Each `except Exception` is broad because source errors
        are heterogeneous (network, auth, parse); any one should degrade, not crash.
        """
        results: dict[str, list[Song]] = {}
        for platform, source in [
            ("spotify", self._spotify),
            ("youtube", self._youtube),
            ("soundcloud", self._soundcloud),
        ]:
            try:
                results[platform] = source.search(query, limit=5)
            except Exception:
                # Broad catch: network errors, expired tokens, extractor failures.
                # Degrading to [] is correct — partial results beat a hard crash.
                results[platform] = []
        return results

    def search_platform(
        self, platform: str, query: str, limit: int = 5
    ) -> list[Song]:
        """Search a single named platform. Raises KeyError on unknown platform."""
        source_map: dict[str, MusicSource] = {
            "spotify": self._spotify,
            "youtube": self._youtube,
            "soundcloud": self._soundcloud,
        }
        source = source_map[platform]
        try:
            return source.search(query, limit=limit)
        except Exception:
            # Same broad-catch rationale as search_all.
            return []

    def find_youtube_candidates(
        self, spotify_song: Song, limit: int = 5
    ) -> list[Song]:
        """Search YouTube for `"{artist} {title}"` to find download candidates.

        This is the first step of the Spotify→YouTube cross-reference flow.
        Returns YouTube Songs (with real URLs) that the user can pick from.
        """
        query = f"{spotify_song.artist} {spotify_song.title}"
        return self._youtube.search(query, limit=limit)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download(
        self,
        song: Song,
        metadata_song: Song | None = None,
        *,
        download_dir: str | Path | None = None,
    ) -> Song:
        """Download `song` (must have a real audio URL) and tag it.

        Parameters
        ----------
        song:
            The YouTube/SoundCloud song whose URL will be downloaded.
        metadata_song:
            If given (Spotify cross-ref path), its display fields override
            `song`'s display fields in the tags and the stored DB row.
        download_dir:
            Override the library's default download directory for this call.

        Returns
        -------
        Song
            A new Song with `file_path` set (plus Spotify display fields merged
            in if `metadata_song` was provided). The returned object is what
            gets stored in the database.

        Raises
        ------
        yt_dlp.utils.DownloadError
            If the URL is invalid or unreachable.
        ValueError
            If `song.url` is None.
        """
        dest = Path(download_dir) if download_dir else self._download_dir
        file_path = self._downloader.download(song, dest_dir=dest)

        # Fetch cover art from whichever song has a cover_url (Spotify has it;
        # YouTube usually doesn't). A failed fetch returns None — never blocks.
        meta = metadata_song or song
        cover_bytes = _fetch_cover(meta.cover_url)

        # Build the final Song that gets tagged and persisted.
        # Audio identity (platform/source_id/url) always comes from `song`
        # (the real downloaded YouTube/SoundCloud track). Display metadata
        # (title/artist/album/duration/cover_url) comes from metadata_song when
        # provided (Spotify cross-ref path), else from song itself.
        final_song = dataclasses.replace(
            song,
            title=meta.title,
            artist=meta.artist,
            album=meta.album,
            duration=meta.duration,
            cover_url=meta.cover_url,
            file_path=str(file_path),
        )

        self._tagger.tag(file_path, final_song, cover_bytes=cover_bytes)
        self._db.add_song(final_song)

        return final_song

    # ------------------------------------------------------------------
    # Playlist passthroughs
    # ------------------------------------------------------------------

    def create_playlist(self, name: str) -> int:
        """Create a playlist; returns its database id."""
        return self._db.create_playlist(name)

    def get_playlists(self) -> list[dict[str, Any]]:
        """Return all playlists as [{id, name}, ...]."""
        return self._db.get_playlists()

    def get_playlist_by_name(self, name: str) -> dict[str, Any] | None:
        return self._db.get_playlist_by_name(name)

    def add_to_playlist(self, playlist_id: int, song: Song) -> None:
        """Add a song to a playlist (appends at the end)."""
        self._db.add_song_to_playlist(playlist_id, song)

    def remove_from_playlist(self, playlist_id: int, song: Song) -> None:
        """Remove a song from a playlist."""
        self._db.remove_song_from_playlist(playlist_id, song)

    def get_playlist_songs(self, playlist_id: int) -> list[Song]:
        """Return the songs in a playlist, in playback order."""
        return self._db.get_playlist_songs(playlist_id)

    def get_all_songs(self) -> list[Song]:
        """Return every downloaded song from the database."""
        return self._db.get_all_songs()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_cover(url: str | None) -> bytes | None:
    """Fetch album-art bytes from `url`; return None on any failure.

    Why here rather than in Tagger?
      - Tagger is an offline bytes-to-file writer. Keeping the HTTP call here
        maintains that boundary: anything that touches the network lives in the
        manager or sources layer.

    Why catch broadly?
      - requests can raise: ConnectionError, Timeout, TooManyRedirects,
        HTTPError, SSLError, and others. Enumerating them all is fragile — the
        requests library explicitly says to catch `requests.RequestException` as
        the base. We also guard against non-OK HTTP status with an early return.
      - A missing cover is a cosmetic failure; the download must still complete.
    """
    if url is None:
        return None
    try:
        response = requests.get(url, timeout=10)
        if not response.ok:
            return None
        return response.content
    except requests.RequestException:
        # Catches all requests errors: network down, timeout, SSL, DNS failure.
        return None
