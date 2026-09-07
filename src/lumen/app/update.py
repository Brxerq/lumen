"""Self-update from GitHub Releases.

    check()  -> {"current", "latest", "url", "asset", "frozen", "available"}
    apply()  -> downloads the release binary next to the running exe and
                hands off to a tiny script that swaps it once this process exits.

Only the frozen (PyInstaller) build can replace itself; a source install is
told to upgrade with its package manager. Network access happens only when the
user presses the button — the dashboard itself still loads nothing remote.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from lumen import __version__

REPO = "Brxerq/lumen"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = {"win32": "lumen.exe", "darwin": "lumen-macos", "linux": "lumen-linux"}.get(sys.platform, "")
SUMS = "SHA256SUMS"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.lstrip("v").split("-")[0].split(".") if p.isdigit())


def check(timeout: float = 8) -> dict:
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "lumen"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rel = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        rel = {}  # repo has no release yet: nothing newer to offer
    latest = str(rel.get("tag_name") or "").lstrip("v")
    by_name = {a.get("name"): a for a in rel.get("assets", [])}
    asset = by_name.get(ASSET)
    sums = by_name.get(SUMS)
    return {
        "current": __version__,
        "latest": latest,
        "url": rel.get("html_url", f"https://github.com/{REPO}/releases"),
        "asset": asset["browser_download_url"] if asset else None,
        "sums": sums["browser_download_url"] if sums else None,
        "frozen": bool(getattr(sys, "frozen", False)),
        "available": bool(latest) and _vtuple(latest) > _vtuple(__version__),
    }


def apply(on_ready, port: int = 6733) -> str:
    """Download, stage the swap script, then call on_ready() (the caller exits)."""
    info = check()
    if not info["available"]:
        return "Already up to date."
    if not info["frozen"]:
        # Not `pip install -U lumen`: the name on PyPI belongs to an unrelated
        # project, so an upgrade has to name this repository.
        return "Source install: run `pip install --upgrade git+https://github.com/Brxerq/lumen`."
    if not info["asset"]:
        return f"Release {info['latest']} has no {ASSET} asset yet."
    if not info["asset"].startswith(DOWNLOAD_PREFIX):
        return "Refusing: asset is not hosted on the project's GitHub releases."
    if not info["sums"]:
        return f"Release {info['latest']} has no {SUMS}, so the download cannot be verified; aborted."

    expected = _expected_digest(info["sums"])
    if not expected:
        return f"{SUMS} does not list {ASSET}; aborted."

    exe = Path(sys.executable).resolve()
    new = exe.with_suffix(exe.suffix + ".new")
    digest = hashlib.sha256()
    with urllib.request.urlopen(info["asset"], timeout=120) as r, open(new, "wb") as f:
        while chunk := r.read(1 << 16):
            digest.update(chunk)
            f.write(chunk)
    if new.stat().st_size < 1_000_000:
        new.unlink(missing_ok=True)
        return "Downloaded file looks wrong (too small); aborted."
    if digest.hexdigest() != expected:
        new.unlink(missing_ok=True)
        return "The download does not match the checksum published with the release; aborted."
    _spawn_swapper(exe, new, port)
    on_ready()
    return f"Updating to {info['latest']} — Lumen restarts in a moment."


def _expected_digest(sums_url: str, timeout: float = 30) -> str:
    """The SHA-256 for this platform's asset, from the release's SHA256SUMS.

    HTTPS already says the bytes came from GitHub; the checksum says they are
    the bytes the release workflow built. Without code signing this is the only
    thing standing between a swapped asset and a replaced binary."""
    if not sums_url.startswith(DOWNLOAD_PREFIX):
        return ""
    with urllib.request.urlopen(sums_url, timeout=timeout) as r:
        text = r.read(64 * 1024).decode("utf-8", "replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == ASSET and len(parts[0]) == 64:
            return parts[0].lower()
    return ""


def _spawn_swapper(exe: Path, new: Path, port: int = 6733) -> None:
    # ponytail: a shell script that waits for our PID is the whole updater; a
    # signed installer/MSIX is the upgrade path if code signing ever lands.
    pid = os.getpid()
    if sys.platform == "win32":
        script = Path(tempfile.gettempdir()) / "lumen-update.cmd"
        # The pauses here are the whole trick, and they have to be `ping`. This
        # script runs with CREATE_NO_WINDOW, so it has no console, and cmd's
        # `timeout` refuses to run without one: "Input redirection is not
        # supported, exiting the process immediately", returned in 0.03s. Every
        # wait was silently a no-op, which meant the new 21 MB binary was
        # launched the instant the move finished — inside the window where
        # Windows Defender still has it open — and the "did it come up?" check
        # ran before it could have. Twice that ended with no daemon at all.
        # `ping -n N 127.0.0.1` needs no console and sleeps N-1 seconds.
        #
        # "Came up" means the dashboard answers on its port, not that a process
        # with the right name exists: a start inside the Defender window can
        # leave a PyInstaller bootloader that never spawns the app — one idle
        # process, no listener, which a name check reads as success. Four
        # rounds of start-and-probe give the scan about a minute to finish.
        probe = f"netstat -ano | findstr /r /c:\":{port} .*LISTENING\" >nul"
        script.write_text(
            "@echo off\r\n"
            f":wait\r\ntasklist /FI \"PID eq {pid}\" | find \"{pid}\" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)\r\n"
            f":copy\r\nmove /y \"{new}\" \"{exe}\" >nul 2>&1 || (ping -n 2 127.0.0.1 >nul & goto copy)\r\n"
            "ping -n 4 127.0.0.1 >nul\r\n"
            "set /a tries=0\r\n"
            f":launch\r\nstart \"\" \"{exe}\"\r\n"
            "ping -n 16 127.0.0.1 >nul\r\n"
            f"{probe} && goto done\r\n"
            f"taskkill /f /im \"{exe.name}\" >nul 2>&1\r\n"
            "set /a tries+=1\r\n"
            "if %tries% LSS 4 goto launch\r\n"
            ":done\r\n"
            "del \"%~f0\"\r\n", encoding="utf-8")
        subprocess.Popen(["cmd", "/c", str(script)], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000,
                         close_fds=True)
    else:
        script = Path(tempfile.gettempdir()) / "lumen-update.sh"
        script.write_text(
            "#!/bin/sh\n"
            f"while kill -0 {pid} 2>/dev/null; do sleep 1; done\n"
            f"mv -f '{new}' '{exe}' && chmod +x '{exe}' && sleep 3\n"
            # Same start-and-probe as on Windows; `sleep` needs no console here,
            # but a first launch can still be slow enough to be worth a retry.
            "for try in 1 2 3 4; do\n"
            f"  nohup '{exe}' >/dev/null 2>&1 &\n"
            "  sleep 15\n"
            f"  lsof -nP -iTCP:{port} -sTCP:LISTEN >/dev/null 2>&1 && break\n"
            f"  pkill -f '{exe}'\n"
            "done\n"
            f"rm -f '{script}'\n", encoding="utf-8")
        script.chmod(0o755)
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, close_fds=True)
