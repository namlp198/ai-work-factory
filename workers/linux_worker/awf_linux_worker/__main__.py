"""Linux worker entry point: `python -m awf_linux_worker` (same as `awf worker`)."""

from __future__ import annotations

import sys

from awf.cli import main

if __name__ == "__main__":
    sys.exit(main(["worker", *sys.argv[1:]]))
