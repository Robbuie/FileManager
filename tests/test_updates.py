"""The updater, minus the network.

Everything here is about the two pieces that decide whether a machine ends up
running the right thing: what the manifest is allowed to say, and what happens
to a download that does not match it. Both run anywhere.

The last test is the one worth keeping honest -- it takes the manifest the
build script writes and feeds it to the parser the shipped application uses. A
change to either side that the other does not know about fails here rather than
on somebody's machine three weeks later.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import threading

import pytest

pytest.importorskip("PySide6")

from app.core import updates  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packaging"))


def manifest(**overrides):
    payload = {
        "version": "1.2.0",
        "notes": "https://example.invalid/notes",
        "installer": {
            "name": "FileManager-Setup-1.2.0.exe",
            "url": updates.DOWNLOAD_PREFIX + "v1.2.0/FileManager-Setup-1.2.0.exe",
            "size": 1024,
            "sha256": "a" * 64,
        },
    }
    payload["installer"].update(overrides.pop("installer", {}))
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------ versions


@pytest.mark.parametrize("candidate, current, expected", [
    ("0.7.0", "0.6.0", True),
    ("0.6.1", "0.6.0", True),
    ("1.0.0", "0.9.9", True),
    ("0.6.0", "0.6.0", False),
    ("0.5.9", "0.6.0", False),
    ("0.7", "0.6.9", True),
    ("0.6.0.1", "0.6.0", True),
    ("v0.7.0", "0.6.0", True),
    ("0.7.0-rc1", "0.6.0", True),
    # Anything unparseable is not newer. A feed that has gone wrong should be
    # inert, not a prompt to install something that does not exist.
    ("latest", "0.6.0", False),
    ("", "0.6.0", False),
    ("0.7.0", "", False),
    ("../../etc", "0.6.0", False),
])
def test_is_newer(candidate, current, expected):
    assert updates.is_newer(candidate, current) is expected


# ------------------------------------------------------------------ manifest


def test_parse_accepts_a_good_manifest():
    release = updates.parse(manifest())
    assert release.version == "1.2.0"
    assert release.name == "FileManager-Setup-1.2.0.exe"
    assert release.size == 1024


@pytest.mark.parametrize("payload", [
    "not an object",
    {},
    {"version": "1.2.0"},
    manifest(version=""),
    # A name that is a path, or is not an executable at all.
    manifest(installer={"name": "..\\\\evil.exe"}),
    manifest(installer={"name": "sub/dir.exe"}),
    manifest(installer={"name": "FileManager-Setup-1.2.0.zip"}),
    # A url that leaves the releases page, or does not match the name beside it.
    manifest(installer={"url": "https://example.invalid/FileManager-Setup-1.2.0.exe"}),
    manifest(installer={"url": "http://github.com/Robbuie/FileManager/releases/download/v1.2.0/FileManager-Setup-1.2.0.exe"}),
    manifest(installer={"url": updates.DOWNLOAD_PREFIX + "v1.2.0/other.exe"}),
    # Checksums and sizes that cannot be checked against anything.
    manifest(installer={"sha256": "short"}),
    manifest(installer={"sha256": "z" * 64}),
    manifest(installer={"size": 0}),
    manifest(installer={"size": "1024"}),
    manifest(installer={"size": updates.MAX_INSTALLER_BYTES + 1}),
])
def test_parse_refuses_everything_else(payload):
    with pytest.raises(updates.UpdateError):
        updates.parse(payload)


# ------------------------------------------------------------------ download


class _Response(io.BytesIO):
    """Just enough of what urlopen returns to stand in for it."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _release(body: bytes, *, size: int | None = None, sha256: str | None = None):
    return updates.Release(
        version="1.2.0",
        name="FileManager-Setup-1.2.0.exe",
        url=updates.DOWNLOAD_PREFIX + "v1.2.0/FileManager-Setup-1.2.0.exe",
        size=len(body) if size is None else size,
        sha256=hashlib.sha256(body).hexdigest() if sha256 is None else sha256,
    )


def _serve(monkeypatch, body: bytes):
    monkeypatch.setattr(updates, "_open", lambda url, timeout: _Response(body))


def test_download_writes_the_file_and_leaves_no_part(monkeypatch, tmp_path):
    body = b"installer bytes" * 400
    _serve(monkeypatch, body)
    path = updates.download(_release(body), folder=str(tmp_path))
    assert open(path, "rb").read() == body
    assert os.listdir(tmp_path) == ["FileManager-Setup-1.2.0.exe"]


def test_download_discards_a_wrong_checksum(monkeypatch, tmp_path):
    body = b"installer bytes" * 400
    _serve(monkeypatch, body)
    with pytest.raises(updates.UpdateError):
        updates.download(_release(body, sha256="b" * 64), folder=str(tmp_path))
    # Nothing at all: not the file, and not a .part that a later run might find
    # and mistake for a finished download.
    assert os.listdir(tmp_path) == []


def test_download_refuses_more_bytes_than_the_manifest_promised(monkeypatch, tmp_path):
    body = b"x" * 5000
    _serve(monkeypatch, body)
    with pytest.raises(updates.UpdateError):
        updates.download(_release(body, size=100), folder=str(tmp_path))
    assert os.listdir(tmp_path) == []


def test_download_stops_when_cancelled(monkeypatch, tmp_path):
    body = b"y" * (updates.CHUNK * 4)
    _serve(monkeypatch, body)
    cancel = threading.Event()

    def progress(done, total):
        cancel.set()

    with pytest.raises(updates.UpdateError):
        updates.download(_release(body), folder=str(tmp_path),
                         progress=progress, cancel=cancel)
    assert os.listdir(tmp_path) == []


def test_download_refuses_a_url_outside_the_releases_page(tmp_path):
    release = updates.Release(version="1.2.0", name="x.exe",
                              url="https://example.invalid/x.exe",
                              size=10, sha256="a" * 64)
    with pytest.raises(updates.UpdateError):
        updates.download(release, folder=str(tmp_path))


def test_reports_unavailable_outside_a_frozen_build():
    assert updates.unavailable()


# --------------------------------------------------------- the two ends agree


def test_the_build_script_writes_a_manifest_the_app_accepts(tmp_path, monkeypatch):
    """Producer and consumer, in one test.

    `packaging/build.py` writes latest.json and the shipped updater reads it.
    They are in different halves of the repository and nothing but this makes
    them agree.
    """
    build = pytest.importorskip("build")
    dist = tmp_path / "dist"
    dist.mkdir()
    installer = dist / "FileManager-Setup-9.9.9.exe"
    installer.write_bytes(b"setup" * 1000)
    monkeypatch.setattr(build, "DIST", str(dist))

    target = build.manifest("9.9.9", str(installer))
    build.verify("9.9.9", target)          # the build's own check

    with open(target, encoding="utf-8") as handle:
        release = updates.parse(json.load(handle))   # the application's check

    assert release.version == "9.9.9"
    assert release.name == installer.name
    assert release.size == installer.stat().st_size
    assert release.sha256 == hashlib.sha256(installer.read_bytes()).hexdigest()
    assert updates.is_newer(release.version, "0.7.0")


# ------------------------------------------------------- the controller runs


class _Settings:
    """A stand-in for `core.config.Config` with only the keys this needs."""

    def __init__(self, **values):
        self._values = {"updates.check_on_launch": True, "updates.skip_version": ""}
        self._values.update(values)

    def get(self, key):
        return self._values[key]

    def set(self, key, value):
        self._values[key] = value


def _spin(signals, *, timeout_ms=5000):
    """Run an event loop until one of `signals` fires, and say which did.

    The check runs in a thread, so there has to be a loop for its result to
    arrive on. Everything below is about that delivery rather than about the
    parsing, which is covered above without Qt.
    """
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

    QCoreApplication.instance() or QCoreApplication([])
    loop = QEventLoop()
    seen = {}
    for name, signal in signals.items():
        signal.connect(lambda *args, n=name: (seen.setdefault(n, args), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return seen


def test_a_newer_version_is_offered(monkeypatch):
    release = updates.parse(manifest(version="9.9.9"))
    monkeypatch.setattr(updates, "unavailable", lambda: None)
    monkeypatch.setattr(updates, "fetch", lambda: release)

    service = updates.Updates(_Settings(), "0.7.0")
    service.check(manual=False)
    seen = _spin({"available": service.available, "uptodate": service.uptodate,
                  "problem": service.problem})
    service.shutdown()
    assert "available" in seen
    assert seen["available"][0].version == "9.9.9"


def test_a_skipped_version_is_not_offered_again(monkeypatch):
    release = updates.parse(manifest(version="9.9.9"))
    monkeypatch.setattr(updates, "unavailable", lambda: None)
    monkeypatch.setattr(updates, "fetch", lambda: release)

    settings = _Settings(**{"updates.skip_version": "9.9.9"})
    service = updates.Updates(settings, "0.7.0")
    service.check(manual=False)
    seen = _spin({"available": service.available}, timeout_ms=1500)
    service.shutdown()
    assert seen == {}

    # Asked for by name, it is offered anyway: skipping is about not being
    # interrupted, not about never being told.
    service.check(manual=True)
    seen = _spin({"available": service.available})
    service.shutdown()
    assert "available" in seen


def test_a_failed_check_is_silent_unless_it_was_asked_for(monkeypatch):
    def boom():
        raise updates.UpdateError("Could not reach GitHub.")

    monkeypatch.setattr(updates, "unavailable", lambda: None)
    monkeypatch.setattr(updates, "fetch", boom)

    service = updates.Updates(_Settings(), "0.7.0")
    service.check(manual=False)
    assert _spin({"problem": service.problem}, timeout_ms=1500) == {}

    service.check(manual=True)
    seen = _spin({"problem": service.problem})
    service.shutdown()
    assert "problem" in seen


def test_an_older_release_says_nothing_and_reports_up_to_date_on_request(monkeypatch):
    release = updates.parse(manifest(version="0.1.0"))
    monkeypatch.setattr(updates, "unavailable", lambda: None)
    monkeypatch.setattr(updates, "fetch", lambda: release)

    service = updates.Updates(_Settings(), "0.7.0")
    service.check(manual=True)
    seen = _spin({"available": service.available, "uptodate": service.uptodate})
    service.shutdown()
    assert list(seen) == ["uptodate"]
