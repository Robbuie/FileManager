"""Archives as folders (0.41): the index, the worker's reads, the engine's
extraction, and every refusal that keeps them read-only."""

from __future__ import annotations

import io
import os
import queue
import tarfile
import zipfile

import pytest

from app.io import archive, worker
from app.io.protocol import Conflict, JobKind, Op, PreviewForm, Progress, Request, Status
from tests.test_ops import final, run_job, tree


@pytest.fixture
def zipped(tmp_path):
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr("main.L5X", "<RSLogix5000Content/>")
        handle.writestr("HMI/screen.mer", "m" * 10)
        handle.writestr("HMI/alarms/list.txt", "alarm")
        handle.writestr("../escape.txt", "no")
        handle.writestr("/absolute.txt", "no")
        handle.writestr("docs/bad:name.txt", "no")
        handle.writestr("empty/", "")
    return path


def ask(op, path, **args):
    outbox: queue.Queue = queue.Queue()
    worker._handle(Request(1, op, str(path), 5.0, args), outbox, queue.Queue(), set())  # noqa: SLF001
    replies = []
    while not outbox.empty():
        replies.append(outbox.get())
    return replies


# -------------------------------------------------------------------- names

@pytest.mark.parametrize("path, expected", [
    ("C:\\Jobs\\export.zip", ("C:\\Jobs\\export.zip", "")),
    ("C:\\Jobs\\export.zip\\HMI\\a.mer", ("C:\\Jobs\\export.zip", "HMI\\a.mer")),
    ("\\\\srv\\share\\x.tar.gz\\in", ("\\\\srv\\share\\x.tar.gz", "in")),
    ("C:\\Jobs\\folder", None),
    ("C:\\Jobs\\.zip", None),
])
def test_an_archive_is_found_in_a_path_by_its_name(path, expected):
    assert archive.split(path) == expected


def test_inside_means_past_the_name():
    assert not archive.inside("C:\\x\\a.zip")
    assert archive.inside("C:\\x\\a.zip\\")
    assert archive.inside("C:\\x\\a.zip\\b")


# --------------------------------------------------------------------- reads

def test_a_zip_lists_like_a_folder_and_leaves_out_what_escapes_it(zipped):
    replies = ask(Op.LIST, zipped)
    assert replies[-1].status is Status.OK
    names = sorted(entry.name for reply in replies for entry in reply.payload)
    # `docs` held only the unusable name, so it is not there either.
    assert names == ["HMI", "empty", "main.L5X"]
    assert replies[-1].message == "archive zip skipped=3"
    inside = ask(Op.LIST, f"{zipped}\\HMI")[-1].payload
    assert sorted((e.name, e.is_dir) for e in inside) == [("alarms", True), ("screen.mer", False)]


def test_a_folder_that_is_not_in_the_archive_is_gone(zipped):
    assert ask(Op.LIST, f"{zipped}\\nowhere")[-1].status is Status.GONE


def test_sizes_walks_and_chevrons_come_from_the_index(zipped):
    assert ask(Op.DIR_SIZE, f"{zipped}\\HMI")[-1].payload == {
        "bytes": 15, "files": 2, "folders": 1}
    walked = sorted(e.name for r in ask(Op.WALK, zipped) for e in (r.payload or []))
    assert walked == ["HMI\\alarms\\list.txt", "HMI\\screen.mer", "main.L5X"]
    assert ask(Op.FOLDERS, zipped)[-1].payload == {
        "names": ["empty", "HMI"], "more": False}


def test_a_member_previews_from_a_temp_copy(zipped, tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    reply = ask(Op.PREVIEW, f"{zipped}\\HMI\\alarms\\list.txt", box=256)[-1]
    assert reply.status is Status.OK
    assert reply.payload.form is PreviewForm.TEXT and reply.payload.text == "alarm"


def test_a_folder_named_like_an_archive_is_a_folder(tmp_path):
    folder = tmp_path / "backup.zip"
    folder.mkdir()
    (folder / "inside.txt").write_text("x")
    assert [e.name for e in ask(Op.LIST, folder)[-1].payload] == ["inside.txt"]
    assert ask(Op.MKDIR, f"{folder}\\new")[-1].status is Status.OK


def test_writes_inside_an_archive_are_refused(zipped):
    for op, path, args in ((Op.MKDIR, f"{zipped}\\new", {}),
                           (Op.RENAME, f"{zipped}\\main.L5X", {"name": "x.L5X"}),
                           (Op.DELETE, str(zipped), {"names": ["main.L5X"]})):
        reply = ask(op, path, **args)[-1]
        assert reply.status is Status.ERROR and "read-only" in reply.message


def test_a_tar_gz_lists_too(tmp_path):
    source = tmp_path / "f.txt"
    source.write_text("tar")
    path = tmp_path / "b.tar.gz"
    with tarfile.open(path, "w:gz") as handle:
        handle.add(source, arcname="in/f.txt")
    assert [(e.name, e.is_dir) for e in ask(Op.LIST, path)[-1].payload] == [("in", True)]
    assert [e.name for e in ask(Op.LIST, f"{path}\\in")[-1].payload] == ["f.txt"]


def test_a_damaged_archive_says_so(tmp_path):
    path = tmp_path / "broken.zip"
    path.write_bytes(b"PK not really")
    reply = ask(Op.LIST, path)[-1]
    assert reply.status is Status.ERROR and "not a readable zip" in reply.message


def test_a_changed_archive_is_read_again(tmp_path):
    path = tmp_path / "grow.zip"
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr("one.txt", "1")
    assert len(ask(Op.LIST, path)[-1].payload) == 1
    with zipfile.ZipFile(path, "a") as handle:
        handle.writestr("two.txt", "2")
    os.utime(path, (os.path.getmtime(path) + 5,) * 2)
    assert len(ask(Op.LIST, path)[-1].payload) == 2


# ---------------------------------------------------------- the engine

def test_copying_members_out_is_extraction(zipped, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    events = run_job(JobKind.COPY, [f"{zipped}\\HMI", f"{zipped}\\main.L5X"], str(out))
    assert final(events).payload["failed"] == 0
    assert tree(out) == {"HMI/screen.mer": b"m" * 10, "HMI/alarms/list.txt": b"alarm",
                         "main.L5X": b"<RSLogix5000Content/>"}


def test_everything_extracts_under_a_folder_of_its_own(zipped, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    events = run_job(JobKind.COPY, [f"{zipped}\\"], str(out), rename="export")
    assert final(events).payload["failed"] == 0
    assert set(tree(out / "export")) == {"HMI/screen.mer", "HMI/alarms/list.txt", "main.L5X"}
    assert (out / "export" / "empty").is_dir()


def test_copying_the_archive_file_still_copies_the_file(zipped, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    run_job(JobKind.COPY, [str(zipped)], str(out))
    assert (out / "export.zip").read_bytes() == zipped.read_bytes()


def test_an_extraction_onto_an_existing_file_follows_the_conflict_rule(zipped, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "main.L5X").write_text("mine")
    run_job(JobKind.COPY, [f"{zipped}\\main.L5X"], str(out), conflict=Conflict.SKIP)
    assert (out / "main.L5X").read_text() == "mine"


@pytest.mark.parametrize("kind", [JobKind.MOVE, JobKind.RECYCLE, JobKind.ERASE])
def test_moving_or_deleting_out_of_an_archive_is_refused(zipped, tmp_path, kind):
    out = tmp_path / "out"
    out.mkdir()
    before = zipped.read_bytes()
    events = run_job(kind, [f"{zipped}\\main.L5X"],
                     str(out) if kind is JobKind.MOVE else "")
    assert final(events).payload["failed"] >= 1
    assert any("read-only" in (event.message or "") for event in events)
    assert zipped.read_bytes() == before
    assert not (out / "main.L5X").exists()


def test_copying_into_an_archive_is_refused(zipped, tmp_path):
    source = tmp_path / "new.txt"
    source.write_text("x")
    before = zipped.read_bytes()
    events = run_job(JobKind.COPY, [str(source)], f"{zipped}\\HMI")
    assert final(events).payload["failed"] == 1
    assert zipped.read_bytes() == before


# ------------------------------------------------------------- the pane

def make_pane(tmp_path):
    from app.core.config import Config
    from app.core.pane import Pane
    from tests.test_columns import FakeBridge

    bridge = FakeBridge()
    return Pane(bridge, Config({}, path=str(tmp_path / "c.json")), "left"), bridge


def test_enter_on_an_archive_goes_into_it(tmp_path):
    from app.io.protocol import Entry, Reply

    pane, bridge = make_pane(tmp_path)
    pane.navigate(str(tmp_path))
    tab = pane.current
    pane._on_reply(tab, Reply(tab.request_id, Status.OK, payload=[  # noqa: SLF001
        Entry("export.zip", False, 10, 1.0, 0), Entry("notes.txt", False, 1, 1.0, 0)]))
    pane.activate(tab.model.row_of("export.zip"))
    assert pane.current.path.endswith("export.zip")
    assert bridge.sent[-1]["op"] is Op.LIST
    assert pane.in_archive


def test_with_browsing_off_an_archive_is_opened_by_windows(tmp_path):
    from app.io.protocol import Entry, Reply

    pane, bridge = make_pane(tmp_path)
    pane._config.set("archives.browse", False)  # noqa: SLF001
    pane.navigate(str(tmp_path))
    tab = pane.current
    pane._on_reply(tab, Reply(tab.request_id, Status.OK, payload=[  # noqa: SLF001
        Entry("export.zip", False, 10, 1.0, 0)]))
    pane.activate(tab.model.row_of("export.zip"))
    assert bridge.sent[-1]["op"] is Op.OPEN


def test_the_status_line_says_read_only_and_how_many_were_left_out(tmp_path):
    from app.io.protocol import Entry, Reply

    pane, _bridge = make_pane(tmp_path)
    pane.navigate(str(tmp_path / "export.zip"))
    tab = pane.current
    pane._on_reply(tab, Reply(tab.request_id, Status.OK,  # noqa: SLF001
                              payload=[Entry("a.txt", False, 1, 1.0, 0)],
                              message="archive zip skipped=2"))
    assert "read-only" in tab.status_text and "2 unusable names left out" in tab.status_text


def test_the_extract_name_drops_the_whole_suffix(tmp_path):
    from app.io.protocol import Entry, Reply

    pane, _bridge = make_pane(tmp_path)
    pane.navigate(str(tmp_path))
    tab = pane.current
    pane._on_reply(tab, Reply(tab.request_id, Status.OK, payload=[  # noqa: SLF001
        Entry("line3.tar.gz", False, 10, 1.0, 0), Entry("x.txt", False, 1, 1.0, 0)]))
    assert pane.archive_extract_name(tab.model.row_of("line3.tar.gz")) == "line3"
    assert pane.archive_extract_name(tab.model.row_of("x.txt")) is None
