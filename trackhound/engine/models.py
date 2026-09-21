"""Data shared by all link sources and the downloader."""

from __future__ import annotations

from dataclasses import dataclass, field

from .i18n import t

# What an error's code means. The codes are for programs: they stay the same
# in every language and across minor versions, while the sentence in
# str(error) is for a person and may be reworded in any release.
ERROR_CODES = {
    # Reading a link
    "unsupported_link": "the link leads to no music, or to a kind of page the service is not read from",
    "login_required": "the service shows its music only to a signed-in account",
    "not_found": "the service has nothing under this link or id, or not in this country",
    "unavailable": "the service gave its page without the release, as Spotify does where it will not show it; "
                   "a proxy or a link from another service helps",
    "not_in_album": "the link names a track its album does not have",
    "empty": "the release or playlist has no tracks that can be read, or it is private",
    "unreadable": "the page or the service's answer is not in the shape it is read in",
    "short_link_broken": "a short link could not be followed to the page behind it",
    "empty_query": "neither a link nor a name to search for was given",
    "no_title": "the link carries no track title to search for",
    "link_failed": "the link could not be opened",
    "http_error": "the service answered with an HTTP error",
    "offline": "the service could not be reached",
    "service_error": "the service answered with an error of its own",
    # Downloading
    "unknown_format": "the audio format asked for is not one of FORMATS",
    "ffmpeg_missing": "the format asked for needs ffmpeg, which was not found",
    "no_source": "the track was found neither on YouTube Music nor on SoundCloud",
    "age_restricted": "the video is age-restricted: it takes a signed-in YouTube account",
    "cookies_locked": "the browser's cookies could not be copied while it is open",
    "cookies_unreadable": "the browser's cookies could not be decrypted",
    "no_cookies": "the browser holds no YouTube cookies",
    "preview_only": "only a preview of the track could be downloaded",
    "wrong_file": "the download did not end in a file of the format asked for",
    "convert_failed": "ffmpeg could not make the downloaded stream into the format asked for",
    "download_failed": "the download failed for any other reason",
    "unknown": "an error raised without a code",
}


class _CodedError(Exception):
    """An error that says what happened twice over.

    str(error) is a sentence for a person, in the language set_language()
    chose. `code` is one of ERROR_CODES, for a program to act on, and
    `details` holds the values the sentence was made from: the service, the
    link, the id.
    """

    # Positional only: a sentence may well have a {code} of its own, an HTTP status
    def __init__(self, message: str = "", code: str = "unknown", /, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    @classmethod
    def of(cls, code: str, text: str, /, **values):
        """The error with its sentence translated and the values kept."""
        return cls(t(text, **values), code, **values)


class SourceError(_CodedError):
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
    # The recording's international code, the same in every catalogue; "" when unknown
    isrc: str = ""


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
