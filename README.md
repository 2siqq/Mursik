# TUI Music Manager

A local, single-user terminal application for searching, downloading, and
organizing music. It searches Spotify, YouTube, and SoundCloud at once,
downloads the best-quality audio available, tags files with metadata, and
manages manually-curated playlists. Personal use only.

## Requirements

- Python 3.10 or newer
- A free Spotify Developer account (used for search and metadata)
- ffmpeg installed and on your PATH — yt-dlp uses it to extract and convert audio

## Setup

1. Create and activate a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate        # Windows: venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Add your Spotify credentials:

   - Go to https://developer.spotify.com/dashboard and create an app.
   - Copy the Client ID and Client Secret.
   - Copy the example env file and paste them in:

     ```bash
     cp .env.example .env        # Windows: copy .env.example .env
     ```

   The real `.env` is git-ignored — never commit it.

## Running

```bash
python app.py
```

## Testing

```bash
pytest
```

Tests mock all network and yt-dlp calls, so the suite runs offline and never
contacts the real APIs. The included example tests pass as-is, so you can run
`pytest` right now to confirm your environment is set up correctly.

## Intended project structure

This is the layout the build targets. The `library/` modules are created during
the build — this skeleton ships the setup files and the test scaffolding.

```
.
├── app.py              # textual TUI entry point
├── library/            # core logic (built during the build)
│   ├── models.py       # Song dataclass, Playlist
│   ├── sources.py      # MusicSource ABC + Spotify/YouTube/SoundCloud sources
│   ├── downloader.py   # yt-dlp wrapper: best-quality + Spotify→YouTube cross-ref
│   ├── tagger.py       # mutagen metadata writer
│   ├── database.py     # sqlite3 wrapper, playlist_songs join table
│   └── manager.py      # MusicLibrary coordinator the TUI talks to
├── tests/              # pytest suite (mirrors library/)
│   ├── conftest.py     # shared fixtures
│   └── test_example.py # example tests demonstrating the patterns used here
├── requirements.txt
├── .env.example
└── .gitignore
```

## Notes

- Spotify is used for search and metadata only; audio is downloaded from
  YouTube/SoundCloud via yt-dlp.
- AI features and Apple Music are intentionally out of scope for this version.
