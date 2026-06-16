"""
ID3 tag writer — the only module that touches mutagen.

Why mutagen's ID3 API rather than EasyID3?
  - EasyID3 exposes a restricted set of tag names as a simple dict (e.g.,
    `tags["title"]`). It's convenient for common text frames but deliberately
    excludes binary frames like APIC (Attached Picture — the cover art).
  - To write cover art we need the raw `ID3` interface where we construct tag
    frames directly (TIT2, TPE1, TALB, TRCK, APIC, etc.).
  - Decision: use `ID3` for everything (text + picture) so there's one
    interface, one save call, and no need to open the file twice.

Why `ID3NoHeaderError`?
  - A freshly written MP3 (or any audio file that's never been tagged) has no
    ID3 header block. `ID3(path)` raises `ID3NoHeaderError` in that case.
  - The fix: catch the exception and create an empty `ID3()` object, add our
    frames, then call `.save(path)` to write the header. Same result; no crash.

Cover art (`APIC` frame):
  - `cover_bytes` is raw image data (JPEG or PNG). The `mime` type matters for
    players that display the art; we detect JPEG vs PNG by checking the magic
    bytes. `type=3` means "Front cover" in the ID3 spec.
  - If `cover_bytes` is None we just skip the APIC frame — a download without
    cover art is better than a failed download.

Error handling:
  - `tag()` catches `FileNotFoundError` (the file wasn't written, e.g. yt-dlp
    failed silently) and `mutagen.MutagenError` (the base error class for all
    mutagen errors — wraps I/O errors and parse failures). Both handler comments
    state what they catch; callers receive False so the download isn't rolled back.
"""

from __future__ import annotations

from pathlib import Path

import mutagen
from mutagen.id3 import (
    APIC,
    ID3,
    ID3NoHeaderError,
    TALB,
    TIT2,
    TPE1,
)

from library.models import Song


class Tagger:
    """Writes ID3 tags to an MP3 file. One method, no state."""

    def tag(
        self,
        file_path: str | Path,
        song: Song,
        cover_bytes: bytes | None = None,
    ) -> bool:
        """Write ID3 tags to `file_path` from `song` and optional cover art.

        Parameters
        ----------
        file_path:
            Path to the MP3 file. Must already exist (Downloader writes it
            before Tagger is called).
        song:
            Source of display metadata (title, artist, album).
        cover_bytes:
            Raw JPEG or PNG image data for the front cover. If None, no
            APIC frame is written — cover art is optional.

        Returns
        -------
        bool
            True on success, False if the file was missing or mutagen raised.
        """
        path = Path(file_path)

        try:
            # Open existing ID3 header, or start fresh if there isn't one yet.
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                # New file with no ID3 block — create an empty container.
                tags = ID3()

            # Text frames: TIT2=title, TPE1=artist, TALB=album.
            # The `encoding=3` arg means UTF-8 — always the right choice.
            tags.add(TIT2(encoding=3, text=song.title))
            tags.add(TPE1(encoding=3, text=song.artist))
            tags.add(TALB(encoding=3, text=song.album))

            if cover_bytes is not None:
                mime = _detect_mime(cover_bytes)
                tags.add(
                    APIC(
                        encoding=3,
                        mime=mime,
                        type=3,     # 3 = Front cover in the ID3 spec
                        desc="Cover",
                        data=cover_bytes,
                    )
                )

            tags.save(path)
            return True

        except FileNotFoundError:
            # The MP3 file wasn't written (yt-dlp may have failed silently).
            return False
        except mutagen.MutagenError:
            # mutagen.MutagenError is the base error class for the whole mutagen
            # library (mutagen.id3.error is a subclass). We catch the base here
            # so both ID3-specific errors and lower-level I/O errors (e.g.
            # "no such file") that mutagen wraps as MutagenError are handled.
            return False


def _detect_mime(data: bytes) -> str:
    """Detect JPEG vs PNG from magic bytes; fall back to JPEG.

    JPEG files start with the SOI marker FF D8.
    PNG files start with the 8-byte PNG signature 89 50 4E 47.
    We check just the first few bytes — enough to distinguish them.
    """
    if data[:4] == b"\x89PNG":
        return "image/png"
    return "image/jpeg"  # reasonable default for album art
