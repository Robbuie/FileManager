"""Headless CLI over the io layer. No Qt, no window, no UI import.

This is how step 1 of the build order gets verified, because the checks that
matter cannot be made from a GUI:

  * a 50,000-file listing over SMB, timed;
  * a connection yanked mid-listing;
  * a worker killed and restarted while requests are outstanding;
  * a reconnect after a mapped drive's session has died.

Run with `python -m app.io.harness --help`.
"""

import sys


def main(argv: list[str] | None = None) -> int:
    print("Not implemented yet — the io layer is the next piece of work.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
