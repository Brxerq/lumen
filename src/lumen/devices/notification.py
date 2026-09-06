"""The operating system's notification center as a device.

Windows uses a WinRT toast through PowerShell (nothing to install), macOS
`osascript`, Linux `notify-send`. Always available, so even a machine with no
lights at all gets a visible "your task is done".
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys

from lumen import paths
from lumen.core.devices import NOTIFY, Device

_WINDOWS_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$title = [System.Security.SecurityElement]::Escape([Environment]::GetEnvironmentVariable('LUMEN_TITLE'))
$body = [System.Security.SecurityElement]::Escape([Environment]::GetEnvironmentVariable('LUMEN_BODY'))
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$icon = [System.Security.SecurityElement]::Escape([Environment]::GetEnvironmentVariable('LUMEN_ICON'))
$logo = if ($icon) { "<image placement='appLogoOverride' hint-crop='none' src='$icon'/>" } else { '' }
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'>$logo<text>$title</text><text>$body</text></binding></visual></toast>")
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
"""


def _run(cmd: list[str], env: dict | None = None) -> None:
    flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW
    subprocess.run(cmd, check=False, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   env={**os.environ, **(env or {})}, **flags)


def _icon() -> str:
    """The brand mark for the toast, or "" if the asset is missing.

    Windows toasts and most Linux notification themes are dark, so the reversed
    (white shell) variant is the one that reads."""
    icon = paths.brand_icon(white_shell=True)
    return str(icon) if icon.is_file() else ""


def send(title: str, message: str) -> None:
    if sys.platform == "win32":
        encoded = base64.b64encode(_WINDOWS_TOAST.encode("utf-16-le")).decode()
        _run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
             {"LUMEN_TITLE": title, "LUMEN_BODY": message, "LUMEN_ICON": _icon()})
    elif sys.platform == "darwin":
        # No icon here: osascript notifications always wear Script Editor's.
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
        _run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'])
    elif shutil.which("notify-send"):
        icon = _icon()
        # "--" first: a title or body starting with a dash is text, not a flag.
        _run(["notify-send", "--app-name=Lumen", *(["--icon", icon] if icon else []), "--", title, message])


class NotificationCenter(Device):
    def __init__(self):
        system = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")
        super().__init__(id="notification", name=f"{system} notifications", kind="notification", vendor=system,
                         capabilities=frozenset({NOTIFY}), details={"connection": "built-in"})

    def notify(self, title: str, message: str) -> None:
        send(title, message)


def discover() -> list[Device]:
    if sys.platform not in ("win32", "darwin") and not shutil.which("notify-send"):
        return []
    return [NotificationCenter()]
