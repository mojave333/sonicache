"""Entry point: window without arguments, command line interface with arguments.

    python main.py
    python main.py https://open.spotify.com/album/... -f mp3 -o D:\\Music
"""

import sys


def main() -> None:
    if len(sys.argv) > 1:
        from trackhound.cli import main as cli_main
        sys.exit(cli_main())
    try:
        from trackhound.gui import main as gui_main
    except ImportError as e:
        # pythonw.exe has no console, so show the problem in a dialog
        from tkinter import messagebox
        messagebox.showerror("Trackhound",
                             f"Не установлены зависимости ({e}).\n\nЗапустите install.bat")
        sys.exit(1)
    gui_main()


if __name__ == "__main__":
    main()
