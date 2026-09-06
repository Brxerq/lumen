# Contributing to Lumen

Thanks for helping. Lumen is small on purpose: the core is a few hundred lines, and everything hardware- or service-specific lives in a plugin module. Most contributions never touch the core.

## Ways to contribute

- **A device adapter** for hardware you own — see [docs/PLUGINS.md](docs/PLUGINS.md). Even an untested-by-us adapter is welcome if you verified it on real hardware; say so in the PR.
- **An integration** (a new event source): CI systems, editors, chat tools, timers.
- **Verification**: try Lumen on a machine we don't have (a MacBook, a Framework laptop, a Hue setup) and report what happened, with the `lumen scan` output.
- **Docs & screenshots**, UI polish, accessibility.

## Development setup

```bash
git clone https://github.com/Brxerq/lumen lumen && cd lumen
python -m venv .venv && . .venv/bin/activate   # Windows: .venvScriptsactivate
pip install -e ".[dev]"
lumen run --no-tray          # daemon in the foreground, dashboard on http://127.0.0.1:6733
pytest                       # tests (no hardware needed)
```

Set `LUMEN_HOME=/some/dir` to keep a development config separate from your real one. More in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Ground rules

- **Adapters never raise out of `discover()`.** A missing driver or unplugged device is an empty list, not a crash.
- **The hook path stays fast.** `lumen hook` runs on every agent tool call; it must import only the standard library.
- **Dependencies are a cost.** The core uses the standard library. Optional imports belong inside the adapter that needs them.
- **Effects must degrade.** If your device can't do color, say so in `capabilities` and let the player pulse its brightness instead.
- **No telemetry, no network calls the user didn't ask for** (device discovery on the LAN is fine; phoning home is not).
- Keep the style of the surrounding code: type hints, short docstrings that explain *why*, tests for logic with branches.

## Pull requests

1. Open an issue first for anything bigger than a bug fix, so we agree on the shape.
2. One topic per PR. Include a screenshot for UI changes and the `lumen scan` output for device work.
3. `pytest` must pass. Add tests for new logic; hardware I/O can be faked (see `tests/test_lightbar.py`).
4. Update the supported-devices table in the README and `docs/ROADMAP.md` if relevant.
5. Add a line to `CHANGELOG.md` under *Unreleased*.

## Reporting bugs

Use the bug template. The log file (`Settings → Open log`, or `~/.config/lumen/lumen.log` / `%LOCALAPPDATA%\lumen\lumen.log` / `~/Library/Application Support/lumen/lumen.log`) and `lumen scan` output make most reports solvable in one round trip.

## Code of conduct

Be kind, assume good faith, and keep discussions about the code. Maintainers may edit or close contributions that don't follow this.
