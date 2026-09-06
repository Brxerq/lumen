---
name: lumen
description: Set up and launch Lumen, the universal device-notification daemon
  that makes your keyboard, lights, screen and notifications react when your AI
  agent finishes, needs input, or a build fails. Use when the user asks to start,
  launch, run, install, or set up Lumen.
disable-model-invocation: true
---

# Launch Lumen

Lumen is a long-running background daemon with a tray icon and a dashboard at
http://127.0.0.1:6733. It must run in the user's desktop session (it opens USB
devices and shows a tray icon), so launch it detached from this turn.

## Preflight

1. **Python ≥ 3.11** — `python3 --version` (Windows: `py --version`). If missing,
   point at https://www.python.org/downloads/ and stop.
2. **Lumen installed** — `lumen --version`. If missing, offer
   `pip install git+https://github.com/Brxerq/lumen` and stop.
3. **Not already running** — `curl -s http://127.0.0.1:6733/api/state` succeeding means
   it is up; just open the dashboard (`lumen open`) and stop.

## Launch

Detached, so it outlives this session:

- macOS: `osascript -e 'tell application "Terminal" to do script "lumen"'`
- Linux: `nohup lumen >/dev/null 2>&1 &`
- Windows (PowerShell): `Start-Process lumen -WindowStyle Hidden`

(Lumen is not on PyPI — the name there belongs to an unrelated project. Install it
with `pip install git+https://github.com/Brxerq/lumen`.)

## Connect this agent

Run `lumen connect claude` (or `codex`) so the agent's hooks report status.
Sessions started after this point light up the devices; this session may not.

## Confirm

Tell the user the dashboard is at http://127.0.0.1:6733, that Lumen sits in the tray,
and that the first-run wizard scans devices, tests them and creates the first
automation. To stop it: tray icon → Quit.
