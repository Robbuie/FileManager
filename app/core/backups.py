"""Settings backups (0.43): a copy of everything this application remembers.

The settings file holds more than preferences now -- favourites, workspaces,
labels and notes, the command table, per-folder sorts -- and it lives inside
the VM's profile. A backup is a dated copy of it beside the original, in
`%APPDATA%\\FileManager\\backups`, which is the settings file's own exception
to the rule that nothing outside a worker touches a file: the folder is local
by definition, the files are a few kilobytes, and nothing here is ever handed
a path somebody typed. To move settings to another machine, Help, Show
settings folder takes a pane there; the files are then copied like any
others, through the queue.

Restoring replaces every value at once, in memory, and saves it; the window
then asks to be restarted, because most settings are read when something is
built rather than watched. A restore first backs up what it is replacing, so
a restore is itself undoable.

Kept: the newest `KEPT` backups. Older ones are removed when a new one is
made, oldest first.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

FOLDER = "backups"
PREFIX = "settings-"
KEPT = 20


@dataclass(frozen=True)
class Backup:
    path: str
    when: float
    size: int

    @property
    def label(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.when))


def folder_for(config) -> str:
    return os.path.join(os.path.dirname(config.path), FOLDER)


def make(config, *, note: str = "") -> Backup:
    """Write the current settings to a new dated file. Raises OSError."""
    folder = folder_for(config)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{PREFIX}{stamp}{('-' + note) if note else ''}.json"
    path = os.path.join(folder, name)
    counter = 1
    while os.path.exists(path):
        counter += 1
        path = os.path.join(folder, f"{PREFIX}{stamp}-{counter}{('-' + note) if note else ''}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config.values(), handle, indent=2, sort_keys=True)
    _trim(folder)
    stat = os.stat(path)
    return Backup(path, stat.st_mtime, stat.st_size)


def listing(config) -> list[Backup]:
    """The backups there are, newest first. Empty when there are none."""
    folder = folder_for(config)
    try:
        entries = [entry for entry in os.scandir(folder)
                   if entry.is_file() and entry.name.startswith(PREFIX)
                   and entry.name.endswith(".json")]
    except OSError:
        return []
    found = []
    for entry in entries:
        try:
            stat = entry.stat()
        except OSError:
            continue
        found.append(Backup(entry.path, stat.st_mtime, stat.st_size))
    return sorted(found, key=lambda backup: backup.when, reverse=True)


def read(path: str) -> dict:
    """A backup's values. Raises ValueError for one that is not a settings file."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            values = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not read {os.path.basename(path)}: {exc}") from exc
    if not isinstance(values, dict):
        raise ValueError(f"{os.path.basename(path)} is not a settings file")
    return values


def restore(config, path: str) -> Backup:
    """Replace every setting with a backup's, keeping what was there first.

    Returns the safety backup made of the settings being replaced.
    """
    values = read(path)
    safety = make(config, note="before-restore")
    config.replace_all(values)
    config.save()
    return safety


def _trim(folder: str) -> None:
    try:
        entries = sorted((entry for entry in os.scandir(folder)
                          if entry.is_file() and entry.name.startswith(PREFIX)),
                         key=lambda entry: entry.stat().st_mtime)
    except OSError:
        return
    for entry in entries[:-KEPT]:
        try:
            os.remove(entry.path)
        except OSError:
            pass
