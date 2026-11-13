#!/usr/bin/env python3
"""Garmin Export -- opens the desktop app, or runs in the terminal with --no-gui.

    python main.py                    # desktop app
    python main.py --no-gui --help    # command-line options
"""

from garmin_export.app import main

if __name__ == "__main__":
    main()
