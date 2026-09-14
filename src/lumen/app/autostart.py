"""Start Lumen at login: HKCU Run key on Windows, a LaunchAgent on macOS, an
XDG autostart entry on Linux. No admin rights needed anywhere."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path


def command() -> list[str]:
    """How to launch the daemon again: the frozen exe, or this python on the package."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")  # no console window at logon
    return [str(exe), "-m", "lumen"]


def enabled() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                return bool(winreg.QueryValueEx(key, "Lumen")[0])
        except OSError:
            return False
    return _unix_file().exists()


def launch_background(open_ui: bool = False, wait_s: float = 20.0) -> None:
    """Release the Windows terminal; the child owns the tray and instance lock.

    Returns once a Lumen daemon answers on the configured port, and raises
    OSError when the child exits or never answers: creating a process is not
    the same as starting Lumen. The child's output goes to the log from the
    first byte, so an import error before run_app() redirects is kept too."""
    import time

    from lumen import paths
    from lumen.core.config import Config
    port = int(Config().settings["port"])
    log = paths.log_file()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        child = subprocess.Popen(command() + ["run", "--background"] + (["--open"] if open_ui else []),
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                 creationflags=0x08000000, close_fds=True)  # CREATE_NO_WINDOW
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if answering(port):  # checked first: a duplicate launch exits, but the running daemon answers
            return
        code = child.poll()
        if code is not None:
            raise OSError(f"the daemon exited with code {code}; see {log}")
        time.sleep(0.25)
    raise OSError(f"no Lumen dashboard on 127.0.0.1:{port} after {wait_s:.0f}s; see {log}")


def answering(port: int, timeout: float = 1.0) -> bool:
    """True if this version of Lumen serves the dashboard on port. A listening
    socket alone could be any program that took the port."""
    import json
    import urllib.request

    from lumen import __version__
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/state", timeout=timeout) as r:
            return json.load(r).get("version") == __version__
    except (OSError, ValueError, AttributeError):
        return False


def set_enabled(value: bool) -> None:
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if value:
                winreg.SetValueEx(key, "Lumen", 0, winreg.REG_SZ, subprocess.list2cmdline(command()))
            else:
                try:
                    winreg.DeleteValue(key, "Lumen")
                except FileNotFoundError:
                    pass
        return
    file = _unix_file()
    if not value:
        file.unlink(missing_ok=True)
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(_plist() if sys.platform == "darwin" else _desktop(), encoding="utf-8")


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _unix_file() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / "com.lumen.daemon.plist"
    return Path.home() / ".config" / "autostart" / "lumen.desktop"


def _plist() -> str:
    return plistlib.dumps({"Label": "com.lumen.daemon", "ProgramArguments": command(),
                          "RunAtLoad": True}).decode("utf-8")


def _desktop() -> str:
    return ("[Desktop Entry]\nType=Application\nName=Lumen\nComment=Universal device notifications\n"
            f"Exec={subprocess.list2cmdline(command())}\nX-GNOME-Autostart-enabled=true\n")
