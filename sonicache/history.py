"""The releases asked for in the window, kept between runs so that an unfinished one comes back.
"""

from __future__ import annotations

from pathlib import Path

from . import logs

HISTORY_LIMIT = 100  # releases remembered between runs

def _history_file() -> Path:
    return logs.data_dir() / "history.json"

def _load_history() -> list[dict]:
    return [entry for entry in logs.read_json(_history_file(), list) if isinstance(entry, dict)]

def _save_history(history: list[dict]) -> None:
    logs.write_json(_history_file(), history)
