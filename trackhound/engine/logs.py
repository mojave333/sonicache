"""Where the engine writes what went on, for whoever cares to keep it.

Everything goes to the "trackhound" logger and its children. The engine never
sets up handlers of its own: the program points the logger at a file
(trackhound/logs.py), and a script using the engine can do what it likes.
"""

from __future__ import annotations

import logging

log = logging.getLogger("trackhound")


class YtdlpLogger:
    """Hands yt-dlp's chatter to the log instead of the console.

    yt-dlp talks in paragraphs and in English; the window shows its own short
    message and the whole text lands in the log, where it can be read afterwards.
    """

    def __init__(self, name: str = "yt-dlp"):
        self._log = log.getChild(name)

    def debug(self, message: str) -> None:
        self._log.debug("%s", message)

    def info(self, message: str) -> None:
        self._log.debug("%s", message)

    def warning(self, message: str) -> None:
        self._log.warning("%s", message)

    def error(self, message: str) -> None:
        self._log.error("%s", message)
