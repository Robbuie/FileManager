"""Run the tests and leave with pytest's verdict, and nothing after it.

The release workflow runs this instead of a bare `pytest`. On the Windows
runner the suite passes and the process then dies on the way out: v0.56.2's
run printed "pytest returned 0" and then an access violation, inside the
code Windows runs as each loaded library is unloaded. `os._exit` does not
avoid that -- on Windows it still ends in `ExitProcess`, and `ExitProcess`
still calls every DLL's detach -- so a passing run was reported as a crash.

`TerminateProcess` on this process ends it with the exit code given and runs
no detach code at all. Nothing is lost by that: the results are already
printed, and what is being skipped is library teardown that tests nothing.
A crash *during* the tests still fails the run, and faulthandler (switched on
here) still says where it was.

`pytest.main` reads pyproject.toml exactly as the bare command does, so
`pythonpath = ["."]` still applies. Extra arguments are passed to pytest.
"""

from __future__ import annotations

import faulthandler
import os
import sys


def _leave(code: int) -> None:
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
    os._exit(code)


def main() -> None:
    faulthandler.enable()
    import pytest

    code = int(pytest.main(["-q", *sys.argv[1:]]))
    print(f"pytest returned {code}", flush=True)
    _leave(code)


if __name__ == "__main__":
    main()
