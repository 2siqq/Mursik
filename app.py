"""
MusicApp — the textual TUI entry point.

Architecture notes
------------------
This is the only file in the project that imports textual. Every other module
is ignorant of the UI so it can be tested offline. The TUI's sole job is to
translate user input into MusicLibrary method calls and render the results.

Single-screen layout (Option A from the plan):
  - A pinned Input widget at the top for search queries.
  - A DataTable (grouped by platform) showing up to 5 results per platform.
  - A Footer with key bindings shown at the bottom.

Downloads run off the UI thread via textual's `@work` decorator. Without this,
a download would block the event loop and freeze the TUI for the duration.
`@work` runs the function in a thread pool; the TUI stays responsive. This is
the one place in the project where concurrency is introduced.

Modal screens
-------------
  - `CandidateScreen`: shown when the user presses Enter on a Spotify row.
    Fetches YouTube candidates via `library.find_youtube_candidates()`, displays
    them, and lets the user pick one to download with Spotify's metadata.
  - `PlaylistScreen`: full CRUD modal. Opened with `p`. Lets the user create
    playlists, add the selected song, remove songs, and view contents.

textual concepts introduced here (new to the project owner):
  - `App`: the top-level textual object. `compose()` builds the widget tree;
    `on_mount()` runs once after the widgets are rendered.
  - `Widget.compose()`: returns an iterable of child widgets (using `yield`).
  - `@on(EventClass)`: a message handler. textual routes events to these methods.
  - `ModalScreen`: a full-screen overlay that pauses the parent screen.
    `app.push_screen()` stacks it; the screen calls `self.dismiss(value)` to
    return a value to the parent's callback.
  - `@work`: decorator that runs the decorated method in a worker thread.
    Inside a @work method you cannot directly call `app.notify()` — use
    `self.app.call_from_thread(self.app.notify, ...)` to safely hand off to the
    main thread.
  - `DataTable`: a grid widget. Rows are added with `table.add_row()`. Each row
    has a `key` we use to look up the original Song.
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

from library.database import Database
from library.downloader import Downloader
from library.manager import MusicLibrary
from library.models import Song
from library.sources import SoundCloudSource, SpotifySource, YouTubeSource
from library.tagger import Tagger

load_dotenv()


# ---------------------------------------------------------------------------
# Modal: YouTube candidate picker (Spotify → YouTube cross-ref)
# ---------------------------------------------------------------------------

class CandidateScreen(ModalScreen[Song | None]):
    """Shows YouTube candidates for a Spotify song; user picks one to download.

    Type parameter `Song | None`: dismiss(song) passes the chosen Song back to
    the parent's callback; dismiss(None) means the user cancelled.

    Why a ModalScreen rather than a panel?
      - The user must pick before anything else can happen — a modal enforces
        that focus without the parent needing to track "are we in pick mode?".
        ModalScreen.dismiss() automatically restores the parent screen.
    """

    CSS = """
    CandidateScreen {
        align: center middle;
    }
    #candidate-container {
        width: 80;
        height: 24;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    #candidate-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #candidate-list {
        height: 1fr;
    }
    #cancel-btn {
        margin-top: 1;
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, spotify_song: Song, candidates: list[Song]) -> None:
        super().__init__()
        self._spotify_song = spotify_song
        self._candidates = candidates

    def compose(self) -> ComposeResult:
        with Vertical(id="candidate-container"):
            yield Label(
                f"YouTube matches for: {self._spotify_song.artist} — {self._spotify_song.title}",
                id="candidate-title",
            )
            with VerticalScroll(id="candidate-list"):
                items = []
                for i, c in enumerate(self._candidates):
                    dur = f"{c.duration}s" if c.duration else "?"
                    items.append(ListItem(Label(f"[{i+1}] {c.title} ({c.artist}) [{dur}]"), id=f"cand-{i}"))
                yield ListView(*items)
            yield Button("Cancel", id="cancel-btn", variant="default")

    @on(ListView.Selected)
    def on_candidate_selected(self, event: ListView.Selected) -> None:
        """User clicked (or pressed Enter on) a candidate row."""
        # Extract the index from the ListItem's id ("cand-0" → 0).
        idx = int(event.item.id.split("-")[1])
        self.dismiss(self._candidates[idx])

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#cancel-btn")
    def on_cancel_pressed(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Modal: Playlist CRUD
# ---------------------------------------------------------------------------

class PlaylistScreen(ModalScreen[None]):
    """Full CRUD for playlists.

    Opened with `p`. The screen:
      - Lists existing playlists.
      - Lets the user create a new playlist (by name).
      - If a song is selected in the main screen, offers "Add to playlist".
      - Lets the user view the songs in a playlist.
      - Lets the user remove the selected song from the selected playlist.
    """

    CSS = """
    PlaylistScreen {
        align: center middle;
    }
    #playlist-container {
        width: 80;
        height: 30;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    #pl-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #pl-name-input {
        margin-bottom: 1;
    }
    #pl-buttons {
        height: auto;
        margin-bottom: 1;
    }
    #pl-list {
        height: 8;
        border: solid $panel;
        margin-bottom: 1;
    }
    #pl-songs-label {
        text-style: bold;
    }
    #pl-songs-list {
        height: 8;
        border: solid $panel;
    }
    #close-btn {
        margin-top: 1;
        width: 100%;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, library: MusicLibrary, selected_song: Song | None) -> None:
        super().__init__()
        self._library = library
        self._selected_song = selected_song
        self._selected_playlist_id: int | None = None
        self._playlists: list[dict] = []
        self._playlist_songs: list[Song] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="playlist-container"):
            yield Label("Playlists", id="pl-title")
            yield Input(placeholder="New playlist name…", id="pl-name-input")
            with Vertical(id="pl-buttons"):
                yield Button("Create playlist", id="btn-create", variant="primary")
                if self._selected_song:
                    yield Button(
                        f"Add '{self._selected_song.title}' to selected playlist",
                        id="btn-add",
                        variant="success",
                    )
                yield Button("Remove selected song from playlist", id="btn-remove", variant="error")
            yield Label("Your playlists (click to view songs):", id="pl-list-label")
            yield ListView(id="pl-list")
            yield Label("Songs in selected playlist:", id="pl-songs-label")
            yield ListView(id="pl-songs-list")
            yield Button("Close", id="close-btn", variant="default")

    def on_mount(self) -> None:
        self._refresh_playlists()

    def _refresh_playlists(self) -> None:
        """Reload the playlist list from the database."""
        self._playlists = self._library.get_playlists()
        pl_view = self.query_one("#pl-list", ListView)
        pl_view.clear()
        for pl in self._playlists:
            pl_view.append(ListItem(Label(pl["name"]), id=f"pl-{pl['id']}"))

    def _refresh_playlist_songs(self) -> None:
        """Reload songs for the currently selected playlist."""
        if self._selected_playlist_id is None:
            return
        self._playlist_songs = self._library.get_playlist_songs(
            self._selected_playlist_id
        )
        songs_view = self.query_one("#pl-songs-list", ListView)
        songs_view.clear()
        for s in self._playlist_songs:
            songs_view.append(ListItem(Label(f"{s.title} — {s.artist}")))

    @on(ListView.Selected, "#pl-list")
    def on_playlist_selected(self, event: ListView.Selected) -> None:
        """User selected a playlist — load its songs."""
        pl_id = int(event.item.id.split("-")[1])
        self._selected_playlist_id = pl_id
        self._refresh_playlist_songs()

    @on(Button.Pressed, "#btn-create")
    def on_create(self) -> None:
        name = self.query_one("#pl-name-input", Input).value.strip()
        if not name:
            self.app.notify("Enter a playlist name first.", severity="warning")
            return
        try:
            self._library.create_playlist(name)
            self.query_one("#pl-name-input", Input).value = ""
            self._refresh_playlists()
            self.app.notify(f"Created playlist '{name}'.")
        except Exception as e:
            self.app.notify(f"Error: {e}", severity="error")

    @on(Button.Pressed, "#btn-add")
    def on_add_to_playlist(self) -> None:
        if self._selected_playlist_id is None:
            self.app.notify("Select a playlist first.", severity="warning")
            return
        if self._selected_song is None:
            self.app.notify("No song selected in the main view.", severity="warning")
            return
        try:
            self._library.add_to_playlist(
                self._selected_playlist_id, self._selected_song
            )
            self._refresh_playlist_songs()
            self.app.notify(f"Added '{self._selected_song.title}' to playlist.")
        except Exception as e:
            self.app.notify(f"Error: {e}", severity="error")

    @on(Button.Pressed, "#btn-remove")
    def on_remove_from_playlist(self) -> None:
        if self._selected_playlist_id is None:
            self.app.notify("Select a playlist first.", severity="warning")
            return
        if self._selected_song is None:
            self.app.notify("No song selected in the main view.", severity="warning")
            return
        try:
            self._library.remove_from_playlist(
                self._selected_playlist_id, self._selected_song
            )
            self._refresh_playlist_songs()
            self.app.notify(f"Removed '{self._selected_song.title}' from playlist.")
        except Exception as e:
            self.app.notify(f"Error: {e}", severity="error")

    def action_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#close-btn")
    def on_close_pressed(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

class MusicApp(App):
    """The top-level textual App.

    textual's App.compose() builds the initial widget tree. Widgets are
    identified by their CSS id so event handlers can query_one() them.

    The `_songs` dict maps DataTable row keys to Song objects so we can retrieve
    the full Song when the user highlights or activates a row.
    """

    CSS = """
    Screen {
        layout: vertical;
    }
    #search-input {
        dock: top;
        margin: 1 1 0 1;
    }
    #status {
        dock: top;
        margin: 0 1;
        color: $text-muted;
    }
    #results-table {
        margin: 0 1;
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("p", "open_playlists", "Playlists"),
    ]

    TITLE = "Mursik — Music Manager"
    SUB_TITLE = "Search · Download · Organize"

    def __init__(self, library: MusicLibrary) -> None:
        super().__init__()
        self._library = library
        # Maps DataTable row key (str) → Song object
        self._songs: dict[str, Song] = {}
        # The currently highlighted Song (set when the user moves the cursor)
        self._current_song: Song | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search for a track…", id="search-input")
        yield Static("", id="status")
        yield DataTable(id="results-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        """Set up DataTable columns once the widget is in the DOM."""
        table = self.query_one("#results-table", DataTable)
        table.add_columns("Platform", "Title", "Artist", "Album", "Duration")

    @on(Input.Submitted, "#search-input")
    def on_search(self, event: Input.Submitted) -> None:
        """User pressed Enter in the search box — trigger a search."""
        query = event.value.strip()
        if not query:
            return
        self._run_search(query)

    @work(thread=True)
    def _run_search(self, query: str) -> None:
        """Search all platforms in a worker thread so the UI stays responsive.

        Why @work(thread=True)?
          - network calls (search) can take 1–5 seconds. Blocking the main
            textual event loop here would freeze all input during that window.
          - @work(thread=True) runs this function in a thread pool thread.
            textual's @work infrastructure handles thread lifecycle and lets us
            call `self.app.call_from_thread()` to safely update widgets from
            the background thread.

        Why call_from_thread?
          - Widget mutations (add_row, update, remove) must happen on the main
            thread. call_from_thread schedules the callback on the main event
            loop and blocks until it completes.
        """
        self.app.call_from_thread(
            self._set_status, f"Searching for '{query}'…"
        )

        try:
            results = self._library.search_all(query)
        except Exception as e:
            self.app.call_from_thread(
                self._set_status, f"Search error: {e}"
            )
            return

        self.app.call_from_thread(self._populate_table, results)

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def _populate_table(self, results: dict[str, list[Song]]) -> None:
        """Clear the table and add grouped results from all platforms."""
        table = self.query_one("#results-table", DataTable)
        table.clear()
        self._songs.clear()
        self._current_song = None

        total = 0
        for platform, songs in results.items():
            for song in songs:
                # Use source_id as the unique row key so we can retrieve the
                # Song object when the user activates a row.
                key = f"{platform}:{song.source_id}"
                dur = f"{song.duration}s" if song.duration else "?"
                table.add_row(
                    platform.upper(),
                    song.title,
                    song.artist,
                    song.album or "—",
                    dur,
                    key=key,
                )
                self._songs[key] = song
                total += 1

        count = sum(len(v) for v in results.values())
        self._set_status(f"Found {count} results.")

    @on(DataTable.RowHighlighted, "#results-table")
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Track which row (and therefore which Song) is under the cursor."""
        if event.row_key.value:
            self._current_song = self._songs.get(event.row_key.value)

    @on(DataTable.RowSelected, "#results-table")
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """User pressed Enter on a row.

        Spotify rows → push CandidateScreen (YouTube cross-ref picker).
        YouTube/SoundCloud rows → download directly.
        """
        key = event.row_key.value
        if not key:
            return
        song = self._songs.get(key)
        if not song:
            return

        if song.platform == "spotify":
            self._open_candidate_picker(song)
        else:
            self._download_song(song)

    @work(thread=True)
    def _open_candidate_picker(self, spotify_song: Song) -> None:
        """Fetch YouTube candidates in a worker thread, then push the picker modal.

        Why @work(thread=True) here?
          - find_youtube_candidates() calls yt-dlp which makes a real HTTP
            request to YouTube. Running that on the main event-loop thread would
            freeze all TUI input for the duration of the search (1-3 s typical).
          - We do the network call in a thread pool thread, then hand the result
            back to the main thread via call_from_thread to push the modal and
            update the status label — both of which touch widgets and must run
            on the main thread.
        """
        self.app.call_from_thread(
            self._set_status,
            f"Finding YouTube matches for '{spotify_song.title}'…",
        )

        try:
            candidates = self._library.find_youtube_candidates(spotify_song)
        except Exception as e:
            self.app.call_from_thread(
                self.app.notify,
                f"Could not fetch candidates: {e}",
                severity="error",
            )
            return

        if not candidates:
            self.app.call_from_thread(
                self.app.notify, "No YouTube candidates found.", severity="warning"
            )
            return

        def on_candidate_picked(chosen: Song | None) -> None:
            if chosen is None:
                self._set_status("Cancelled.")
            else:
                self._download_song(chosen, metadata_song=spotify_song)

        self.app.call_from_thread(
            self.push_screen,
            CandidateScreen(spotify_song, candidates),
            on_candidate_picked,
        )

    @work(thread=True)
    def _download_song(
        self, song: Song, metadata_song: Song | None = None
    ) -> None:
        """Download `song` in a worker thread.

        This is the second place @work(thread=True) appears — downloads can
        take 10–60 seconds and must never block the main event loop.
        """
        display_title = (metadata_song or song).title
        self.app.call_from_thread(
            self._set_status, f"Downloading '{display_title}'…"
        )

        try:
            result = self._library.download(song, metadata_song=metadata_song)
            self.app.call_from_thread(
                self.app.notify,
                f"Downloaded '{result.title}' → {result.file_path}",
            )
            self.app.call_from_thread(
                self._set_status, f"Done: {result.title}"
            )
        except Exception as e:
            self.app.call_from_thread(
                self.app.notify, f"Download failed: {e}", severity="error"
            )
            self.app.call_from_thread(self._set_status, "Download failed.")

    def action_open_playlists(self) -> None:
        """Open the playlist CRUD modal (`p` key)."""
        self.push_screen(PlaylistScreen(self._library, self._current_song))


# ---------------------------------------------------------------------------
# Bootstrap: build MusicLibrary from environment and launch
# ---------------------------------------------------------------------------

def _build_library() -> MusicLibrary:
    """Construct MusicLibrary with real credentials from .env.

    Why not inline in __main__?
      - Keeping construction in its own function makes it easy to swap in a
        test library or add DI later. The App constructor just receives a
        MusicLibrary; it doesn't know how it was built.

    Reads from environment (populated by load_dotenv() at module top):
      SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET — read automatically by spotipy
      when SpotifyClientCredentials() is called with no arguments.
    """
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials

    # spotipy reads SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET from the
    # environment. If they're missing it raises spotipy.oauth2.SpotifyOauthError.
    sp_client = spotipy.Spotify(auth_manager=SpotifyClientCredentials())

    db_path = Path("mursik.db")

    return MusicLibrary(
        spotify_source=SpotifySource(client=sp_client),
        youtube_source=YouTubeSource(),
        soundcloud_source=SoundCloudSource(),
        downloader=Downloader(),
        tagger=Tagger(),
        database=Database(db_path),
        download_dir=Path("downloads"),
    )


if __name__ == "__main__":
    library = _build_library()
    app = MusicApp(library)
    app.run()
