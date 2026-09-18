"""The log file and the report behind the "Скопировать отчёт" button."""

import logging

import pytest

from trackhound import logs
from trackhound.engine.logs import YtdlpLogger


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Keeps the test out of the real %LOCALAPPDATA% and off the shared logger."""
    monkeypatch.setattr(logs, "log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(logs, "log_file", lambda: tmp_path / "logs" / logs.LOG_NAME)
    for handler in list(logs.log.handlers):
        logs.log.removeHandler(handler)
    yield tmp_path / "logs"
    for handler in list(logs.log.handlers):
        logs.log.removeHandler(handler)


class TestSetup:
    def test_the_file_is_created_and_written(self, log_dir):
        path = logs.setup()
        logs.log.info("привет")
        assert path == log_dir / logs.LOG_NAME
        assert "привет" in path.read_text(encoding="utf-8")

    def test_the_startup_line_names_the_version(self, log_dir):
        logs.setup()
        assert "Trackhound" in logs.log_file().read_text(encoding="utf-8")

    def test_setting_up_twice_does_not_double_the_handlers(self, log_dir):
        logs.setup()
        logs.setup()
        assert len([h for h in logs.log.handlers if hasattr(h, "baseFilename")]) == 1

    def test_an_unwritable_folder_is_not_fatal(self, monkeypatch, tmp_path):
        monkeypatch.setattr(logs, "log_dir", lambda: tmp_path / "logs")
        monkeypatch.setattr(logs, "log_file", lambda: tmp_path / "logs" / logs.LOG_NAME)

        def refuse(*args, **kwargs):
            raise OSError("read-only")

        monkeypatch.setattr("pathlib.Path.mkdir", refuse)
        assert logs.setup() is None


class TestTail:
    def test_returns_the_last_lines(self, log_dir):
        logs.setup()
        for number in range(10):
            logs.log.info("строка %d", number)
        assert len(logs.tail(3)) == 3
        assert "строка 9" in logs.tail(3)[-1]

    def test_a_missing_file_is_empty(self, log_dir):
        assert logs.tail() == []


class TestReport:
    def test_names_the_version_and_the_tools(self, log_dir):
        text = logs.report({"format": "mp3", "threads": 4, "cookies_browser": "firefox",
                            "folder": "D:\\Music"},
                           ["Не найден ffmpeg"], {"ffmpeg": "", "deno": "C:\\deno.exe"})
        assert "Trackhound" in text
        assert "yt-dlp" in text
        assert "ffmpeg: не найден" in text
        assert "deno: C:\\deno.exe" in text
        assert "формат mp3" in text and "потоков 4" in text and "firefox" in text
        assert "D:\\Music" in text
        assert "проблема: Не найден ffmpeg" in text

    def test_carries_the_end_of_the_log(self, log_dir):
        logs.setup()
        logs.log.error("что-то сломалось")
        assert "что-то сломалось" in logs.report()

    def test_works_before_anything_was_logged(self, log_dir):
        assert "Trackhound" in logs.report()


class TestYtdlpLogger:
    def test_chatter_lands_in_the_log_as_debug(self, log_dir, caplog):
        logs.setup()
        with caplog.at_level(logging.DEBUG, logger="trackhound.yt-dlp"):
            caplog.clear()
            YtdlpLogger().debug("[download] 12%")
            YtdlpLogger().info("[info] something")
        assert [record.levelno for record in caplog.records] == [logging.DEBUG, logging.DEBUG]

    def test_warnings_and_errors_keep_their_level(self, log_dir, caplog):
        logs.setup()
        with caplog.at_level(logging.DEBUG, logger="trackhound.yt-dlp"):
            caplog.clear()
            YtdlpLogger().warning("nothing to worry about")
            YtdlpLogger().error("ERROR: it broke")
        assert [record.levelno for record in caplog.records] == [logging.WARNING, logging.ERROR]

    def test_the_yt_dlp_option_shape_is_satisfied(self):
        logger = YtdlpLogger()
        for method in ("debug", "info", "warning", "error"):
            assert callable(getattr(logger, method))


def test_package_version_reads_installed_packages():
    assert logs.package_version("yt_dlp")[0].isdigit()
    assert logs.package_version("no-such-package-here") == "не установлен"
