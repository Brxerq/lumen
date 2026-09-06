# Roadmap

Lumen's direction: *something happens on your computer → the physical environment around you reacts*, on any hardware, extensible by anyone. This is the honest state of things; pick anything unchecked and open an issue to claim it.

## Devices

- [x] ASUS Aura laptops over raw HID (keyboard zones + light bar) — verified on ROG Strix G513RM
- [x] Everything OpenRGB drives (Razer, Corsair, Logitech, SteelSeries, MSI, HyperX, Gigabyte, RAM, strips, fans)
- [x] Linux keyboard backlights (`/sys/class/leds`)
- [x] macOS keyboard backlight brightness (private CoreBrightness framework, via pyobjc)
- [x] Philips Hue (bridge discovery, pairing, state restore)
- [x] Govee LAN API
- [x] Screen edge glow (all platforms)
- [x] System notifications (Windows toast, macOS, `notify-send`)
- [x] System sounds
- [ ] **Community verification** of Hue and Govee on real hardware (written to spec, untested by maintainers)
- [ ] More ASUS Aura laptop models verified (other PIDs are detected but flagged unverified, with a one-click report link)
- [ ] Windows laptops with non-RGB backlights (vendor-specific: Dell, Lenovo, HP)
- [ ] Nanoleaf (mDNS + pairing)
- [ ] LIFX (LAN protocol)
- [ ] Elgato Key Light / Stream Deck
- [ ] Razer Chroma SDK direct (Windows) for machines without OpenRGB
- [ ] Logitech G HUB / Corsair iCUE SDK direct
- [ ] Home Assistant (one adapter, thousands of devices)
- [ ] Generic serial / WLED strips
- [ ] Per-key layouts for OpenRGB keyboards ("highlight specific keys")

## Integrations

- [x] Claude Code (hooks + transcript fallback)
- [x] Codex (hooks + rollout fallback)
- [x] GitHub Actions via `gh`
- [x] Terminal: `lumen exec`, `lumen emit`, `lumen timer`, shell snippet
- [x] Webhook
- [ ] Codex hook trust automation (Codex must approve hooks; currently manual on first start)
- [ ] Cursor / Windsurf / Copilot agents (as they expose hooks or logs)
- [ ] GitLab CI, CircleCI, Buildkite (webhook recipes)
- [ ] VS Code task events (extension)
- [ ] Browser downloads finished
- [ ] Docker / Kubernetes job completion
- [ ] Calendar / Slack mentions (opt-in, no cloud in the core)

## Core & UI

- [x] Rule engine with persistent base colors and transient effects
- [x] Effect degradation (color → brightness, wave → pulse)
- [x] Onboarding wizard, test bench, activity feed
- [x] Reduce-flashing accessibility mode
- [x] Server-sent events instead of 2 s polling
- [x] Rule ordering, duplicate, import/export
- [x] Agent tabs reordered by drag and drop, with pinned zones and names
- [x] Effect presets shared between rules ("Success", "Attention")
- [x] Per-device brightness and "quiet hours" (no flashes at night)
- [x] Light/dark theme toggle
- [ ] Localisation
- [x] In-app update from GitHub releases, checksum-verified
- [ ] Code signing, so Windows SmartScreen and macOS Gatekeeper stop warning
- [ ] Signed installers: Windows (winget), macOS (.app + brew), Linux (AppImage/Flatpak)
- [ ] Plugin discovery from PyPI in the dashboard

## Project

- [ ] Demo GIF and screenshots from several setups
- [ ] Publish to PyPI as `lumen`
- [ ] Website
