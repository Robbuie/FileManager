"""0.43: diagnostics, settings backups, attributes and dates, the login prompt."""

from __future__ import annotations

import os
import queue
import stat

import pytest

from app.core.config import DEFAULTS, Config
from app.io import worker
from app.io.protocol import Op, Request, Status


# --------------------------------------------------------------- diagnostics

def test_the_report_names_changed_settings_but_not_the_users_places():
    from app.core import diagnostics

    events = diagnostics.Events(kept=2)
    for text in ("one", "two", "three"):
        events.add("left", text)
    values = {"theme": "paper", "labels": {"C:\\Jobs\\secret.dwg": "red"},
              "favorites": ["C:\\a", "C:\\b"], "density": DEFAULTS["density"]}
    text = diagnostics.report(version="9.9", values=values, defaults=DEFAULTS,
                              events=events.lines())
    assert "File Manager 9.9" in text
    assert "theme = 'paper'" in text
    assert "labels = (1 item, not shown)" in text
    assert "favorites = (2 items, not shown)" in text
    assert "secret" not in text and "density" not in text
    assert "one" not in text and "three" in text        # only the newest are kept


# ------------------------------------------------------------------ backups

def test_back_up_list_and_restore(tmp_path):
    from app.core import backups

    config = Config({"theme": "paper"}, path=str(tmp_path / "config.json"))
    first = backups.make(config)
    config.set("theme", "blueprint")
    safety = backups.restore(config, first.path)
    assert config.get("theme") == "paper"
    assert backups.read(safety.path)["theme"] == "blueprint"   # what it replaced
    assert len(backups.listing(config)) == 2
    assert Config.load(config.path).get("theme") == "paper"


def test_a_backup_from_elsewhere_keeps_only_known_settings(tmp_path):
    config = Config({}, path=str(tmp_path / "config.json"))
    config.replace_all({"theme": "paper", "from.the.future": 1})
    assert config.values() == {"theme": "paper", "look.024": True}


def test_a_file_that_is_not_settings_is_refused(tmp_path):
    from app.core import backups

    bad = tmp_path / "settings-bad.json"
    bad.write_text("[1, 2]")
    with pytest.raises(ValueError):
        backups.read(str(bad))


def test_old_backups_are_trimmed(tmp_path, monkeypatch):
    from app.core import backups

    monkeypatch.setattr(backups, "KEPT", 3)
    config = Config({}, path=str(tmp_path / "config.json"))
    for _ in range(5):
        backups.make(config)
    assert len(backups.listing(config)) == 3


# --------------------------------------------------------- attributes and dates

def ask(path, **args):
    outbox: queue.Queue = queue.Queue()
    worker._handle(Request(1, Op.ATTRIBUTES, str(path), 5.0, args), outbox,  # noqa: SLF001
                   queue.Queue(), set())
    return outbox.get()


def test_dates_and_read_only_including_inside_folders(tmp_path):
    (tmp_path / "d" / "e").mkdir(parents=True)
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "d" / "e" / "f.txt").write_text("y")
    try:
        reply = ask(tmp_path, names=["a.txt", "d"], set=0x1, mtime=1_000_000_000.0,
                    recursive=True)
        assert reply.status is Status.OK and reply.payload["changed"] == 4
        assert os.path.getmtime(tmp_path / "d" / "e" / "f.txt") == 1_000_000_000.0
        assert not os.stat(tmp_path / "a.txt").st_mode & stat.S_IWUSR
        assert os.stat(tmp_path / "d").st_mode & stat.S_IWUSR       # folders keep theirs
    finally:
        for path in (tmp_path / "a.txt", tmp_path / "d" / "e" / "f.txt"):
            os.chmod(path, 0o644)


def test_a_name_that_leaves_the_folder_and_an_archive_are_refused(tmp_path):
    import zipfile

    reply = ask(tmp_path, names=["..\\x"], set=0x1)
    assert reply.payload["failed"] == {"..\\x": "not a name in this folder"}
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr("x.txt", "x")
    assert "read-only" in ask(path, names=["x.txt"], set=0x1).message


def test_the_dialog_reports_only_what_changed():
    from PySide6.QtCore import Qt

    from app.ui.attributes import AttributesDialog, initial_state, nothing_to_do

    assert initial_state([1, 1], 1) == Qt.Checked
    assert initial_state([0, 0], 1) == Qt.Unchecked
    assert initial_state([1, 0], 1) == Qt.PartiallyChecked
    dialog = AttributesDialog(None, names=["a", "b"], attributes=[0x21, 0x20],
                              mtime=1_000_000_000.0, has_folders=False)
    assert nothing_to_do(dialog.change())
    readonly = dialog._boxes[0][0]  # noqa: SLF001 - mixed at the start
    readonly.setCheckState(Qt.Checked)
    hidden = dialog._boxes[1][0]  # noqa: SLF001
    hidden.setCheckState(Qt.Checked)
    archive = dialog._boxes[3][0]  # noqa: SLF001
    archive.setCheckState(Qt.Unchecked)
    dialog._set_modified.setChecked(True)  # noqa: SLF001
    change = dialog.change()
    assert change["set"] == 0x1 | 0x2 and change["clear"] == 0x20
    assert change["mtime"] == 1_000_000_000.0 and not change["recursive"]


def test_the_pane_sends_the_change(tmp_path):
    from app.core.pane import Pane
    from tests.test_columns import FakeBridge

    bridge = FakeBridge()
    pane = Pane(bridge, Config({}, path=str(tmp_path / "c.json")), "left")
    pane.navigate(str(tmp_path))
    pane.set_attributes(["a.txt"], {"set": 1, "clear": 0, "recursive": False})
    assert bridge.sent[-1]["op"] is Op.ATTRIBUTES
    assert bridge.sent[-1]["args"]["names"] == ["a.txt"]


# ------------------------------------------------------------ the login prompt

@pytest.mark.parametrize("message, wanted", [
    ("The user name or password is incorrect.", True),
    ("The specified network password is not correct.", True),
    ("Access is denied.", True),
    ("The network name cannot be found.", False),
    ("", False),
])
def test_only_account_failures_get_a_prompt(message, wanted):
    from app.ui.credentials import wants_login

    assert wants_login(message) is wanted


def test_a_reconnect_with_an_account_carries_it_and_one_without_does_not(tmp_path):
    from app.core.network import Network
    from tests.test_columns import FakeBridge

    bridge = FakeBridge()
    network = Network(bridge, Config({}, path=str(tmp_path / "c.json")))
    network.reconnect("\\\\srv\\share")
    assert bridge.sent[-1]["args"] == {"remember": True}
    network.reconnect("\\\\srv\\share", user="PLANT\\rj", password="pw", save=True)
    assert bridge.sent[-1]["args"] == {"remember": True, "user": "PLANT\\rj",
                                       "password": "pw", "save": True}


def test_the_prompt_needs_a_user():
    from app.ui.credentials import LoginDialog

    dialog = LoginDialog(None, share="\\\\srv\\share", reason="Access is denied.")
    assert not dialog._ok.isEnabled()  # noqa: SLF001
    dialog._user.setText("rj")  # noqa: SLF001
    dialog._password.setText("pw")  # noqa: SLF001
    assert dialog._ok.isEnabled() and dialog.answer() == ("rj", "pw", False)  # noqa: SLF001
