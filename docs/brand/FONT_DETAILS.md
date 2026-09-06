# Lettering & color

## Wordmark

The wordmark is a custom geometric lowercase drawing, not typeset text: five named SVG paths (`letter-l`, `letter-u`, `letter-m`, `letter-e`, `letter-n`) in near-black `#191C23`. Broad rounded arches, a curved-foot `l`, straight terminals, compact spacing. Because the letters are outlines, nothing has to be installed to display the logo, and the letterforms cannot be re-typed — use the artwork.

## Companion typeface

[Outfit](https://fonts.google.com/specimen/Outfit) (SIL Open Font License 1.1) pairs with the mark: 800 for display, 500 for interface labels, 400 for body. Start display tracking at `-0.02em` and adjust by eye. Outfit is a companion for new text, not the source of the wordmark — typing "lumen" in it will not reproduce the logo.

The dashboard ships no webfont at all; headings fall through a geometric stack (Outfit if installed, then Avenir Next, Nunito, Century Gothic, Segoe UI Variable Display) and text uses the platform UI face.

## Color

| Role | Value |
|---|---|
| Shell and wordmark | `#191C23` |
| Reversed shell and wordmark | `#FFFFFF` |
| Violet field: center → middle → edge | `#F4F1FF` → `#EAE1FD` → `#DCC8F6` |
| Cyan field: center → middle → edge | `#EDFCFF` → `#D8F8FF` → `#B8EAF3` |
| Gray preview fill | `#A3A3AD` |

The gradients are static brand artwork. Live surfaces — tray icon, dashboard device tiles — use the status palette instead.
