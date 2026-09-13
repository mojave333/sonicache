"""Entry point: window without arguments, command line interface with arguments.

    python main.py
    python main.py https://open.spotify.com/album/... -f mp3 -o D:\\Music

The build (trackhound.spec) makes two exe files out of this one script:
Trackhound.exe opens the window, Trackhound-cli.exe always talks to the
console, so that a double click never flashes a terminal and a terminal run
never opens a window without output.
"""

import sys
from pathlib import Path


def wants_cli() -> bool:
    if len(sys.argv) > 1:
        return True
    return getattr(sys, "frozen", False) and Path(sys.executable).stem.endswith("-cli")


def main() -> None:
    if wants_cli():
        from trackhound.cli import main as cli_main
        sys.exit(cli_main(sys.argv[1:] or ["--check"]))
    try:
        from trackhound.gui import main as gui_main
    except ImportError as e:
        # a window build has no console, so show the problem in a dialog
        from tkinter import messagebox
        messagebox.showerror("Trackhound",
                             f"Не установлены зависимости ({e}).\n\nЗапустите install.bat")
        sys.exit(1)
    gui_main()


if __name__ == "__main__":
    main()
