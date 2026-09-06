# Lumen — logo & icon kit

## Files

- `svg/lumen-logo.svg` — primary logo: dark capsule plus lowercase wordmark, with the pale violet/cyan light fields.
- `svg/lumen-icon.svg` — the standalone two-field icon.
- `png/` — transparent PNG exports of the logo and icon (primary, white-on-dark, monochrome black/white, equal-gray proof).
- `icons/` — square PNGs at 16, 32, 48, 64, 128 and 256 px, plus multi-resolution `.ico` files.
- `runtime/` — the icon as a template whose two fields are recoloured at runtime (see below).
- `palette.json` — the colors below in machine-readable form.
- `FONT_DETAILS.md` — lettering, companion typeface and color notes.

The SVGs are real paths with native gradients: no embedded bitmaps, no live text, so no font has to be installed to render the logo. Backgrounds are transparent. The lockup has a 976 × 342 viewBox, the icon 320 × 320.

Monochrome variants use transparent cutouts for the two fields, so they are genuinely one color rather than a grayscale gradient.

## Runtime icon

The tray icon is drawn live from device state, not from the static artwork. Use `runtime/lumen-runtime-light.svg` on light backgrounds (dark shell) and `runtime/lumen-runtime-dark.svg` on dark ones (white shell). Each exposes three named paths:

- `container` — the shell; follows the system theme, never the device state.
- `surface-top` — the first color device (keyboard).
- `surface-bottom` — the second (light bar).

Set the two `fill` values from the application's status palette. The gray in the shipped templates and their PNG previews is a readability placeholder, not a fifth state.

The violet/cyan gradients are static brand artwork — sidebar, favicon, executable icon. They never stand in for status color.

## Usage

Primary logo on white or very light backgrounds, the white variant on dark ones. At tray and favicon sizes use the icon alone, without the wordmark. Keep the aspect ratio.

## Colors

| Role | Value |
|---|---|
| Shell and wordmark | `#191C23` |
| Reversed, on dark | `#FFFFFF` |
| Violet field: center → middle → edge | `#F4F1FF` → `#EAE1FD` → `#DCC8F6` |
| Cyan field: center → middle → edge | `#EDFCFF` → `#D8F8FF` → `#B8EAF3` |
| Gray preview fill | `#A3A3AD` |

No font files are included.
