"""
Search sources — one class per platform, sharing the MusicSource ABC.

Why an Abstract Base Class?
  - An ABC declares a contract: any subclass MUST implement `search()`. Without
    it you'd discover a missing implementation only at runtime (AttributeError).
    With `@abstractmethod` Python raises TypeError at instantiation time if you
    forgot to implement it — the mistake surfaces earlier.
  - Polymorphism: MusicLibrary can call `source.search(query)` on a list of
    sources without knowing or caring which platform each one wraps. Adding a
    new platform (e.g. Apple Music) is just a new subclass — no changes to the
    caller.

Spotify vs. YouTube/SoundCloud search mechanics
  - Spotify: network call goes through the injected spotipy client (we never
    construct a spotipy.Spotify inside the class — it's passed in so tests can
    inject a fake). Returns JSON with `duration_ms` (milliseconds), which we
    convert to seconds to match the Song model's `duration` field.
  - YouTube/SoundCloud: yt-dlp in `flat` (search-only) mode. `ytsearch5:query`
    tells yt-dlp to search YouTube and return (at most) 5 entries without
    downloading. `scsearch5:query` does the same for SoundCloud. `quiet=True`
    suppresses console output during tests. We never set a `download` option
    here — this class is search only; Downloader handles download opts.

Why inject the spotipy client (constructor arg) but build YoutubeDL inside the method?
  - spotipy.Spotify is stateful (it caches OAuth tokens), so we want one shared
    instance across the app's lifetime — injecting it lets the caller (and tests)
    control that lifetime.
  - YoutubeDL is a single-use context manager: each call gets fresh opts that
    include the search prefix. Building it per call is clean and matches yt-dlp's
    intended usage pattern.

Graceful degradation
  - Both yt-dlp sources catch Exception broadly on each entry during parsing.
    A single malformed entry (missing `id`, None `title`, etc.) should not crash
    the entire search — we skip it. The broad catch is intentional and noted.
"""

from __future__ import annotations

import abc
from typing import Any

import yt_dlp

from library.models import Song


class MusicSource(abc.ABC):
    """Abstract base: every search source must implement `search`."""

    @abc.abstractmethod
    def search(self, query: str, limit: int = 5) -> list[Song]:
        """Search for `query` and return up to `limit` Song objects."""
        ...


# ---------------------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------------------

class SpotifySource(MusicSource):
    """Searches Spotify for tracks.

    Audio download from Spotify is not possible (they don't expose streams), so
    Song.url is always None for Spotify results — they're metadata-only rows
    used either for display or as the metadata_song in a cross-ref download.
    """

    def __init__(self, client: Any) -> None:
        """
        Parameters
        ----------
        client:
            A `spotipy.Spotify` instance, already authenticated. Injected so
            tests can pass a Mock without touching the network.
        """
        self._client = client

    def search(self, query: str, limit: int = 5) -> list[Song]:
        """Call the Spotify Search API and return up to `limit` Songs.

        Spotify's search endpoint returns a page of tracks under
        `results["tracks"]["items"]`. Each item is a dict with nested dicts for
        album and artists. We extract only what Song needs.

        Catches:
          - Any exception from the spotipy client (network error, rate limit,
            bad credentials). A source failure should degrade to [] rather than
            crash the app — the caller (MusicLibrary.search_all) catches this
            too, but defensive parsing here keeps the per-entry logic clean.
        """
        try:
            results = self._client.search(q=query, type="track", limit=limit)
        except Exception:
            # Network error, rate limit, or auth failure — degrade to empty.
            return []

        songs: list[Song] = []
        for item in results.get("tracks", {}).get("items", []):
            try:
                songs.append(_parse_spotify_track(item))
            except Exception:
                # Malformed entry — skip it; don't crash the whole search.
                continue
        return songs


def _parse_spotify_track(item: dict[str, Any]) -> Song:
    """Convert a single Spotify track dict into a Song.

    Spotify reports duration in milliseconds; Song.duration is in seconds.
    Cover URL: Spotify orders images largest-first, so images[0] is the biggest.
    We take it or fall back to None if the list is empty.
    """
    album = item.get("album", {})
    images: list[dict[str, Any]] = album.get("images", [])
    cover_url: str | None = images[0]["url"] if images else None

    artists = item.get("artists", [])
    artist_name = artists[0]["name"] if artists else ""

    duration_ms: int = item.get("duration_ms", 0)
    duration_s: int = duration_ms // 1000  # ms → s

    return Song(
        title=item.get("name", ""),
        artist=artist_name,
        album=album.get("name", ""),
        duration=duration_s,
        platform="spotify",
        source_id=item.get("id", ""),
        url=None,           # Spotify is metadata-only; no audio URL
        cover_url=cover_url,
    )


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

class YouTubeSource(MusicSource):
    """Searches YouTube using yt-dlp's `ytsearch` prefix.

    yt-dlp in flat-extract mode returns metadata for search results without
    downloading anything. The `ytsearch{limit}:{query}` URL prefix is yt-dlp's
    built-in YouTube search trigger.
    """

    def search(self, query: str, limit: int = 5) -> list[Song]:
        """
        Catches:
          - Exception from YoutubeDL (network unavailable, yt-dlp internals).
            Degrades to []. Comments say 'broad' because we genuinely can't
            enumerate all yt-dlp error types.
        """
        opts = _ytdlp_search_opts(f"ytsearch{limit}:{query}")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        except Exception:
            # Broad catch: yt-dlp raises a variety of internal exception types
            # (DownloadError, ExtractorError, etc.) — degrade to [] rather than
            # exposing yt-dlp internals to callers.
            return []

        return _parse_ytdlp_entries(info, platform="youtube")


# ---------------------------------------------------------------------------
# SoundCloud
# ---------------------------------------------------------------------------

class SoundCloudSource(MusicSource):
    """Searches SoundCloud using yt-dlp's `scsearch` prefix.

    Identical mechanics to YouTubeSource — yt-dlp abstracts the platform
    difference. The only change is the URL prefix (`scsearch` vs `ytsearch`).
    """

    def search(self, query: str, limit: int = 5) -> list[Song]:
        """
        Catches:
          - Exception from YoutubeDL (same as YouTubeSource).
        """
        opts = _ytdlp_search_opts(f"scsearch{limit}:{query}")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
        except Exception:
            # Broad catch: same rationale as YouTubeSource.search.
            return []

        return _parse_ytdlp_entries(info, platform="soundcloud")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ytdlp_search_opts(url: str) -> dict[str, Any]:
    """Build yt-dlp options for a flat (search-only, no download) extraction.

    `extract_flat="in_playlist"` means: when yt-dlp gets a search result (which
    is a playlist of results), return the entries as lightweight dicts rather
    than fully resolving each video's page. This makes search fast.
    `quiet=True` / `no_warnings=True` suppress console chatter.
    """
    return {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
    }


def _parse_ytdlp_entries(info: dict[str, Any] | None, *, platform: str) -> list[Song]:
    """Convert yt-dlp search result entries into Song objects.

    `info` may be None if yt-dlp returned nothing. Each entry is a flat dict
    with fields like `id`, `title`, `uploader`, `duration`, `webpage_url`.
    All are optional in flat mode — we guard every access.
    """
    if not info:
        return []

    songs: list[Song] = []
    for entry in info.get("entries", []):
        try:
            songs.append(_parse_ytdlp_entry(entry, platform=platform))
        except Exception:
            # Skip malformed entries — a partial result is better than a crash.
            continue
    return songs


def _parse_ytdlp_entry(entry: dict[str, Any], *, platform: str) -> Song:
    """Parse one flat yt-dlp entry into a Song.

    duration: int | None — flat entries often omit it; we tolerate None.
    uploader may be missing in some SoundCloud entries.
    """
    duration_raw = entry.get("duration")
    duration: int | None = int(duration_raw) if duration_raw is not None else None

    return Song(
        title=entry.get("title") or "",
        artist=entry.get("uploader") or "",
        album="",           # yt-dlp flat search doesn't return album metadata
        duration=duration,
        platform=platform,
        source_id=entry.get("id") or "",
        url=entry.get("webpage_url") or entry.get("url") or "",
        cover_url=None,     # flat search entries don't include thumbnails
    )
