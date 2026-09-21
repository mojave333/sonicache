"""The engine stays a library: usable without the window and the command line."""

import ast
import re
import subprocess
import sys
from pathlib import Path

import trackhound.engine as engine

ENGINE = Path(engine.__file__).parent
ROOT = ENGINE.parent.parent


def test_the_engine_imports_nothing_from_the_program():
    for path in ENGINE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.level <= 1, f"{path.name}:{node.lineno} reaches out of the engine"
                if node.level == 0 and node.module:
                    assert not node.module.startswith("trackhound") or \
                        node.module.startswith("trackhound.engine"), f"{path.name}:{node.lineno}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name == "trackhound.engine" or not alias.name.startswith("trackhound"), \
                        f"{path.name}:{node.lineno}"


def test_importing_the_engine_leaves_the_window_out():
    # A fresh interpreter: this one has long imported the window for other tests
    code = ("import sys, trackhound.engine; "
            "print(sorted(m for m in sys.modules if m in ('webview', 'trackhound.gui', 'trackhound.cli')))")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                            check=True)
    assert result.stdout.strip() == "[]"


def test_the_public_names_are_there():
    for name in engine.__all__:
        assert hasattr(engine, name), name


def test_the_program_adds_its_lines_to_the_engine_table():
    from trackhound import i18n

    assert i18n.ENGLISH is engine.i18n.ENGLISH
    assert "Плейлист пуст или закрыт" in i18n.ENGLISH  # the engine's
    assert "не используются" in i18n.ENGLISH  # the program's


class TestErrorCodes:
    """Errors say what happened to a program as well as to a person."""

    def test_every_code_the_engine_uses_is_explained(self):
        from trackhound.engine import downloader

        source = "\n".join(path.read_text(encoding="utf-8") for path in ENGINE.glob("*.py"))
        used = set(re.findall(r'\.of\(\s*"(\w+)"', source))
        used |= set(re.findall(r'Error\(\w+, "(\w+)"\)', source))
        used |= set(re.findall(r'Failure\(track, "(\w+)"', source))
        used |= set(re.findall(r'code="(\w+)"', source))
        used |= {code for _, code, _ in downloader._KNOWN_ERRORS}
        assert len(used) > 20  # the patterns above still find the calls
        assert used <= set(engine.ERROR_CODES), used - set(engine.ERROR_CODES)

    def test_no_error_is_raised_without_a_code(self):
        for path in ENGINE.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ("SourceError", "DownloaderError")):
                    assert len(node.args) >= 2, f"{path.name}:{node.lineno}"

    def test_the_code_and_values_are_kept_beside_the_sentence(self):
        error = engine.SourceError.of("http_error", "{service} ответил HTTP {code}: {url}",
                                      service="Deezer", code=503, url="https://api.deezer.com")
        assert error.code == "http_error"  # a {code} in the sentence does not take its place
        assert error.details == {"service": "Deezer", "code": 503, "url": "https://api.deezer.com"}
        assert str(error) == "Deezer ответил HTTP 503: https://api.deezer.com"

    def test_the_code_stays_when_the_language_changes(self):
        engine.set_language("en")
        error = engine.SourceError.of("empty", "Плейлист пуст или закрыт")
        assert (error.code, str(error)) == ("empty", engine.i18n.ENGLISH["Плейлист пуст или закрыт"])

    def test_an_error_made_the_old_way_still_works(self):
        error = engine.DownloaderError("something broke")
        assert (str(error), error.code, error.details) == ("something broke", "unknown", {})
