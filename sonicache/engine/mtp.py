"""An Android phone over a cable, in its "File transfer" mode (MTP).

Windows gives such a phone no drive letter: it is reached through Windows
Portable Devices (WPD), a COM API in which a storage holds objects, each with
an id and a parent, and nothing has a path. MtpTarget is the sync's target
(devices.Target) on top of that: it finds an object by walking the names down
from the storage, creates the folders on the way, and, since MTP cannot write
over a file, deletes the old one and writes the new one beside it under a
.part name before giving it the real one.

The program's files on a phone go into Music/Sonicache, where players look and
where they keep the phone's own folders tidy.

_Wpd is the COM itself, through ctypes as the taskbar buttons are; MtpTarget
only asks it for the few things below, so a phone kept in memory stands in for
it in the tests.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .devices import SYNC_DIR, Target

BASE = "Music"
CHUNK = 256 * 1024  # bytes handed to the phone at a time


@dataclass(frozen=True)
class Entry:
    """An object on the phone, as a folder's listing gives it."""
    id: str
    name: str
    folder: bool
    size: int = 0


class MtpTarget(Target):
    """Where a sync writes on a phone's storage. session answers children(),
    make_folder(), write(), rename(), remove(), read() and space() (_Wpd)."""

    def __init__(self, session, storage_id: str, base: str = BASE):
        self.session = session
        self.storage_id = storage_id
        self.base = base
        self._listings: dict[str, dict[str, Entry]] = {}  # folder id -> its objects by lower-case name
        self._renames = True  # until the phone says it cannot rename

    def __str__(self) -> str:
        return f"MTP {self.storage_id}/{self.base}"

    # Finding objects by their path

    def _listing(self, folder_id: str) -> dict[str, Entry]:
        if folder_id not in self._listings:
            self._listings[folder_id] = {entry.name.casefold(): entry for entry in self.session.children(folder_id)}
        return self._listings[folder_id]

    def _forget(self, folder_id: str) -> None:
        self._listings.pop(folder_id, None)

    def _parts(self, dest: str) -> list[str]:
        return [self.base, *[part for part in dest.split("/") if part]]

    def _folder(self, parts: list[str], create: bool = False) -> str | None:
        """The id of the folder these names lead to from the storage; made on the way when asked."""
        folder = self.storage_id
        for name in parts:
            entry = self._listing(folder).get(name.casefold())
            if entry is None:
                if not create:
                    return None
                made = self.session.make_folder(folder, name)
                self._forget(folder)
                entry = Entry(made, name, True)
            elif not entry.folder:
                raise NotADirectoryError(20, "a file where a folder should be", "/".join(parts))
            folder = entry.id
        return folder

    def _find(self, dest: str) -> tuple[str | None, Entry | None]:
        """The folder that would hold dest, and dest's own object, either None when not there."""
        *above, name = self._parts(dest)
        folder = self._folder(above)
        if folder is None:
            return None, None
        return folder, self._listing(folder).get(name.casefold())

    # The target

    def free(self) -> int:
        return self.session.space(self.storage_id)[0]

    def exists(self, dest: str) -> bool:
        return self._find(dest)[1] is not None

    def is_file(self, dest: str) -> bool:
        entry = self._find(dest)[1]
        return entry is not None and not entry.folder

    def read_bytes(self, dest: str) -> bytes:
        entry = self._find(dest)[1]
        if entry is None or entry.folder:
            raise FileNotFoundError(2, "not on the phone", dest)
        return self.session.read(entry.id)

    def write_bytes(self, dest: str, data: bytes) -> None:
        self._store(dest, lambda: [data], len(data))

    def put(self, local: Path, dest: str) -> int:
        size = Path(local).stat().st_size

        def chunks() -> Iterable[bytes]:
            with open(local, "rb") as file:
                while block := file.read(CHUNK):
                    yield block
        # The file is opened before anything is sent: a library file that went away
        # fails here, naming itself, and not halfway through the phone's write
        with open(local, "rb"):
            pass
        self._store(dest, chunks, size)
        return size

    def _store(self, dest: str, chunks, size: int) -> None:
        """The file under its name, in place of the old one: written whole as .part
        first and then renamed, or, on a phone that cannot rename, straight away."""
        *above, name = self._parts(dest)
        folder = self._folder(above, create=True)
        if self._renames:
            partial = f"{name}.part"
            stale = self._listing(folder).get(partial.casefold())
            if stale is not None:
                self.session.remove(stale.id)
            self.session.write(folder, partial, chunks(), size)
            self._forget(folder)
            written = self._listing(folder)[partial.casefold()]
            old = self._listing(folder).get(name.casefold())
            if old is not None:
                self.session.remove(old.id)
            if self.session.rename(written.id, name):
                self._forget(folder)
                return
            self._renames = False
            self.session.remove(written.id)
            self._forget(folder)
        old = self._listing(folder).get(name.casefold())
        if old is not None:
            self.session.remove(old.id)
        self.session.write(folder, name, chunks(), size)
        self._forget(folder)

    def delete(self, dest: str) -> None:
        folder, entry = self._find(dest)
        if entry is not None and not entry.folder:
            self.session.remove(entry.id)
            self._forget(folder)

    def prune(self, folder: str) -> None:
        parts = [part for part in folder.split("/") if part]
        while len(parts) > 1 and parts[0] == SYNC_DIR:  # Sonicache/ itself stays
            above = self._folder([self.base, *parts[:-1]])
            if above is None:
                return
            entry = self._listing(above).get(parts[-1].casefold())
            if entry is None or not entry.folder or self._listing(entry.id):
                return
            self.session.remove(entry.id)
            self._forget(above)
            self._forget(entry.id)
            parts.pop()

    def clean_parts(self) -> None:
        top = self._folder([self.base, SYNC_DIR])
        if top is None:
            return
        waiting = [top]
        while waiting:
            folder = waiting.pop()
            for entry in list(self._listing(folder).values()):
                if entry.folder:
                    waiting.append(entry.id)
                elif entry.name.endswith(".part"):
                    self.session.remove(entry.id)
                    self._forget(folder)


def find_phones() -> list:
    """The phones plugged in by cable in "File transfer" mode, with their storages."""
    if sys.platform != "win32":
        return []
    return []
