# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.7.8] — 2026-09-09

### Changed
- Workspace status now leads the dashboard and stays visible; Device preview follows and also stays expanded.
- The standard completed-task green is now a deeper green across new rules, presets and previews.

### Fixed
- Completed status-tab rows automatically disappear after 30 minutes by default. The delay is configurable in Settings; active and input-needed tasks remain visible.

## [0.7.7] — 2026-09-08

### Added
- Force sync refreshes agent records and device output, with progress and failure feedback.

### Changed
- Device preview appears first and expanded on the dashboard; other sections start collapsed.
- Running tasks appear first in the dashboard and notch, with clearer typography and smoked glass dashboard surfaces.

### Fixed
- Recover quiet Codex tasks after restart and retry recovery after temporary database failures.
  Discovery of unknown tasks is limited to the past 24 hours to avoid reviving abandoned history;
  known and hooked tasks remain tracked without an age limit.
- Completed and archived records override stale running hooks; unavailable tracking preserves the last snapshot.
- Idle Claude tabs no longer take device zones, and dismissing one task does not hide other tasks in its project.
- Refresh notch details every half-second even when device colors stay unchanged.
- Preserve dashboard disclosure choices and report force-sync results correctly during overlapping live updates.

## [0.7.6] — 2026-09-08

### Changed
- Simplified the dashboard around live tasks, connected devices and recent activity,
  with secondary information tucked away and clearer empty, paused and offline states.
- Grouped Settings into everyday controls and expandable options, with updates easier to find.
- Improved narrow-screen navigation, keyboard access and light/dark styling.

### Fixed
- Intel Macs now receive a native Intel binary during installation and updates.
  Release and CI jobs cover both Intel and Apple Silicon.
- Corrected the macOS keyboard-backlight discovery API name.
- Start at login now handles installation paths containing XML special characters.
- Mac paths retain their native separators; offline instructions use the correct launch command.
- Self-updates on macOS and Linux handle apostrophes in installation paths and use a private temporary script.
- More reliable active Codex session tracking and stable task placement on the status tab.

## [0.7.5] — 2026-09-07

### Changed
- **Both agents' limits stay on the status tab.** Since 0.7.1 a limit meter
  was hidden while its agent had no tab open, which read as "Codex disappears
  when I pick Claude". A known limit now stays up regardless; the old
  behaviour is a tick under What it shows → "Limits only while that agent has
  a tab open".

## [0.7.4] — 2026-09-07

### Added
- **The status tab can live on the left or right edge.** Settings → Where it
  sits gains Left edge and Right edge: the folded tab lies along the edge on
  its side, and hovering opens the panel upright beside it. Dragging moves it
  up and down the edge, and the offset is saved the same way.
- **Each bar wears its agent's colour.** A small cap at the leading end of a
  bar — Claude orange, Codex blue — says whose tab it is, whatever state the
  bar itself shows. Settings → What it shows → "Agent colour on each bar".
- **Hide when idle.** Settings → Hide when idle takes a number of minutes; once
  no tab has been working or waiting on you for that long, the tab goes away
  and comes back the moment an agent does something. 0 (the default) keeps it up.

## [0.7.3] — 2026-09-07

### Fixed
- **Claude limits show again on machines that only use the desktop app.** Only
  the `claude` CLI rotates the token in `~/.claude/.credentials.json`; the
  desktop app keeps its own login, so the file aged out and Lumen reported
  "no Claude Code login" for good while Codex, whose CLI refreshes its own
  file, kept working. Lumen now refreshes an expired token with the refresh
  token, the way the CLI does, and writes the new pair back so the CLI stays
  signed in. The macOS keychain is still read-only.
- **The notch's folded number no longer goes blank** for an account with no
  5-hour window (Codex Pro Lite): it falls back to the week.

### Added
- **Drag the status tab anywhere along its edge.** Press and drag the tab
  and it follows; let go and the spot is saved (`notch_offset`, as a
  percentage of the screen width) so it comes back there next start. Settings
  has the same knob as a slider, with Reset to return to the preset corner.
- **Thin, regular or thick.** Settings → Thickness picks the folded tab's
  height and width; the unfolded panel is unchanged.
- **Opacity** for the tab, 30–100 %.
- **Double-click pins the tab open** so the session list and meters stay up
  without hovering; double-click again to let it fold.
- **Per-model weekly limits.** The Opus, Sonnet and scoped-model weeks the
  usage endpoint reports beside the all-models week now appear as extra meters
  on the dashboard and the notch, and as `seven_day_<model>_used` rule fields.

## [0.7.2] — 2026-09-07

### Fixed
- **Updating restarts Lumen again.** Pressing Update swapped the binary in
  correctly and then left the machine with nothing running, every time. The
  swap script relaunched the new binary as its own child, and spawned by the
  frozen, windowed daemon that never worked — the identical script run from a
  console brought Lumen up in seconds. The restart is now handed to a one-shot
  Windows Scheduled Task, which starts it in a clean session with nothing
  inherited from us, and the task is deleted again once the dashboard answers.
- **The update log records what actually happened.** `retry %tries%>>"log"`
  expands to `retry 1>>"log"`, so cmd read the counter as a stream number and
  ate it: the first retry logged a blank number and the second and third logged
  nothing at all, which made one failed attempt look like three.
- **Usage meters go quiet instead of freezing.** When the Claude or Codex login
  stopped working, the last good reading stayed on the status tab and the
  Integrations page indefinitely, presented as current — a "58%" from hours ago
  is worse than no number. A reading older than twenty minutes (four missed
  polls) is now dropped, and the row says why the limits are unavailable.

## [0.7.1] — 2026-09-07

### Fixed
- **A limit meter only shows while that agent is open.** Codex's weekly limit sat
  on the status tab hours after Codex had been closed. The number was true — the
  limit is account-wide and does not care whether anything is running — but the
  tab is a picture of what is happening now, so a meter for an agent with no tabs
  open reads as stale data rather than as information. Close Codex and its meter
  goes with it; open it again and it comes back; with both open you see both.

## [0.7.0] — 2026-09-07

### Added
- **Keep the lights on when paused.** Pausing has always meant "stop reacting"
  *and* "hand the hardware back", which takes the light with it — a keyboard in
  direct mode reverts to its own stored profile the moment nothing is writing to
  it. Settings → "Keep the lights on when paused" holds the device instead, so it
  stays exactly as it is until you unpause.

### Fixed
- **Codex's limit is named by its length, not its rank.** Codex reports its
  windows as "primary" and "secondary", and Lumen read those as the 5-hour and
  the 7-day limit. They are not the same thing: on a Pro Lite account the
  primary window *is* the weekly limit (`limit_window_seconds` 604800) and there
  is no secondary one, so a 7-day limit at 100% was shown as "5h 100%" — sitting
  there long after Codex was closed, with no five-hour reset ever coming to
  explain it. Each window is now identified by the length it declares.
- **"Only this agent's tabs" now covers the limit meters too.** The status tab's
  agent filter trimmed the session rows but left the other agent's usage meter
  on screen, which is the opposite of what picking one agent asks for.

## [0.6.3] — 2026-09-07

### Fixed
- **The update actually restarts Lumen.** Swapping the binary always worked;
  starting it again did not. cmd's `start`, from a script with no console and
  no valid stdio to inherit, never produced a working daemon here — three
  updates, three times nothing, while the same binary launched by hand was
  answering in 2.5 seconds. The relaunch goes through PowerShell's
  `Start-Process` now, which gives the new process a clean environment, and the
  script probes the dashboard port every two seconds for thirty rather than
  killing a daemon that was merely still starting. Measured end to end: back up
  6.6s after the swap.
- **A failed update leaves evidence.** Every step writes to `update.log` beside
  the config, so an update that ends with no Lumen says why instead of only
  being gone.
- **The swap script is written with the line endings it says.** `write_text`
  translated each `

` again, so the file was really CR CR LF throughout.

## [0.6.2] — 2026-09-07

### Fixed
- **A settings change shows on the device straight away.** Settings that live
  on a device — where the status tab sits, which meters it shows, whose
  sessions it follows — are read when that device is discovered, but the
  repaint that would show them ran *before* the rescan that reads them. The
  first repaint could only carry the old values, so the change waited for
  whatever event next happened to write to the device: a minute, or until an
  agent tab did something. The repaint now happens again once the rescan has
  settled.

## [0.6.1] — 2026-09-07

### Fixed
- **Updating actually leaves Lumen running.** Every pause in the Windows swap
  script was silently doing nothing. The script is spawned without a console,
  and cmd's `timeout` refuses to run without one — it exits immediately with
  "Input redirection is not supported", so `timeout /t 3` returned in 0.03s.
  The new 21 MB binary was therefore launched the instant the move finished,
  while Windows Defender still had it open, and the "did it come up?" check ran
  before it could have. That ended an update with a process that never opened
  its port, and then with no process at all. The pauses are now `ping -n`,
  which needs no console, and the script gives the start four rounds of
  start-and-probe — about a minute — before it gives up.
- **A tab's file name in the status tab, not its whole path.** `Path(...).name`
  asks the OS Lumen runs on what a separator is, but hook payloads come from the
  agent, which need not be the same machine. A Windows path read on Linux came
  back whole, so a row read "Editing C:\proj\src\api.py".

## [0.6.0] — 2026-09-07

### Added
- **The status tab acts, moves and breathes.** Click a session row and that
  tab's terminal or IDE comes to the front (Windows, macOS with System
  Events, Linux with `xdotool`). Settings picks where it sits — top centre,
  top left or right, or the bottom edge, which keeps it clear of a MacBook's
  notch — and whether it hides while a game, film or presentation runs full
  screen. The panel unfolds instead of popping, and a tab that just started
  waiting on you pulses its bar for a moment.
- **What each tab is doing, and what it cost.** Hooks record the current tool
  ("Editing api.py", "Running: pytest -q", "Asking you") and the transcript's
  token totals per session; the panel shows the activity next to the tab's
  name and the session's cost in dollars beside its context use. Both ride in
  the `agents.sessions` event for the dashboard and other devices.
- **Codex usage limits** the same way as Claude's: read from `~/.codex/auth.json`
  (never refreshed), polled every five minutes, shown on the Codex card and
  emitted as `codex.usage`.
- **Rules on usage.** `claude.usage` and `codex.usage` are in the rule builder
  with threshold filters ("five_hour_used is >= 90"); any filter value that
  starts with `>=`, `<=`, `>` or `<` compares as a number. Both cards on the
  Integrations page show the two limits as meters, with a burn-rate line
  ("at this pace, full in 1h 40m") once a few samples are in.
- **Choose what the status tab shows.** Settings → "Whose tabs" (Claude, Codex
  or both) and "What it shows" (live tabs, activity, context window, session
  cost, Claude limits, Codex limits) — untick down to just the Claude 5-hour
  and 7-day limits and the live tabs if that is all you want. Both agents'
  limits are labelled separately when both are on.
- **Log viewer.** Settings → Advanced → Log shows the tail of `lumen.log`
  in the dashboard, so a Mac problem can be read without hunting for a file.
- **`lumen selfcheck`** imports every adapter and integration and draws the
  status tab off-screen. CI runs it on all three platforms, and the release
  workflow runs it against the frozen binary before uploading, so a missing
  hidden import fails the release instead of a user's first launch.
- **Signing hooks in the release workflow**, active when the certificate
  secrets exist, plus a Homebrew formula and winget manifest set under
  `docs/packaging/` ready to publish once releases are signed.

### Changed
- Pyright now covers the whole package (devices and integrations included) and
  reports nothing.

## [0.5.0] — 2026-09-07

### Changed
- **Your open tabs share the whole device.** A four-zone keyboard used to light
  one zone per tab and leave the rest dark, which reads as broken hardware
  rather than as free seats. Now one tab lights the whole keyboard, two take
  half each, and three across four zones take 2 / 1 / 1. The dashboard draws
  those blocks the way the hardware shows them — one block per tab, not four
  squares of two colours — and numbers tabs by their place in the list instead
  of by a raw slot that could read "10" with four tabs open.

### Fixed
- **MacBook keyboard backlight not detected.** The adapter always talked to
  keyboard id 1, which newer Apple Silicon Macs don't use, so the read-back
  failed and the device was silently dropped. It now asks CoreBrightness for
  the real id, and a failure to load the framework is written to the log
  instead of swallowed.
- **Idle Claude tabs no longer report "working" forever.** Claude Code writes a
  `task-notification` prompt into the transcript when a tab is reopened with
  background work unaccounted for. Nobody answers it until that tab is focused,
  so the transcript ended on an unanswered prompt and Lumen read the session as
  a turn in progress — one working tab showed up as three or four, each holding
  a keyboard zone amber for hours. Only a human prompt or a tool result counts
  as an open turn now.
- **Dropdown menus are no longer clipped.** The Test menu is placed against the
  viewport instead of inside its row, so it escapes the card, the modal and the
  setup wizard's device list — all of which hide their own overflow — and flips
  above the button when it is near the bottom of the window. In setup the last
  device's menu used to open as an unreadable sliver.

### Added
- **A notch-style status tab.** A new built-in device, `notch`, hangs a small
  always-on-top tab from the top of the screen (under the menu bar and the
  hardware notch on a MacBook) on Windows, macOS and
  Linux. Like a keyboard it has zones, so the default "A tab on every zone"
  rule lights one soft bar per open Claude or Codex tab in its status colour,
  each bar filling up as that tab's context window does; hover it to see
  every live session's agent, name or folder, context use and state. It hides
  itself when no tab is open, and Settings → "Status tab at the top of the
  screen" turns it off. Inspired by codenotch, without the Mac-only part.
- **Your Claude limits, without signing in again.** Lumen reads the login
  Claude Code already holds (the macOS keychain, or `~/.claude/.credentials.json`)
  and asks the same endpoint Claude Code's `/usage` does, every five minutes,
  for the 5-hour and 7-day windows. The token is used read-only — Lumen never
  refreshes it, so it cannot log Claude Code out; an expired one just means no
  numbers until Claude Code signs in again. The folded status tab shows the
  5-hour figure, the hover panel both meters with their reset countdowns, the
  Integrations page the same, and a `claude.usage` event fires when they move
  so a rule can warn you at 90 %.
- **Context-window use per session.** The hook reads the last assistant turn
  of the Claude Code transcript and records how many tokens the session holds
  against its window (200k, or 1M for `[1m]` models). It travels with the
  `agents.sessions` event, so devices and the dashboard can show it.
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
[0.7.2]: https://github.com/Brxerq/lumen/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/Brxerq/lumen/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/Brxerq/lumen/compare/v0.6.3...v0.7.0
[0.6.3]: https://github.com/Brxerq/lumen/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/Brxerq/lumen/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/Brxerq/lumen/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Brxerq/lumen/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Brxerq/lumen/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Brxerq/lumen/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Brxerq/lumen/releases/tag/v0.3.1
