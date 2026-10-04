"""0.51: .7z and .rar through the installed 7-Zip -- the table, extraction,
previews, and the ways a child program could otherwise hold a worker."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from app.io import archive
from app.io.protocol import JobKind, Op, PreviewForm, Status
from tests.test_archive import ask
from tests.test_ops import final, run_job

SEVEN = shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")
needs_7zip = pytest.mark.skipif(SEVEN is None, reason="no 7-Zip on this machine")


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(archive, "_cache", {})
    monkeypatch.setattr(archive, "_order", [])
    monkeypatch.setattr(archive, "_seven_found", [])


@pytest.fixture
def seven(tmp_path):
    source = tmp_path / "src"
    (source / "HMI" / "alarms").mkdir(parents=True)
    (source / "empty").mkdir()
    (source / "main.L5X").write_text("<RSLogix5000Content/>")
    (source / "HMI" / "screen.mer").write_bytes(b"m" * 10)
    (source / "HMI" / "alarms" / "list.txt").write_text("alarm")
    (source / "big.bin").write_bytes(os.urandom(300_000))
    path = tmp_path / "export.7z"
    subprocess.run([SEVEN, "a", "-bd", str(path), "./*"], cwd=source, check=True,
                   stdout=subprocess.DEVNULL)
    return path


def test_seven_suffixes_are_archives():
    assert archive.split("C:\\Jobs\\x.7z\\in\\a.txt") == ("C:\\Jobs\\x.7z", "in\\a.txt")
    assert archive.kind_of("C:\\x.RAR") == "7z"
    assert archive.kind_of("C:\\x.zip") == "zip"


def test_the_slt_listing_is_parsed_into_members():
    text = ("Path = HMI\nSize = 0\nModified = 2024-05-06 07:08:09.1234567\n"
            "Attributes = D\n\nPath = HMI\\a.txt\nSize = 12\n"
            "Modified = 2024-05-06 07:08:09\nAttributes = A\nCRC = \n\n"
            "Path = rarfolder\nFolder = +\nSize = 0\n")
    members = list(archive.parse_slt(text))
    assert [(m.name, m.size, m.is_dir) for m in members] == [
        ("HMI", 0, True), ("HMI\\a.txt", 12, False), ("rarfolder", 0, True)]
    assert members[0].mtime > 0


def test_without_7zip_the_listing_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "SEVEN_ZIP", "")
    path = tmp_path / "x.7z"
    path.write_bytes(b"7z\xbc\xaf\x27\x1c")
    reply = ask(Op.LIST, path)[-1]
    assert reply.status is Status.ERROR and "7-Zip is needed" in reply.message


@needs_7zip
def test_a_7z_lists_like_a_folder(seven):
    replies = ask(Op.LIST, seven)
    assert replies[-1].status is Status.OK
    names = sorted(e.name for r in replies for e in r.payload)
    assert names == ["HMI", "big.bin", "empty", "main.L5X"]
    inside = ask(Op.LIST, f"{seven}\\HMI")[-1].payload
    assert sorted((e.name, e.is_dir) for e in inside) == [("alarms", True), ("screen.mer", False)]
    assert ask(Op.DIR_SIZE, f"{seven}\\HMI")[-1].payload == {
        "bytes": 15, "files": 2, "folders": 1}


@needs_7zip
def test_a_7z_member_previews(seven, tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    reply = ask(Op.PREVIEW, f"{seven}\\HMI\\alarms\\list.txt", box=256)[-1]
    assert reply.payload.form is PreviewForm.TEXT and reply.payload.text == "alarm"


@needs_7zip
def test_copying_out_of_a_7z_is_extraction(seven, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    events = run_job(JobKind.COPY, [f"{seven}\\HMI", f"{seven}\\big.bin"], str(out))
    assert final(events).payload["failed"] == 0
    assert (out / "HMI" / "alarms" / "list.txt").read_text() == "alarm"
    assert (out / "big.bin").stat().st_size == 300_000
    assert not [n for n in os.listdir(out) if "part" in n]


@needs_7zip
def test_writes_into_a_7z_are_refused(seven):
    reply = ask(Op.MKDIR, f"{seven}\\new")[-1]
    assert reply.status is Status.ERROR and "read-only" in reply.message


@needs_7zip
def test_a_password_protected_7z_says_so_rather_than_waiting(tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("secret")
    hidden = tmp_path / "hidden.7z"
    subprocess.run([SEVEN, "a", "-bd", "-pS3cret", "-mhe=on", str(hidden), str(source)],
                   check=True, stdout=subprocess.DEVNULL)
    reply = ask(Op.LIST, hidden)[-1]
    assert reply.status is Status.ERROR and "password" in reply.message

    locked = tmp_path / "locked.7z"
    subprocess.run([SEVEN, "a", "-bd", "-pS3cret", str(locked), str(source)],
                   check=True, stdout=subprocess.DEVNULL)
    out = tmp_path / "out"
    out.mkdir()
    events = run_job(JobKind.COPY, [f"{locked}\\a.txt"], str(out))
    assert final(events).payload["failed"] == 1
    assert os.listdir(out) == []


@needs_7zip
def test_a_damaged_7z_says_so(tmp_path):
    path = tmp_path / "bad.7z"
    path.write_bytes(b"not an archive at all")
    reply = ask(Op.LIST, path)[-1]
    assert reply.status is Status.ERROR and "archive" in reply.message


def test_a_name_with_brackets_is_not_a_wildcard(tmp_path):
    """`-spd` is on every extraction, so 7-Zip takes the name literally."""
    calls = []

    class Fake:
        stdout = stderr = None
        returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def fake(arguments, **_extra):
        calls.append(arguments)
        return Fake()

    stream = None
    try:
        original = archive._seven  # noqa: SLF001
        archive._seven = fake  # noqa: SLF001
        stream = archive._SevenStream("C:\\x.7z", archive.Member("a[1].txt", 1, 0, False))  # noqa: SLF001
    finally:
        archive._seven = original  # noqa: SLF001
    assert "-spd" in calls[0] and "-p" in calls[0] and calls[0][-1] == "a[1].txt"
    del stream
