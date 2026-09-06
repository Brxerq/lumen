## What

<!-- One paragraph: what changes and why. Link the issue if there is one. -->

## How it was verified

- [ ] `pytest` passes
- [ ] Tried on real hardware / a real integration (which?): 
- [ ] Screenshot attached (UI changes)

## Checklist

- [ ] Adapter `discover()` never raises; write failures raise `OSError`
- [ ] No new dependency in the core (optional imports stay inside the adapter)
- [ ] README supported-devices table / `docs/ROADMAP.md` updated if relevant
- [ ] `CHANGELOG.md` has a line under *Unreleased*
