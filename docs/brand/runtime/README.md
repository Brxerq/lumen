# Runtime icon templates

Two variants of the icon whose light fields are recoloured by the application:

- `lumen-runtime-light.svg` — dark shell, for a light background.
- `lumen-runtime-dark.svg` — white shell, for a dark background.

Each contains three named paths: `container` (the shell, follows the system theme), `surface-top` (first color device — keyboard) and `surface-bottom` (second — light bar). Set the two surface `fill` values from the status palette before rendering; the shell stays neutral.

The gray in these files and in the PNG previews is a placeholder for reading the geometry at small sizes, not a status color.
