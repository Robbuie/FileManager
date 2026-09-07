"""Collect what a session needs to see about the context menu, into one file.

Written to be started from anywhere, including a bare `python` that has none of
this project's dependencies: it shells out to the virtual environment's
interpreter when there is one, so the only thing the interpreter running *this*
file needs is a standard library.

    python tools\\diagnose_menu.py                  the repository
    python tools\\diagnose_menu.py D:\\some\\folder   somewhere more interesting

It writes `Claude outputs\\menu-diagnosis.txt` and prints where it went. Nothing
here changes a file: every command below builds a menu, prints it and releases
it again.
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "Claude outputs")
OUT = os.path.join(OUT_DIR, "menu-diagnosis.txt")


def interpreter() -> str:
    """The one with PySide6 and pywin32 in it, which is rarely the one running."""
    venv = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    return venv if os.path.exists(venv) else sys.executable


def run(python: str, args: list[str]) -> str:
    try:
        done = subprocess.run([python, "-m", "app.io.harness", *args],
                              cwd=ROOT, capture_output=True, text=True, timeout=180)
    except Exception as exc:  # noqa: BLE001 - the report is the output
        return f"[the command did not run: {exc}]\n"
    return (done.stdout or "") + (done.stderr or "")


def main(argv: list[str]) -> int:
    target = argv[0] if argv else ROOT
    item = "" if argv else "README.md"
    python = interpreter()
    os.makedirs(OUT_DIR, exist_ok=True)

    sections: list[tuple[str, list[str]]] = []
    if item:
        sections.append((f"menu for one item: {target} {item}", ["menu", target, item]))
        sections.append((f"the same item, extended", ["menu", target, item, "--extended"]))
    else:
        # A folder was named, so the interesting item is whatever is in it. The
        # harness names the folder itself when it is given no names, which is
        # the background menu -- both are collected either way.
        try:
            names = [n for n in sorted(os.listdir(target))
                     if os.path.isfile(os.path.join(target, n))][:1]
        except OSError:
            names = []
        if names:
            sections.append((f"menu for one item: {target} {names[0]}",
                             ["menu", target, names[0]]))
            sections.append((f"the same item, extended",
                             ["menu", target, names[0], "--extended"]))
    sections.append((f"menu for the folder background: {target}", ["menu", target]))
    sections.append((f"overlays on the first 20 rows", ["overlays", target, "--rows", "20"]))

    with open(OUT, "w", encoding="utf-8") as report:
        report.write(f"interpreter: {python}\n")
        report.write(f"repository:  {ROOT}\n")
        report.write(f"target:      {target}\n\n")
        for title, args in sections:
            report.write(f"==== {title} ====\n")
            report.write(run(python, args))
            report.write("\n")
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
