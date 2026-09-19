"""Data shared by all link sources and the downloader."""

from __future__ import annotations

from dataclasses import dataclass, field


class SourceError(Exception):
    """A link could not be read; the message is shown to the user as is."""


@dataclass
class Track:
    id: str  # unique within its release
    title: str
    artists: str
    duration: float  # seconds, 0 when unknown
    track_number: int
    disc_number: int = 1
    explicit: bool = False
    # Set when the service itself streams the track openly (YouTube, SoundCloud...);
    # otherwise the audio is matched on YouTube Music and SoundCloud.
    audio_url: str = ""
    audio_source: str = ""  # "song", "video", "soundcloud" or "web"


@dataclass
class Album:
    id: str
    name: str
    artist: str
    release_date: str = ""  # "YYYY-MM-DD", "YYYY" or ""
    kind: str = "album"  # album / single / ep / compilation / playlist
    cover_url: str = ""
    tracks: list[Track] = field(default_factory=list)
    service: str = ""  # where the link came from, e.g. "Apple Music"
    genre: str = ""  # "Electronic"; from the service, or looked up by find_genre()
    # A caveat about the release itself, shown next to it: a playlist page that
    # only handed over its first tracks says so here.
    note: str = ""

    @property
    def year(self) -> str:
        return self.release_date[:4]

    @property
    def total_discs(self) -> int:
        return max((t.disc_number for t in self.tracks), default=1)


@dataclass
class Release:
    album: Album
    tracks: list[Track]  # what to download: the whole album or one track of it
    single: bool = False  # a link to one track: saved next to albums, not in a folder
