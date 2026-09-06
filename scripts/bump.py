#!/usr/bin/env python3
"""Set the release version everywhere it is written down.

    python scripts/bump.py 0.3.0

The package's __version__ is the source of truth (pyproject reads it through
hatchling); this also updates the Claude Code plugin manifest and opens a
section in the changelog, so a release cannot ship three different numbers.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "src" / "lumen" / "__init__.py"
PLUGIN = ROOT / "plugin" / ".claude-plugin" / "plugin.json"
CHANGELOG = ROOT / "CHANGELOG.md"


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not re.fullmatch(r"\d+\.\d+\.\d+", argv[0]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    version = argv[0]

    text = INIT.read_text(encoding="utf-8")
    new = re.sub(r'^__version__ = ".*"$', f'__version__ = "{version}"', text, count=1, flags=re.M)
    if new == text:
        print(f"no __version__ line in {INIT}", file=sys.stderr)
        return 1
    INIT.write_text(new, encoding="utf-8")

    plugin = json.loads(PLUGIN.read_text(encoding="utf-8"))
    plugin["version"] = version
    PLUGIN.write_text(json.dumps(plugin, indent=2) + "\n", encoding="utf-8")

    log = CHANGELOG.read_text(encoding="utf-8")
    today = dt.date.today().isoformat()
    if f"## [{version}]" not in log:
        CHANGELOG.write_text(log.replace("## [Unreleased]", f"## [Unreleased]\n\n## [{version}] — {today}", 1), encoding="utf-8")

    print(f"version {version}: {INIT.name}, {PLUGIN.name}, {CHANGELOG.name}")
    print(f"next: review the changelog, then `git tag v{version} && git push --tags`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
