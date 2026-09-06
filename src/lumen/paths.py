"""Where Lumen keeps its files, per platform.

Everything lives under one directory so a user can find (or delete) all of it:

    Windows  %LOCALAPPDATA%\\lumen
    macOS    ~/Library/Application Support/lumen
    Linux    $XDG_CONFIG_HOME/lumen  (default ~/.config/lumen)

Override with the LUMEN_HOME environment variable (tests do).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("LUMEN_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "lumen"


def config_file() -> Path:
    return data_dir() / "config.json"


def sessions_dir() -> Path:
    """One small JSON file per live agent session, written by the hook command."""
    return data_dir() / "sessions"


def log_file() -> Path:
    return data_dir() / "lumen.log"


def brand_icon(size: int = 256, white_shell: bool = False) -> Path:
    """The brand mark as a PNG, for notification toasts and window icons.

    `white_shell` picks the reversed variant, for dark surfaces like a Windows
    toast. It ships inside the package (and inside the frozen bundle) next to
    the dashboard, so this resolves from a checkout and from a PyInstaller build.
    """
    from lumen import server
    name = f"lumen-icon-white-{size}.png" if white_shell else f"lumen-icon-{size}.png"
    return Path(server.__file__).parent / "ui" / "brand" / name
