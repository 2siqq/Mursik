# CLAUDE.md

## Project
A local, single-user terminal UI (TUI) music manager. It searches across
Spotify, YouTube, and SoundCloud, downloads the best-quality audio, tags files
with metadata, and manages manually-curated playlists. Personal use only.

## Who I am
I've completed CS50P. I'm solid on functions, exceptions, file I/O, regex,
classes/OOP, pytest, and writing my own modules. I'm new to third-party APIs,
OAuth, async, the textual framework, and SQLite. I'm building this to deepen my
Python by studying a well-built implementation. In comments, explain your
*design choices* — why a class, method, abstraction, or test is shaped the way
it is — and explain anything genuinely new (API patterns, async, SQLite,
textual). Don't re-explain language basics.

## Tech stack
- Python 3 with type hints throughout, run inside the venv
- TUI: textual
- Spotify: spotipy — search + metadata ONLY (no audio download)
- YouTube + SoundCloud: yt-dlp — search + best-quality audio download
- Tagging: mutagen
- Database: sqlite3 (built-in)
- Tests: pytest
- Secrets: python-dotenv reading from .env (never commit it)

## Architecture (OOP, tested)
Object model:
- Song (dataclass) — consistent shape returned by every search
- MusicSource (ABC) → SpotifySource, YouTubeSource, SoundCloudSource; each
  implements search(query) -> list[Song]
- Downloader — yt-dlp wrapper; best-quality + Spotify→YouTube cross-reference
- Tagger — mutagen wrapper
- Database — sqlite3 wrapper; playlist_songs join table; parameterized queries only
- Playlist — a playlist and its ordered songs
- MusicLibrary — coordinator/façade the TUI talks to
- textual App subclass — the UI

Tests live in tests/ mirroring the modules. Mock all network/yt-dlp calls — the
suite never hits the internet.

## Conventions
- One class per responsibility (small related dataclasses may share a file).
- Type hints throughout.
- Parameterized SQL only — never f-strings/concatenation in queries.
- Secrets from .env; keep .env.example current.
- Comment to explain design intent, not line-by-line mechanics.
- Handle errors gracefully (network, rate limits, missing files); note what each
  handler catches.

## Scope
In scope now: Spotify (search/metadata), YouTube (search+download), SoundCloud
(search+download), manual playlists, best-quality downloads.
Out of scope (architect so they slot in later): AI features (isolate so an
ai.py / AIAssistant can be added without a rewrite), Apple Music, auto-suggestions.

## Working agreement
- For any non-trivial change, explain your plan (classes/files touched, test
  impact), then wait for my go-ahead.
- Write or update pytest tests alongside any logic change.
- After changes, summarize what changed and why in plain English.
- If I'm heading toward a bad design or habit, say so before proceeding.

## Setup, run, test
- venv → `pip install -r requirements.txt`
- Copy `.env.example` to `.env`, add Spotify keys (developer.spotify.com)
- Run: `python app.py`
- Test: `pytest`

## Never
- Never hardcode API keys.
- Never commit .env.
- Never use string-formatted SQL.