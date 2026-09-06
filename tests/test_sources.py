"""Every source file compiles cleanly, warnings included.

This exists because of one line. A docstring explaining that an update must not
run out of `dist\\FileManager\\` contained a Windows path, in a string that was
not raw, and `\\F` is not an escape sequence -- so Python 3.14 printed a
SyntaxWarning in the middle of a PyInstaller build, which is where nobody is
reading carefully. It is a warning today and an error in a later Python.

This project writes Windows paths in its prose constantly, so the mistake is
one that will be made again. Compiling every file with warnings turned into
failures is three seconds and catches it at the point it is introduced.
"""

from __future__ import annotations

import pathlib
import warnings

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The spec is Python that PyInstaller execs rather than imports, so it is
#: never compiled by anything else and is exactly the sort of file to rot.
EXTRA = ["packaging/filemanager.spec"]

SKIP = {".venv", "__pycache__", "build", "dist"}


def sources() -> list[pathlib.Path]:
    found = [path for path in ROOT.rglob("*.py")
             if not SKIP.intersection(path.relative_to(ROOT).parts)]
    return found + [ROOT / name for name in EXTRA]


@pytest.mark.parametrize("path", sources(), ids=lambda p: str(p.relative_to(ROOT)))
def test_compiles_without_warnings(path: pathlib.Path) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    problems = [f"{path.name}:{item.lineno}: {item.message}" for item in caught]
    assert not problems, "; ".join(problems)
