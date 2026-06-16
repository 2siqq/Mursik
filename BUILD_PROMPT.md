ROLE & CONTEXT
You are an expert Python developer and a thoughtful mentor. You are building a
complete, working reference implementation that I will study to deepen my
Python. I have just completed Harvard's CS50P, so I'm solid on: functions,
exceptions, file I/O, regular expressions, classes and OOP, unit testing with
pytest, and writing my own modules. I'm new to: third-party APIs, OAuth, async,
the textual framework, and SQLite. Write idiomatic, well-structured code and
explain your *design choices* in comments — reinforce why a class, method,
abstraction, or test is shaped the way it is. Don't re-explain language basics;
do explain anything genuinely new.

PROJECT
A terminal user interface (TUI) music manager. It searches across Spotify,
YouTube, and SoundCloud, downloads the best-quality audio available, tags files
with metadata, and manages manually-curated playlists. Local, single-user,
personal use.

WORKFLOW (IMPORTANT)
Before writing ANY code, explain your full plan: the class design, the module
layout, the libraries, the testing approach, and the build order. Then STOP and
wait for my explicit go-ahead. Do not write code until I approve the plan.

ARCHITECTURE — OBJECT-ORIENTED, TESTED
Design this with classes from the start, and write pytest tests alongside the
code (not as an afterthought). Suggested object model — adapt if you have a
cleaner design, but justify any change:
- Song (dataclass): title, artist, album, duration, platform, source_id/url,
  local file_path. The consistent shape returned by every search.
- MusicSource (abstract base class): defines search(query) -> list[Song].
  Concrete subclasses SpotifySource, YouTubeSource, SoundCloudSource each
  implement it. Use this polymorphism so the rest of the app treats all
  platforms uniformly.
- Downloader: wraps yt-dlp; best-quality selection; cross-references a Spotify
  Song to find and download its YouTube equivalent.
- Tagger: wraps mutagen; writes metadata + cover art into a downloaded file.
- Database: wraps sqlite3; methods for songs and playlists; uses a
  playlist_songs join table; parameterized queries only.
- Playlist: represents a playlist and its ordered songs; add/remove methods.
- MusicLibrary (coordinator/façade): ties sources, downloader, tagger, and
  database together; the single object the TUI talks to.
- A textual App subclass for the UI layer.

TESTING
- Use pytest. Write tests for all non-UI logic: Song, Playlist, Database
  (against in-memory or temp-file SQLite), the Spotify→YouTube cross-referencing,
  and result parsing.
- Mock all network and yt-dlp calls so the suite never touches the internet
  (pytest monkeypatch or unittest.mock). Explain in comments what each mock
  stands in for.
- Organize tests in a tests/ directory mirroring the module layout. Note: this
  repo already has tests/conftest.py with fixtures (in-memory db, sample song
  data, fake yt-dlp response) and tests/test_example.py demonstrating the
  patterns. Build on those; replace the example file as real tests appear.

FUNCTIONAL REQUIREMENTS
1. Unified search: one query searches all three platforms; show top 5 per platform.
2. Drill-down: I can select one platform to see more results from it.
3. Download: selecting a song downloads best available audio. Spotify selections
   are cross-referenced and downloaded from YouTube.
4. Metadata: downloaded files are tagged (title, artist, album, cover art) using
   Spotify's metadata where available.
5. Database: persist all downloaded songs and playlists in SQLite (join table).
6. Playlists: I create playlists and add/remove songs manually. No auto-generation.
7. Always download best quality.

OUT OF SCOPE NOW (but design so they slot in cleanly later)
- No AI / natural-language features yet — isolate logic so a future ai.py /
  AIAssistant class can be added without a rewrite.
- No Apple Music.
- No automatic playlist suggestions.

PROJECT-QUALITY CONSTRAINTS
- Clear module boundaries; one class per responsibility (small related
  dataclasses may share a file).
- Type hints throughout (I use them — keep them consistent).
- This repo already contains: CLAUDE.md, README.md, requirements.txt,
  .env.example, .gitignore, and the tests/ folder above. Read CLAUDE.md first;
  it has the stack, conventions, and rules. Use python-dotenv to load secrets
  from .env (the user adds real keys there; never hardcode them).
- Update requirements.txt and the README as you add real modules.
- Handle errors gracefully (network failures, rate limits, missing files) and
  explain what each handler catches.

OUTPUT FORMAT FOR YOUR PLAN
- One-paragraph architecture summary.
- The class model (each class → its responsibility and key methods).
- The module/file layout, including the tests directory.
- Your testing strategy (what you'll mock, what you'll assert).
- The build order you recommend, and why.
- Then stop and ask for my go-ahead. Do not write code until I approve.

INTERFACE DESIGN
(Design Option A — the single-screen layout. To use a different design, replace
this whole section with Option B or C.)

One screen, with no navigation between separate views. A search bar is pinned at
the top. Below it, a results table is grouped under platform headers
(Spotify / YouTube / SoundCloud), showing 5 rows each. Arrow keys move through
the rows; pressing Enter on a row downloads that song. A keybinding (for example,
"p") opens a playlist panel as a modal overlay on top of the current screen. A
footer shows the active keybindings at all times. Everything the user does
happens on this one screen — searching, browsing results, downloading, and
managing playlists via the overlay. Favor the simplest textual widgets that
achieve this (an Input for the search bar, a DataTable or ListView for results,
a ModalScreen for the playlist overlay, and a Footer for keybindings).
