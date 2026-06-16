"""
SQLite persistence layer — the only place sqlite3 is touched in this codebase.

Why a wrapper class rather than raw sqlite3 calls scattered through the app?
  - A single class means one place to change the schema or connection settings.
  - It's trivially replaceable (e.g., swap to PostgreSQL later) because the
    rest of the code calls method names, not SQL strings.
  - It's independently testable with an in-memory database — no test disk I/O.

Why `row_factory = sqlite3.Row`?
  - By default sqlite3 returns plain tuples, so row[0] is the first column —
    fragile if the column order changes. sqlite3.Row lets us use row["title"]
    which is column-order-independent and far more readable.

Why parameterized queries only (no f-strings / concatenation)?
  - SQLite's ? placeholders let the driver handle escaping. Without them,
    user-supplied strings (like a track title that contains a quote) could
    break or inject arbitrary SQL — a classic SQL injection risk.
  - It's also a habit the CLAUDE.md spec enforces unconditionally.

Schema overview
---------------
songs           — one row per downloaded track
playlists       — one row per named playlist
playlist_songs  — join table that maps playlists to songs with an explicit
                  `position` column so ORDER BY gives consistent playback order.
                  We use MAX(position)+1 on insert (not COUNT, which would break
                  after a remove leaves gaps) and never renumber on remove — gaps
                  are fine as long as we sort by position.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from library.models import Song


# ---------------------------------------------------------------------------
# DDL — run once on every Database.__init__ (CREATE TABLE IF NOT EXISTS)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    artist      TEXT    NOT NULL,
    album       TEXT    NOT NULL,
    duration    INTEGER,
    platform    TEXT    NOT NULL,
    source_id   TEXT    NOT NULL UNIQUE,
    url         TEXT,
    file_path   TEXT,
    cover_url   TEXT
);

CREATE TABLE IF NOT EXISTS playlists (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS playlist_songs (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    song_id     INTEGER NOT NULL REFERENCES songs(id)     ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, song_id)
);
"""


class Database:
    """sqlite3 wrapper.  All public methods accept/return domain objects or
    plain Python types — callers never write SQL.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        """Open (or create) the database file and ensure the schema exists.

        Passing ":memory:" (the default) creates an in-memory database useful
        for tests and for first-launch without a file path yet. In normal use
        the TUI passes the real on-disk path.

        `check_same_thread=False` is safe here because the TUI runs downloads on
        worker threads (not the DB thread) but all DB writes go through this
        single object on the main thread. If you later add concurrent writes you'd
        need a connection pool or a write-serialization queue.
        """
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Foreign-key enforcement is off by default in SQLite; turn it on so
        # ON DELETE CASCADE in playlist_songs actually works.
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Songs
    # ------------------------------------------------------------------

    def add_song(self, song: Song) -> Song:
        """Persist a Song row; returns the same song (useful for chaining).

        INSERT OR REPLACE handles the case where you re-download and update
        the file_path of an existing track (matched on UNIQUE source_id).
        """
        self._conn.execute(
            """
            INSERT INTO songs (title, artist, album, duration, platform,
                               source_id, url, file_path, cover_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                title     = excluded.title,
                artist    = excluded.artist,
                album     = excluded.album,
                duration  = excluded.duration,
                url       = excluded.url,
                file_path = excluded.file_path,
                cover_url = excluded.cover_url
            """,
            (
                song.title,
                song.artist,
                song.album,
                song.duration,
                song.platform,
                song.source_id,
                song.url,
                song.file_path,
                song.cover_url,
            ),
        )
        self._conn.commit()
        return song

    def get_song(self, source_id: str) -> Song | None:
        """Fetch a single song by its platform source_id; None if not found."""
        row = self._conn.execute(
            "SELECT * FROM songs WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return _row_to_song(row) if row else None

    def get_all_songs(self) -> list[Song]:
        """Return every downloaded song, ordered by title."""
        rows = self._conn.execute(
            "SELECT * FROM songs ORDER BY title"
        ).fetchall()
        return [_row_to_song(r) for r in rows]

    # ------------------------------------------------------------------
    # Playlists
    # ------------------------------------------------------------------

    def create_playlist(self, name: str) -> int:
        """Insert a new playlist; returns the new row's id.

        Raises sqlite3.IntegrityError on duplicate name — callers can catch
        that to show a "name already taken" error.
        """
        cursor = self._conn.execute(
            "INSERT INTO playlists (name) VALUES (?)",
            (name,),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_playlists(self) -> list[dict[str, object]]:
        """Return all playlists as a list of {id, name} dicts."""
        rows = self._conn.execute(
            "SELECT id, name FROM playlists ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_playlist_by_name(self, name: str) -> dict[str, object] | None:
        """Look up a playlist by name; returns {id, name} or None."""
        row = self._conn.execute(
            "SELECT id, name FROM playlists WHERE name = ?",
            (name,),
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Playlist ↔ Song join
    # ------------------------------------------------------------------

    def add_song_to_playlist(self, playlist_id: int, song: Song) -> None:
        """Append a song to a playlist.

        Position is computed as MAX(position)+1 so existing positions are never
        renumbered and gaps left by removes don't cause conflicts. COALESCE
        handles an empty playlist (where MAX returns NULL) by defaulting to 0.
        """
        # First make sure the song row exists.
        self.add_song(song)
        song_row = self._conn.execute(
            "SELECT id FROM songs WHERE source_id = ?",
            (song.source_id,),
        ).fetchone()
        song_id = song_row["id"]

        self._conn.execute(
            """
            INSERT OR IGNORE INTO playlist_songs (playlist_id, song_id, position)
            VALUES (
                ?,
                ?,
                COALESCE((SELECT MAX(position) FROM playlist_songs WHERE playlist_id = ?), -1) + 1
            )
            """,
            (playlist_id, song_id, playlist_id),
        )
        self._conn.commit()

    def remove_song_from_playlist(self, playlist_id: int, song: Song) -> None:
        """Remove a song from a playlist. Gaps in position are intentional."""
        song_row = self._conn.execute(
            "SELECT id FROM songs WHERE source_id = ?",
            (song.source_id,),
        ).fetchone()
        if not song_row:
            return  # Song not in DB at all — nothing to remove.
        self._conn.execute(
            "DELETE FROM playlist_songs WHERE playlist_id = ? AND song_id = ?",
            (playlist_id, song_row["id"]),
        )
        self._conn.commit()

    def get_playlist_songs(self, playlist_id: int) -> list[Song]:
        """Return the songs in a playlist, sorted by their insertion position."""
        rows = self._conn.execute(
            """
            SELECT s.*
            FROM songs s
            JOIN playlist_songs ps ON ps.song_id = s.id
            WHERE ps.playlist_id = ?
            ORDER BY ps.position
            """,
            (playlist_id,),
        ).fetchall()
        return [_row_to_song(r) for r in rows]

    def close(self) -> None:
        """Explicitly close the connection.  Usually not needed — the connection
        closes when the object is garbage-collected, but explicit is better for
        long-running processes that open many DBs.
        """
        self._conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_song(row: sqlite3.Row) -> Song:
    """Convert a sqlite3.Row to a Song domain object.

    This is a module-level function (not a static method on Database) because
    it only works with data, not connection state — it has no business being on
    the class.
    """
    return Song(
        title=row["title"],
        artist=row["artist"],
        album=row["album"],
        duration=row["duration"],       # may be None — Song tolerates that
        platform=row["platform"],
        source_id=row["source_id"],
        url=row["url"],
        file_path=row["file_path"],     # None until downloaded
        cover_url=row["cover_url"],
    )
