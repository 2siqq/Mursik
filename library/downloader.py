"""
yt-dlp audio downloader — the only module that writes files from URLs.

Responsibility: given a URL (always YouTube or SoundCloud), download the
best-quality audio and transcode it to MP3 320 kbps via ffmpeg. Return the
path to the written file.

Why is cross-referencing NOT here?
  - Cross-referencing (finding the YouTube match for a Spotify song) is a
    *search* operation that reuses YouTubeSource.search(). Keeping it in the
    façade (MusicLibrary) means Downloader stays single-responsibility:
    "turn a URL into a best-quality file." The façade can change search/cross-
    ref logic without touching the download mechanics.

Why MP3 320 kbps (not FLAC or the raw best stream)?
  - Decided in the working agreement. MP3 at 320 kbps is indistinguishable to
    most listeners from lossless, plays everywhere, and is what mutagen's EasyID3
    tags target. FLAC would need different tagging and more disk.

Why `preferredcodec: "mp3"` + `preferredquality: "320"`?
  - yt-dlp's FFmpegExtractAudio postprocessor runs ffmpeg after the download to
    transcode the audio stream (which might be AAC, Opus, WebM, etc.) into MP3.
    `preferredquality` becomes ffmpeg's `-b:a 320k` flag. Without this we'd get
    whatever codec the source serves.

Why `outtmpl`?
  - Without an explicit output template, yt-dlp uses the video title and writes
    to the current working directory. We want files in `dest_dir` with a
    predictable name pattern so the Tagger can find the file afterward.

Error handling:
  - yt_dlp.utils.DownloadError is the most common error (bad URL, network
    failure, ffmpeg not on PATH). We let it bubble — the façade's `download`
    method is the right place to catch it and report to the user.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yt_dlp

from library.models import Song


class Downloader:
    """Downloads a single song from a URL as MP3 320 kbps.

    No state beyond the optional output directory default — each `download()`
    call is independent.
    """

    def download(self, song: Song, dest_dir: str | Path = "downloads") -> Path:
        """Download `song.url` to `dest_dir`, returning the written file path.

        Parameters
        ----------
        song:
            The song to download. `song.url` must be a real playback URL (i.e.
            a YouTube or SoundCloud URL, never a Spotify song).
        dest_dir:
            Directory to write the MP3 into. Created if it doesn't exist.

        Returns
        -------
        Path
            The path to the written MP3 file.

        Raises
        ------
        yt_dlp.utils.DownloadError
            If the URL is invalid, the network is unavailable, or ffmpeg is
            missing from PATH. Callers should catch this and surface the error.
        ValueError
            If `song.url` is None (Spotify songs have no audio URL).
        """
        if song.url is None:
            raise ValueError(
                f"Song '{song.title}' has no audio URL — "
                "Spotify songs must be cross-referenced to YouTube before downloading."
            )

        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)

        opts = self._build_opts(dest)

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([song.url])

        # yt-dlp appends the postprocessed extension; reconstruct the path.
        # The outtmpl produces "<dest>/<source_id>.mp3" after ffmpeg transcoding.
        return dest / f"{song.source_id}.mp3"

    def _build_opts(self, dest: Path) -> dict[str, Any]:
        """Construct the yt-dlp options dict.

        Kept in its own method so tests can assert on the opts structure without
        running a real download.
        """
        return {
            # Download the single best audio stream (not video).
            "format": "bestaudio/best",
            # Output template: <dest_dir>/<source_id>.<ext>.  yt-dlp replaces
            # %(id)s with the video ID and %(ext)s with the post-processed extension.
            "outtmpl": str(dest / "%(id)s.%(ext)s"),
            # Postprocessor: transcode whatever audio stream yt-dlp fetched into
            # MP3 at 320 kbps via ffmpeg.
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                }
            ],
            # Suppress console output — the TUI handles progress display.
            "quiet": True,
            "no_warnings": True,
        }
