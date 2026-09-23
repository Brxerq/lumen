"""Where Lumen keeps its files, per platform.

Everything lives under one directory so a user can find (or delete) all of it:

    Windows  %LOCALAPPDATA%\\lumen
    macOS    ~/Library/Application Support/lumen
    Linux    $XDG_CONFIG_HOME/lumen  (default ~/.config/lumen)

Override with the LUMEN_HOME environment variable (tests do).
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import time
from pathlib import Path


def write_atomic(path: Path, text: str, mode: int | None = None) -> None:
    """Write `text` to `path` through a temp file and os.replace, so a reader
    never sees half a file.

    Windows refuses the replace with WinError 5 while any other handle has the
    target open — another poller thread reading it, an antivirus scan. That
    used to fail the whole poll and leave the temp file behind, one per
    failure, hundreds of them. Retry briefly, and never leave the temp."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is not None:  # mkstemp is 0600; keep whatever the file had
            os.chmod(tmp, mode)
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        Path(tmp).unlink(missing_ok=True)


def unlink_quietly(path: Path) -> None:
    """Delete a file that may be open elsewhere. On Windows an open handle makes
    unlink raise PermissionError; the caller's next tick tries again."""
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        pass


def basename(path: str) -> str:
    """The last segment of a path out of an agent's hook payload, either separator.

    `Path(...).name` asks the OS *Lumen* runs on what a separator is, and the
    payload comes from the agent, which need not be the same machine — a WSL or
    container tab reporting to a Windows daemon, or the reverse. On Linux,
    `Path(r"C:\\proj\\api.py").name` is the whole string."""
    return re.split(r"[\\/]", str(path or "").rstrip("/\\"))[-1]


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
