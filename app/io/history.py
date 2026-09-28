"""What the queue has done, kept across restarts.

A job's own row in the queue says how it went, and "Clear finished" or closing
the window takes that away. This is the record that stays: one line of JSON per
finished job, beside the settings file, appended by the ops process as each job
ends and read once by the window at startup.

It lives in `io` because the ops process is the side that writes it, and the
write is a file call like any other. The read at startup is the settings
file's exception, made for the settings file's reason -- `%APPDATA%` is local
by definition and it happens before the window exists. Nothing here is ever
handed a path somebody typed, and nothing reads it while the window is up.

Bounded twice, because a log that grows forever is a log somebody eventually
has to find and delete: the file is cut back to the newest `KEEP` entries once
it passes `TRIM_AT` bytes, and a job records its first `PROBLEMS` failures
rather than all 30,000 of a delete that went wrong.

A write that fails is dropped without a word. History is a convenience, and a
copy must never fail because its diary could not be written.
"""

from __future__ import annotations

import json
import os
from typing import Any

FILE_NAME = "history.jsonl"
KEEP = 500
TRIM_AT = 2 * 1024 * 1024
PROBLEMS = 50
SOURCES = 20


def default_path(config_path: str) -> str:
    """Beside the settings file, the way the hang log is."""
    return os.path.join(os.path.dirname(config_path), FILE_NAME)


def entry(kind: str, sources: tuple[str, ...], destination: str, *,
          started: float, seconds: float, payload: dict, message: str,
          problems: list[str]) -> dict[str, Any]:
    """One finished job as it is kept: plain values only, bounded lists."""
    return {
        "at": started,
        "seconds": round(seconds, 1),
        "kind": kind,
        "count": len(sources),
        "sources": list(sources[:SOURCES]),
        "destination": destination,
        "copied": int(payload.get("copied", 0) or 0),
        "skipped": int(payload.get("skipped", 0) or 0),
        "failed": int(payload.get("failed", 0) or 0),
        "bytes": int(payload.get("bytes", 0) or 0),
        "cancelled": bool(payload.get("cancelled")),
        "message": message,
        "problems": problems[:PROBLEMS],
    }


def record(path: str, item: dict[str, Any]) -> bool:
    """Append one entry. False, never an exception, when it could not be."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        if os.path.getsize(path) > TRIM_AT:
            _trim(path)
        return True
    except (OSError, ValueError, TypeError):
        return False


def load(path: str, limit: int = KEEP) -> list[dict[str, Any]]:
    """The newest `limit` entries, oldest first. A damaged line is skipped
    rather than costing the rest of the file."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _trim(path: str) -> None:
    """Keep the newest entries, written beside and renamed over, so a kill in
    the middle leaves the old file rather than half of one."""
    kept = load(path, KEEP)
    partial = path + ".part"
    with open(partial, "w", encoding="utf-8") as handle:
        for item in kept:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    os.replace(partial, path)


