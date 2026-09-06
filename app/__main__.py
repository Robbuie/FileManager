"""Entry point. Deliberately empty until step 2 of the build order.

The io layer is built and verified headless first, through
`python -m app.io.harness`. There is no window to open yet.
"""

import sys


def main() -> int:
    print(
        "No UI yet. The io layer is built first; exercise it with:\n"
        "    python -m app.io.harness --help"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
