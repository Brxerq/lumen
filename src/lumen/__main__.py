"""Enable `python -m lumen` as an alias for the `lumen` console script."""

import sys

from lumen.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["run"]))
