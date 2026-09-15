r"""What the io layer sends when a path is longer than Windows allows.

Windows' file calls stop at 260 characters unless the path carries the `\\?\`
prefix, and a folder tree on a share reaches that without anybody trying. What
makes it worth a release rather than a note is how it fails: `os.scandir`
answers a folder that is plainly there with "The system cannot find the path
specified", so the length arrives disguised as a bug in whichever feature
reached it first.

These run on Linux, which is the point: `paths.EXTENDED_PATHS` is a module flag
rather than an `os.name` test at each call site, so the rewrite can be turned on
and what the io layer *would* have sent read back without a Windows machine.
The rewrite itself is pure string work and is checked directly.

The second half is the rule that is easy to lose: **the shell does not take the
prefix**. A prefixed path handed to `SHFileOperation` or `SHGetFileInfo` fails
or comes back empty, so the delete that goes to the Recycle Bin gets the plain
path and only the file calls get the other one.
"""

from __future__ import annotations

import os
from queue import Empty

import pytest

from app.io import paths, worker
from app.io.protocol import Op, Request


@pytest.fixture
def extended(monkeypatch):
    """Windows, as far as `paths.api` is concerned."""
    monkeypatch.setattr(paths, "EXTENDED_PATHS", True)


@pytest.mark.parametrize("given, expected", [
    (r"C:\Jobs\2026", r"\\?\C:\Jobs\2026"),
    ("C:\\", "\\\\?\\C:\\"),
    (r"\\dc01\projects\Jobs", r"\\?\UNC\dc01\projects\Jobs"),
    (r"\\dc01\projects", r"\\?\UNC\dc01\projects"),
    # Already prefixed, so there is nothing to do -- which is what makes it
    # safe to pass a path that came back from a scan of one.
    (r"\\?\C:\Jobs", r"\\?\C:\Jobs"),
    (r"\\?\UNC\dc01\projects", r"\\?\UNC\dc01\projects"),
    # A device path is not a file path and is left alone.
    (r"\\.\PhysicalDrive0", r"\\.\PhysicalDrive0"),
    # The prefix turns normalization off, so anything not fully qualified is
    # returned as it stands: under the prefix it would simply not be found.
    ("notes.txt", "notes.txt"),
    (r"Jobs\2026", r"Jobs\2026"),
    ("", ""),
])
def test_the_extended_form(given, expected):
    assert paths.extended(given) == expected


def test_it_tidies_on_the_way():
    """The prefix means "do not work this out for me", so what goes under it
    has to be tidy already. `normalize` is what makes that true."""
    assert paths.extended("c:/Jobs//2026/") == r"\\?\C:\Jobs\2026"
    assert paths.extended("//dc01//projects//Jobs") == r"\\?\UNC\dc01\projects\Jobs"


def test_api_is_off_where_the_prefix_means_nothing(monkeypatch):
    monkeypatch.setattr(paths, "EXTENDED_PATHS", False)
    assert paths.api(r"C:\Jobs") == r"C:\Jobs"


def test_api_is_idempotent(extended):
    once = paths.api(r"\\dc01\projects\Jobs")
    assert paths.api(once) == once


# --------------------------------------------------------------------------
# What the calls actually receive.
# --------------------------------------------------------------------------


class Outbox:
    def __init__(self):
        self.replies = []

    def put(self, reply):
        self.replies.append(reply)


class Control:
    """An empty control queue, which is what a worker with nothing to cancel
    is looking at."""

    def get_nowait(self):
        raise Empty


def test_a_listing_asks_for_the_extended_path(extended, monkeypatch):
    seen = []

    class FakeScanner:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def __iter__(self):
            return iter(())

        def __next__(self):
            raise StopIteration

    def fake_scandir(path):
        seen.append(path)
        return FakeScanner()

    monkeypatch.setattr(os, "scandir", fake_scandir)
    request = Request(id=1, op=Op.LIST, path=r"\\dc01\projects\Jobs", timeout=5.0)
    worker._list(request, Outbox(), Control(), set())
    assert seen == [r"\\?\UNC\dc01\projects\Jobs"]


def test_a_new_folder_is_made_at_the_extended_path(extended, monkeypatch):
    seen = []
    monkeypatch.setattr(os, "mkdir", seen.append)
    request = Request(id=2, op=Op.MKDIR, path=r"C:\Jobs\2026\new", timeout=5.0)
    worker._mkdir(request, Outbox())
    assert seen == [r"\\?\C:\Jobs\2026\new"]


def test_a_rename_carries_it(extended, monkeypatch):
    """Only the source is asserted, and the reason is the lesson 0.17 learned:
    `_rename` builds its target with `os.path.join`, which is Windows-shaped
    where this runs for real and POSIX-shaped here. The source is the same
    string on both."""
    seen = []
    monkeypatch.setattr(os, "rename", lambda a, b: seen.append((a, b)))
    monkeypatch.setattr(os.path, "exists", lambda _p: False)
    request = Request(id=3, op=Op.RENAME, path=r"C:\Jobs\old.txt", timeout=5.0,
                      args={"name": "new.txt"})
    worker._rename(request, Outbox())
    assert seen and seen[0][0] == r"\\?\C:\Jobs\old.txt"


def test_the_recycle_bin_gets_the_plain_path(extended, monkeypatch):
    """The rule the rest of this file exists to protect. `SHFileOperation`
    refuses a prefixed path, and a delete that silently did nothing is the
    worst way to find that out."""
    seen = []

    def fake_delete(targets, *, permanent=False):
        seen.extend(targets)
        return len(targets), False, "", 0

    monkeypatch.setattr(worker, "win32shell", object())
    monkeypatch.setattr(worker, "shell_delete", fake_delete)
    request = Request(id=4, op=Op.DELETE, path=r"\\dc01\projects\Jobs",
                      timeout=5.0, args={"names": ["one.txt"]})
    worker._delete(request, Outbox())
    assert seen and not seen[0].startswith("\\\\?")
    assert seen[0].startswith(r"\\dc01\projects\Jobs")


def test_the_prefix_never_reaches_the_window(extended):
    r"""`display` is what the path bar draws, and a person cannot type
    `\\?\UNC\...` back into it."""
    assert not paths.display(r"\\dc01\projects\Jobs",
                             prefer_letter=False).startswith("\\\\?")
    assert not paths.resolve(r"\\dc01\projects\Jobs").startswith("\\\\?")
    assert not paths.volume_key(r"\\dc01\projects\Jobs").startswith("\\\\?")
