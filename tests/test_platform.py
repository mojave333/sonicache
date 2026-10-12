"""The parts that differ between Windows, macOS and the Linux desktops."""

import subprocess
import sys
from pathlib import Path

import pytest

from sonicache import gui, logs


class Ran:
    """Stands in for subprocess.run and remembers what it was asked to do."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls = []
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        return self.result


class TestDataDir:
    def test_windows_uses_localappdata(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
        assert logs.data_dir() == Path(r"C:\Users\x\AppData\Local") / "Sonicache"

    def test_macos_uses_application_support(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert logs.data_dir().parts[-3:] == ("Library", "Application Support", "Sonicache")

    def test_linux_follows_xdg(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert logs.data_dir() == tmp_path / "sonicache"

    def test_the_log_lives_under_it(self, monkeypatch, tmp_path):
        monkeypatch.setattr(logs, "data_dir", lambda: tmp_path)
        assert logs.log_file() == tmp_path / "logs" / logs.LOG_NAME


class TestFormerData:
    """Until 3.0 the program was called Trackhound: what it kept under that
    name is taken over on the first start under the new one."""

    @pytest.fixture
    def former(self):
        old = logs._folder_of(logs.FORMER_NAME)  # inside the test's own folder (conftest)
        (old / "logs").mkdir(parents=True)
        (old / "playlists.json").write_text('{"mine": []}', encoding="utf-8")
        (old / "logs" / "trackhound.log").write_text("old lines", encoding="utf-8")
        return old

    def test_it_sits_beside_the_new_folder(self, former):
        assert former.parent == logs.data_dir().parent and former.name.lower() == "trackhound"

    def test_the_files_come_over_and_the_log_stays(self, former):
        assert logs.adopt_former_data() is True
        assert (logs.data_dir() / "playlists.json").read_text(encoding="utf-8") == '{"mine": []}'
        assert not (logs.data_dir() / "logs").exists()
        assert (former / "playlists.json").is_file()  # a copy, the old folder is whole

    def test_it_happens_once(self, former):
        logs.adopt_former_data()
        (former / "playlists.json").write_text('{"later": []}', encoding="utf-8")
        assert logs.adopt_former_data() is False
        assert "mine" in (logs.data_dir() / "playlists.json").read_text(encoding="utf-8")

    def test_a_folder_of_its_own_is_never_written_over(self, former):
        logs.data_dir().mkdir(parents=True)
        assert logs.adopt_former_data() is False
        assert list(logs.data_dir().iterdir()) == []

    def test_with_nothing_to_take_over_no_folder_is_made(self):
        assert logs.adopt_former_data() is False
        assert not logs.data_dir().exists()

    def test_a_copy_that_fails_halfway_leaves_nothing_behind(self, former, monkeypatch):
        def half(source, target, **kwargs):
            Path(target).mkdir(parents=True)
            (Path(target) / "playlists.json").write_text("{", encoding="utf-8")
            raise OSError("disk full")

        monkeypatch.setattr(logs.shutil, "copytree", half)
        assert logs.adopt_former_data() is False
        assert [path.name for path in logs.data_dir().parent.iterdir()] == [former.name]

    def test_starting_the_log_takes_them_over_first(self, former):
        for handler in list(logs.log.handlers):
            logs.log.removeHandler(handler)
        try:
            logs.setup()
            assert (logs.data_dir() / "playlists.json").is_file()
            assert str(former) in logs.log_file().read_text(encoding="utf-8")
        finally:
            for handler in list(logs.log.handlers):
                logs.log.removeHandler(handler)
                handler.close()


class TestTrash:
    """Deleting from the library has to stay undoable on every desktop."""

    def test_macos_hands_the_file_to_finder(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "darwin")
        ran = Ran()
        monkeypatch.setattr(subprocess, "run", ran)
        gui._recycle(tmp_path / "album")
        assert ran.calls[0][0] == "osascript"
        assert "Finder" in ran.calls[0][2] and "delete POSIX file" in ran.calls[0][2]

    def test_a_refusing_finder_is_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(subprocess, "run", Ran(returncode=1, stderr="no such file"))
        with pytest.raises(OSError, match="no such file"):
            gui._recycle(tmp_path / "album")

    def test_linux_prefers_gio(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(gui.shutil, "which", lambda name: "/usr/bin/gio")
        ran = Ran()
        monkeypatch.setattr(subprocess, "run", ran)
        gui._recycle(tmp_path / "album")
        assert ran.calls == [["gio", "trash", str((tmp_path / "album").resolve())]]

    def test_linux_falls_back_to_the_trash_folder(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(gui.shutil, "which", lambda name: None)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        album = tmp_path / "Daft Punk - Discovery"
        album.mkdir()
        (album / "01. One.m4a").write_bytes(b"x")

        gui._recycle(album)

        trash = tmp_path / "data" / "Trash"
        assert not album.exists()
        assert (trash / "files" / "Daft Punk - Discovery" / "01. One.m4a").exists()
        note = (trash / "info" / "Daft Punk - Discovery.trashinfo").read_text(encoding="utf-8")
        assert note.startswith("[Trash Info]")
        assert "Path=" in note and "DeletionDate=" in note

    def test_a_name_already_in_the_trash_gets_another(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(gui.shutil, "which", lambda name: None)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        trash_files = tmp_path / "data" / "Trash" / "files"
        trash_files.mkdir(parents=True)
        (trash_files / "song.m4a").write_bytes(b"old")
        song = tmp_path / "song.m4a"
        song.write_bytes(b"new")

        gui._recycle(song)

        assert (trash_files / "song.m4a").read_bytes() == b"old"
        assert (trash_files / "song.1.m4a").read_bytes() == b"new"

    def test_a_failed_move_leaves_no_note_behind(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(gui.shutil, "which", lambda name: None)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        def refuse(*args, **kwargs):
            raise OSError("cross-device link")

        monkeypatch.setattr(gui.shutil, "move", refuse)
        song = tmp_path / "song.m4a"
        song.write_bytes(b"x")
        with pytest.raises(OSError):
            gui._recycle(song)
        assert list((tmp_path / "data" / "Trash" / "info").iterdir()) == []


class TestReveal:
    def test_windows_selects_the_file_in_explorer(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "win32")
        opened = []
        monkeypatch.setattr(gui.subprocess, "Popen", lambda command, **kwargs: opened.append(command))
        assert gui._reveal(tmp_path / "song.m4a") is True
        assert "explorer /select," in opened[0]

    def test_macos_uses_open_minus_r(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "darwin")
        opened = []
        monkeypatch.setattr(gui.subprocess, "Popen", lambda command, **kwargs: opened.append(command))
        assert gui._reveal(tmp_path / "song.m4a") is True
        assert opened[0][:2] == ["open", "-R"]

    def test_linux_asks_the_file_manager_over_dbus(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        ran = Ran()
        monkeypatch.setattr(subprocess, "run", ran)
        assert gui._reveal(tmp_path / "song.m4a") is True
        assert ran.calls[0][0] == "dbus-send"
        assert any("org.freedesktop.FileManager1.ShowItems" in part for part in ran.calls[0])

    def test_a_desktop_that_cannot_is_not_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")

        def refuse(*args, **kwargs):
            raise OSError("no dbus-send")

        monkeypatch.setattr(subprocess, "run", refuse)
        assert gui._reveal(tmp_path / "song.m4a") is False


class TestClipboard:
    def test_macos_pipes_into_pbcopy(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(gui.shutil, "which", lambda name: "/usr/bin/pbcopy" if name == "pbcopy" else None)
        ran = Ran()
        monkeypatch.setattr(subprocess, "run", ran)
        assert gui._copy_to_clipboard("отчёт") is True
        assert ran.calls == [["pbcopy"]]

    def test_wayland_and_x11_are_tried_in_turn(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(gui.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "xclip" else None)
        ran = Ran()
        monkeypatch.setattr(subprocess, "run", ran)
        assert gui._copy_to_clipboard("отчёт") is True
        assert ran.calls == [["xclip", "-selection", "clipboard"]]


class TestSystemTheme:
    def test_macos_reads_the_interface_style(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(gui, "_ask", lambda command: "Dark")
        assert gui._system_dark() is True

    def test_macos_light_says_nothing(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(gui, "_ask", lambda command: "")
        assert gui._system_dark() is False

    def test_gnome_is_asked_for_its_colour_scheme(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        asked = []

        def ask(command):
            asked.append(command[-1])
            return "'prefer-dark'" if command[-1] == "color-scheme" else ""

        monkeypatch.setattr(gui, "_ask", ask)
        assert gui._system_dark() is True
        assert asked == ["color-scheme"]

    def test_an_older_desktop_falls_back_to_the_theme_name(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(gui, "_ask",
                            lambda command: "'Adwaita-dark'" if command[-1] == "gtk-theme" else "")
        assert gui._system_dark() is True

    def test_a_missing_command_is_simply_quiet(self, monkeypatch):
        monkeypatch.setattr(gui.shutil, "which", lambda name: None)
        assert gui._ask(["gsettings", "get", "x", "y"]) == ""
