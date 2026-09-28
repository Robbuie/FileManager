"""The job history: written by the ops process, bounded, and read back."""

from __future__ import annotations

import json
import queue

from app.core.transfers import describe_entry
from app.io import history
from app.io.ops import Runner
from app.io.protocol import Conflict, Job, JobKind, Progress
from tests.test_queue import Inbox, drain, kinds


def test_a_finished_job_is_written_and_carried_on_its_done(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.txt").write_text("alpha")
    (tmp_path / "out").mkdir()
    log = tmp_path / "history.jsonl"
    runner = Runner(Inbox(), queue.Queue(), history_path=str(log))
    job = Job(id=1, kind=JobKind.COPY, sources=(str(tmp_path / "src"),),
              destination=str(tmp_path / "out"), conflict=Conflict.SKIP)
    runner._run(job)
    done = kinds(drain(runner), Progress.DONE)[-1]
    kept = history.load(str(log))
    assert len(kept) == 1
    assert kept[0] == done.payload["history"]
    assert kept[0]["kind"] == "copy" and kept[0]["copied"] == 1
    assert kept[0]["destination"] == str(tmp_path / "out")


def test_failures_are_kept_but_only_the_first_few(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "PROBLEMS", 2)
    runner = Runner(Inbox(), queue.Queue(), history_path=str(tmp_path / "h.jsonl"))
    missing = [str(tmp_path / f"gone{i}") for i in range(5)]
    runner._run(Job(id=2, kind=JobKind.ERASE, sources=tuple(missing),
                    destination="", conflict=Conflict.SKIP))
    kept = history.load(str(tmp_path / "h.jsonl"))[0]
    assert kept["failed"] == 5
    assert len(kept["problems"]) == 2


def test_a_runner_with_no_path_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = Runner(Inbox(), queue.Queue())
    runner._run(Job(id=3, kind=JobKind.ERASE, sources=(str(tmp_path / "x"),),
                    destination="", conflict=Conflict.SKIP))
    assert list(tmp_path.iterdir()) == []


def test_the_file_is_cut_back_to_the_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "TRIM_AT", 400)
    monkeypatch.setattr(history, "KEEP", 3)
    log = str(tmp_path / "h.jsonl")
    for index in range(10):
        assert history.record(log, {"n": index, "pad": "x" * 60})
    kept = [item["n"] for item in history.load(log)]
    assert kept[-1] == 9 and len(kept) <= 5
    assert not (tmp_path / "h.jsonl.part").exists()


def test_a_damaged_line_costs_only_itself(tmp_path):
    log = tmp_path / "h.jsonl"
    log.write_text(json.dumps({"n": 1}) + "\n{half a line\n" + json.dumps({"n": 2}) + "\n")
    assert [item["n"] for item in history.load(str(log))] == [1, 2]


def test_a_missing_file_is_an_empty_history(tmp_path):
    assert history.load(str(tmp_path / "none.jsonl")) == []


def test_an_entry_reads_as_a_sentence():
    when, what, where, outcome = describe_entry({
        "at": 0, "kind": "copy", "count": 3, "sources": ["S:\\a", "S:\\b", "S:\\c"],
        "destination": "D:\\out", "copied": 2, "failed": 1, "seconds": 75,
    })
    assert what == "Copy 3 items"
    assert where == "D:\\out"
    assert outcome.startswith("1 failed, 2 copied")


def test_a_delete_names_the_folder_it_emptied():
    _when, what, where, outcome = describe_entry({
        "at": 0, "kind": "erase", "count": 1, "sources": ["S:\\jobs\\old.dwg"],
        "destination": "", "copied": 1, "bytes": 999,
    })
    assert what == "Erase old.dwg"
    assert where == "S:\\jobs"
    assert outcome == "1 removed"
