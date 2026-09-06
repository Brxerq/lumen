# Development

## Setup

```bash
git clone https://github.com/Brxerq/lumen lumen && cd lumen
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

An editable install puts `lumen`, `pytest`, `ruff` and `pyright` on PATH inside the
virtualenv, so every command below is the one from this checkout.

## Run

```bash
LUMEN_HOME=./.dev-home lumen run --no-tray --open   # foreground daemon, separate config, opens the dashboard
lumen scan                                          # what discovery finds
lumen test asus-aura-lightbar wave '#00a0ff'        # play one effect from the CLI
lumen emit build.failed --data name=web             # raise an event against the running daemon
```

The dashboard's static files are served straight from `src/lumen/server/ui`; reload the browser to see CSS/JS changes. Python changes need a daemon restart.

Only one daemon can run at a time (named mutex on Windows, lock file elsewhere). If a packaged build is running from your tray, quit it first.

## Test

```bash
pytest                 # fast, no hardware
```

`tests/test_api.py` boots the real engine and HTTP server with fake devices — the best template for end-to-end tests of new features.

## Package a standalone binary

```bash
pyinstaller build/lumen.spec --distpath dist --workpath build/pyi -y
```

Produces `dist/lumen.exe` (Windows) or `dist/lumen` — windowed, tray icon, no console; output goes to the log file. Enable *Start at login* from the tray or Settings to register the binary.

## Layout

```
src/lumen/
  cli.py  paths.py
  core/          events, devices, rules, effects, config, integrations, engine
  devices/       one adapter per hardware family
  integrations/  one module per event source
  server/        api.py + ui/ (index.html, styles.css, app.js)
  app/           tray.py, autostart.py
tests/
docs/
plugin/          Claude Code plugin (launches Lumen from a chat)
build/           PyInstaller spec
```

See `docs/ARCHITECTURE.md` for how the pieces talk to each other and `docs/PLUGINS.md` to add devices or integrations.

## Lint and types

```bash
ruff check src tests        # config in pyproject.toml
pyright                     # config in pyrightconfig.json
```

Both run in CI. Pyright checks `core/`, `server/`, `app/` and the top-level modules — the code with no untyped dependency. Device adapters and integrations wrap libraries with no type information of their own (`hid`, `openrgb`, `tkinter`, sqlite rows, `gh` JSON), where a type checker reports the library rather than this code.

## Releasing

1. `pytest` green on Windows, macOS and Linux (CI does all three).
2. Move *Unreleased* in `CHANGELOG.md` under the new version.
3. Bump and tag:

```bash
python scripts/bump.py 0.3.0      # __init__.py, plugin.json, CHANGELOG
git tag v0.3.0 && git push --tags # the release workflow builds and attaches the binaries
```

`pyproject.toml` has no version of its own: hatchling reads `lumen.__version__`, and a test asserts the plugin manifest agrees.
