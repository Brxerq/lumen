# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: one windowed binary with the dashboard bundled.
#   pyinstaller build/lumen.spec --distpath dist --workpath build/pyi -y

import sys

ui = [("../src/lumen/server/ui", "lumen/server/ui")]
hidden = [
    # adapters and integrations are imported by name at runtime
    "lumen.devices.asus_aura", "lumen.devices.openrgb", "lumen.devices.linux_backlight", "lumen.devices.mac_backlight", "lumen.devices.govee",
    "lumen.devices.hue", "lumen.devices.screen", "lumen.devices.notch", "lumen.devices.notification", "lumen.devices.sound",
    "lumen.integrations.claude_code", "lumen.integrations.claude_usage", "lumen.integrations.codex", "lumen.integrations.codex_usage", "lumen.integrations.webhook",
    "lumen.integrations.terminal", "lumen.integrations.github", "lumen.app.update",
] + (["pystray._win32"] if sys.platform == "win32" else ["pystray._darwin", "objc", "Foundation", "AppKit"] if sys.platform == "darwin" else ["pystray._xorg"])

a = Analysis(
    ["../src/lumen/__main__.py"],
    pathex=["../src"],
    binaries=[],
    datas=ui,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="lumen",
    icon="../src/lumen/server/ui/brand/lumen.ico",  # taskbar / explorer / installer icon
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
