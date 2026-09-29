"""Checks on git's marks in the listing (0.38)."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from app.io import gitstatus


def test_parse_marks_names_in_this_folder_only():
    output = b"\0".join([
        b"## main...origin/main [ahead 2, behind 1]",
        b" M CHANGELOG.md",
        b"A  app/core/new.py",
        b"?? notes.txt",
        b" M app/ui/window.py",
        b"R  tools/b.py", b"tools/a.py",
        b"?? build/",
        b"",
    ])
    top = gitstatus.parse(output, "/repo", "/repo")
    assert top["branch"] == "main" and top["ahead"] == 2 and top["behind"] == 1
    assert top["marks"] == {"changelog.md": "M", "app": "*", "notes.txt": "?",
                            "tools": "*", "build": "?"}
    inside = gitstatus.parse(output, "/repo", "/repo/app/core")
    assert inside["marks"] == {"new.py": "A"}


def test_a_folder_outside_any_repository(tmp_path):
    assert gitstatus.status(str(tmp_path), 5)["marks"] == {}


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_a_real_repository(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "kept.txt").write_text("a")
    git("add", "kept.txt")
    git("commit", "-q", "-m", "one")
    (tmp_path / "kept.txt").write_text("b")
    (tmp_path / "new.txt").write_text("c")
    os.mkdir(tmp_path / "sub")
    (tmp_path / "sub" / "deep.txt").write_text("d")
    answer = gitstatus.status(str(tmp_path / "sub"), 10)
    assert answer["untracked"] is True
    top = gitstatus.status(str(tmp_path), 10)
    assert top["marks"]["kept.txt"] == "M"
    assert top["marks"]["new.txt"] == "?"
    assert top["marks"]["sub"] == "?"


def test_marks_reach_the_model():
    pytest.importorskip("PySide6")
    from app.core.gitmarks import GitMarks
    from app.core.config import Config
    from app.core.listing import Column, ListingModel
    from app.io.protocol import Entry, Reply, Status

    class Bridge:
        def __init__(self):
            self.sent = []

        def submit(self, op, path, *, timeout, on_reply, args=None):
            self.sent.append(on_reply)
            return 1

    bridge = Bridge()
    marks = GitMarks(bridge, Config({}))
    marks._known["c:\\repo"] = (0.0, {"marks": {"a.py": "M"}})
    model = ListingModel()
    model.set_folder("C:\\Repo")
    model.set_git(marks)
    model.begin(has_parent=False)
    model.add([Entry("a.py", False, 1, 1.0, 0), Entry("b.py", False, 1, 1.0, 0)])
    model.finish()
    assert model.data(model.index(model.row_of("a.py"), int(Column.NAME)),
                      ListingModel.GitRole) == "M"
    assert model.data(model.index(model.row_of("b.py"), int(Column.NAME)),
                      ListingModel.GitRole) is None
