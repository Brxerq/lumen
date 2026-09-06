# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **A volume for the sound.** The slider on each device row is a dimmer for a
  light and the volume for the speaker, 5% to 100%. It is rendered into the tone
  rather than set on a mixer: Windows' `winsound` has no volume control at all,
  and per-application volume elsewhere means talking to the sound server.
- **Four more tones**, so a notification can sound like the thing that happened:
  **bell** (soft, rings on), **ping** (a quiet nudge), **fanfare** (a big one
  landed) and **alert** (look now). The Test menu on the sound device now plays
  each one by name, so you can hear them before assigning one.
- **Send one agent to one device.** Each device row on the Devices page has an
  "agent tabs" menu: point the keyboard at Codex and the light bar at Claude,
  or switch a device out of agent status entirely. Picking one for the first
  time splits the shipped wildcard automation into one action per device, so
  "Codex here" does not quietly mean "Codex everywhere". The same control is
  still in the automation editor for anyone building a rule by hand.

## [0.4.0] — 2026-09-06

### Added
- **One-line install, no Python.** `curl -fsSL
  https://brxerq.github.io/lumen/install.sh | sh` on macOS and Linux, `irm
  https://brxerq.github.io/lumen/install.ps1 | iex` on Windows. Each fetches the
  standalone build for the platform from the latest release, verifies it against
  the published SHA-256 before writing anything, and puts `lumen` on the PATH.
  The old instruction — `uv tool install git+https://github.com/Brxerq/lumen` —
  asked for a Python toolchain to install a program that ships as a single
  binary; it is still there, under a fold, for people who want to build from
  source.
- Four notification tones you can assign per automation: **chime** (a task
  landed), **blip** (something happened), **knock** (needs you) and **descend**
  (something failed). They are synthesised into the data directory on first use
  rather than shipped as audio files, so nothing is downloaded, nothing is
  licensed from anyone, and a Mac and a PC make the same sound instead of each
  playing whatever its desktop theme happens to have. The old names (`default`,
  `success`, `error`, `attention`) still resolve, so existing rules keep
  working.
- The site shows what a signal actually looks like on three classes of machine:
  a zoned laptop keyboard with a light bar, a MacBook with no RGB at all (screen
  edge glow, native notification, sound), and a per-key board with one key per
  agent tab. One scenario, played on each, in SVG.

### Changed
- **The dashboard tells you which agent tabs actually reach your hardware.** A
  keyboard's zones are drawn as a keyboard and a light bar as a bar, each zone
  labelled with the tab it is showing, worked out from your automations rather
  than guessed. Tabs past the last zone are still listed — under a line saying
  how many zones you have and what to do about it — instead of silently sitting
  in the list looking identical to the ones that are lit.
- The ASUS adapter is described as what it is: ASUS laptops with Aura Core
  lighting, which is ROG, TUF and Zephyrus, not ROG alone. Detection is
  unchanged; only every place that said "ASUS ROG laptops" is.
- The site's privacy section no longer says the update check is the only request
  Lumen makes. Pairing a Hue can fall back to Philips' bridge lookup, and a page
  making a "no outbound calls" promise has to count that one.
- Uninstalling agent hooks now also recognises an unquoted `python -m lumen
  hook`, which was being left behind next to the freshly written one.
- Hue light ids drop the bridge's separators instead of keeping a truncated
  slice of them.
- The dev dependency list lives in one place: the `dev` extra, which `pip
  install -e ".[dev]"` reaches directly and CI installs verbatim.
- **The dashboard is legible.** Its text was set at eleven ad-hoc sizes, two
  thirds of it under 14px, and the colour carrying the smallest text failed
  contrast on every surface it appeared on. There is now one type ramp, with
  14px as the floor for anything meant to be read rather than glanced at, and no
  text on any page in either theme falls below WCAG AA. The agent-tab labels
  drawn inside a lit zone were the worst of it — black on a red "needs you" zone
  was 1.5:1 — and they now sit on a plate that holds them legible over any
  colour you can pick.
- **The live board takes the full width of the page.** It is the thing the app
  is for, and it was sharing a column: its zone tiles were 143px wide, and the
  feed beside it ended a thousand pixels short of the tab list, which is what
  made the page look like a column that had run out. The board is now the full
  measure and the feed is a rail that tracks whatever you scroll past.
- The brand mark follows the theme. Its capsule was fixed to `#191C23`, so on
  the dark sidebar it was black on black and only the two light surfaces were
  visible.
- Hit targets grew: the switches, filter chips, slot badges, icon buttons and
  the brightness slider were all under 32px.
- Reduced motion stops the decorative pulse and the modal entrances but leaves
  the scan spinner turning, because it is the only thing that says a scan is
  still running.
- The dashboard builds its sound menu from what the adapter reports instead of a
  hardcoded list, so a new tone shows up without touching the UI. The playground
  gained a sound picker, so you can hear one before assigning it.
- The project site is rebuilt on the product's own design tokens, palette and
  brand kit. The hero is the Lumen mark itself, its two light fields cycling the
  real default status colors, and the page documents what Lumen actually does:
  supported hardware, where events come from, the rule model, and the local-only
  guarantee. It scrolls normally now instead of hijacking the wheel, honours
  `prefers-reduced-motion`, and needs no animation library.
- `LICENSE` carries this project's own copyright.
- Install instructions point at this repository. `pip install lumen` and `uv
  tool install lumen` fetch an unrelated project of the same name from PyPI,
  which the README and the site were both telling people to run.

### Removed
- **uv.** It was the toolchain in CI, in the release build and in every
  published install instruction, but nothing here needs it: the metadata is
  standard PEP 621 and the backend is hatchling. CI and the release workflow use
  `pip install -e ".[dev]"`, and the install line is now `pip install
  git+https://github.com/Brxerq/lumen`. `uv.lock` went with it, which means
  release binaries build against whatever pip resolves rather than a pinned set.
- Dead code left behind by the pre-hero dashboard and by helpers nothing calls:
  `AgentIntegration.current_status`, `read_statuses`, `hooked_by` and `fold`
  (the last three had test callers only, and the tests now compose
  `read_sessions` / `session_statuses` / `aggregate` directly), the unused
  `autostart.LABEL`, the dashboard's `L.saveDraftValid`, and thirteen CSS rules
  for the stat tiles and card header the current layout no longer renders.
- `docs/assets/keyboard_wide.mp4` and `docs/assets/gsap.min.js` (4.2 MB of old
  demo footage and a library the new page does not need).
- `docs/PLAN.md` and `docs/brand/validation.json`: an internal audit plan and an
  asset-check report, neither of which described the project to anyone reading
  it. What was still true in the plan lives in `docs/ROADMAP.md` and the issue
  tracker.

### Security
- Device ids are pinned to a safe character set for every adapter, in the device
  model itself. Some ids are chosen by the network — a Govee light announces its
  own — and an id ends up as a config key, a path segment in `/api/devices/<id>`
  and an argument in the dashboard's click handlers.
- The dashboard escapes values for JavaScript *and* HTML before dropping them
  into an inline handler. Escaping only for HTML turned a quote into `&#39;`,
  which the HTML parser handed straight back to JavaScript as a quote.
- Static files are checked against the UI directory with `is_relative_to`
  instead of a string prefix, so a sibling directory whose name merely starts
  with the same characters is no longer served.
- `notify-send` gets a `--` separator, so a notification whose text begins with
  a dash is text rather than an option.
- Every GitHub Action is pinned to a commit rather than a moving tag, with the
  version in a trailing comment. Two of them can write to this project's
  releases. CI also drops to `contents: read` instead of inheriting whatever the
  repository default is.

## [0.3.1] — 2026-09-06

### Fixed
- The relaunch after a self-update could fail with a Windows loader dialog,
  leaving no daemon running at all: a 20 MB binary written a moment earlier is
  still being scanned, and starting it inside that window does not work. The
  swap script now waits for the file to settle and starts a second time if
  nothing came up.

## [0.3.0] — 2026-09-06

Hardening pass over the local API and the daemon's edges, plus the dashboard
work that makes several devices and several agent tabs manageable by hand.

### Added
- **Agent tabs can be arranged by hand.** Drag a tab (or use the arrows) to give
  it a keyboard zone, and name it. The choice is pinned to both the session and
  its project folder, so it survives the tab, a daemon restart, and the next tab
  you open there.
- Live updates over server-sent events (`GET /api/stream`) instead of polling
  every two seconds.
- Automations can be reordered, duplicated, exported and imported; the editor
  refuses to save a rule with no trigger, no actions, or a device that is not
  connected.
- Reusable effect presets, a per-device dimmer, and quiet hours (colours only,
  or fully dark, between two times).
- Devices page shows the last error, offers a prefilled issue link for
  unverified models, and can forget a device that is gone. The activity feed can
  be filtered and cleared.
- A light theme, and keyboard/screen-reader support for the switches, icon
  buttons and dialogs.
- The updater verifies the download against the release's `SHA256SUMS` and
  refuses to install anything that does not match; the release workflow
  publishes those checksums.
- A device that fails a write now triggers an immediate rescan (debounced)
  instead of staying dark until the next scheduled one, and shows its last error
  on the Devices page.
- ASUS Aura devices warn when Armoury Crate is running, since both sides repaint
  the same registers.
- Self-update: Settings → About has **Check for updates**; `GET/POST
  /api/update` compare with the latest GitHub release and, for the frozen build,
  download it and restart. Release workflow (`.github/workflows/release.yml`)
  builds the binaries on a `v*` tag.
- `agents.sessions` event and `sessions` effect: one zone per live agent session
  (Claude Code / Codex tab), each in its own status color. Sessions keep a
  sticky slot; `offset` continues the slots on a second device.
- Dashboard "Agent sessions" panel with live per-zone strips, one row per
  session (project, status, age) and a dismiss button; `DELETE
  /api/sessions/<id>`.
- Test menu has a color picker; session-zone rules have a per-status palette, an
  agent filter (keyboard = Claude, light bar = Codex) and a whole-device layout.

### Fixed
- **The local API now refuses cross-origin and rebound requests.** It has no
  login — being on loopback was the whole defence — so any page in the user's
  browser could have driven the daemon, and a hostile DNS name resolving to
  127.0.0.1 could have reached it. Every request must present a loopback `Host`,
  and a loopback `Origin` if it sends one; `POST /api/events` keeps its
  bearer-token door open for CI.
- Adapter setup actions resolve against the real adapter registry instead of
  importing the module name the client sent.
- Settings that would stop the daemon starting (a port outside 1024–65535, a
  negative rescan interval) are refused rather than saved, and the server falls
  back to the next free port instead of failing to come up.
- Installing agent hooks refuses to overwrite a `settings.json` it cannot parse,
  rather than replacing a hand-edited file with defaults.
- `lumen exec` quotes arguments the way `cmd.exe` reads them on Windows, so an
  argument with spaces survives.
- Agent tabs closed without a `SessionEnd` hook are dropped once the agent's own
  record forgets them, instead of holding a keyboard zone lit for four hours.
- The log keeps one previous generation instead of truncating in place and
  losing the lines you wanted.
- A `slots.json` path was built with a backslash inside an f-string, which is a
  syntax error on Python 3.11 — a version this package supports.
- ASUS Aura keyboard/light bar flickered on every background rescan: the adapter
  re-opened the HID handle and re-sent the init reports each time. Devices are
  now reused across rescans.
- Rule editor lost focus after every keystroke in the name field; color presets
  changed the rule without updating the swatch.

## [0.2.0] — 2026-09-05

First release of Lumen: a universal device-feedback platform.

### Added
- Automatic device detection with a capability model; adapters for ASUS Aura
  laptops (HID), OpenRGB, Linux keyboard backlights, Philips Hue, Govee (LAN),
  screen edge glow, system notifications and system sounds.
- Event bus and automation rules (`WHEN event THEN effects`), persistent base
  colors plus transient flash/pulse/wave effects, graceful degradation to
  brightness-only devices.
- Integrations: Claude Code and Codex with one-click hook install and hook-free
  fallbacks, GitHub Actions (via `gh`), terminal (`lumen exec`, `lumen emit`,
  `lumen timer`, shell snippet), webhook.
- Web dashboard on `127.0.0.1:6733`: onboarding wizard, dashboard, devices,
  automations builder, integrations, effects test bench, settings.
- Cross-platform tray app, start at login (Windows / macOS / Linux), single
  instance, `LUMEN_HOME`.
- Reduce-flashing accessibility setting.
- Docs: architecture, plugin guide, API, roadmap, development; issue and PR
  templates; CI on three platforms.

[unreleased]: https://github.com/Brxerq/lumen/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Brxerq/lumen/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Brxerq/lumen/releases/tag/v0.3.1
