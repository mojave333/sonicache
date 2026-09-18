"""The engine stays a library: usable without the window and the command line."""

import ast
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
