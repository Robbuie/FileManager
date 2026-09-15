r"""Getting back onto a share that has gone.

Losing the connection to a server is the reason this application exists, and
until 0.20 the only thing offered for it was Ctrl+Shift+R, which restarted the
volume's worker and asked for the listing again. That is the right answer for a
worker that wedged and the wrong one for a session that has died: the worker
was never the problem, and the second listing fails exactly as the first one
did. Windows has to be told to attach to the share again first.

Two things these pin down, and both were wrong in the obvious first version.
The **share** is what is reconnected, not the folder the pane is standing in --
Windows attaches to `\\server\share` and knows nothing about `\Jobs\2026`
underneath it. And everything here works on the *resolved* path, so a pane
showing `S:\Jobs` is recognised as standing in the share it is mapped to; the
version that compared what the pane displayed left exactly that pane sitting on
its error message.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402

from app.core.config import Config  # noqa: E402
from app.core.pane import Pane  # noqa: E402
from app.core.transfers import TransferQueue  # noqa: E402
from app.io import paths  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

SHARE = r"\\dc01\projects"
MAPPED = r"S:\Jobs\2026"
LOCAL = "C:\\Jobs"


class FakeBridge:
    def __init__(self):
        self.retried = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        return 1

    def cancel(self, request_id):
        pass

    def forget(self, request_id):
        pass

    def retry(self, path):
        self.retried.append(path)


class FakeVolumes(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.drives = []

    def letter_for(self, path):
        return None

    def refresh(self, *, rescan=False):
        pass


class FakeNetwork(QObject):
    changed = Signal()
    reconnected = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.asked = []

    def refresh(self):
        pass

    def reconnect(self, path):
        self.asked.append(path)

    @property
    def locations(self):
        return []

    @property
    def saved(self):
        return []

    problem = ""


@pytest.fixture
def mapped(monkeypatch):
    """A session where S: is a mapping onto the share."""
    monkeypatch.setattr(paths, "mapping_for",
                        lambda letter, refresh=False:
                        SHARE if letter.upper().startswith("S") else None)
    monkeypatch.setattr(paths, "mapped_drives",
                        lambda *, refresh=False: {"S:": SHARE})


def build(tmp_path, left_path):
    config = Config({"left.path": left_path, "right.path": LOCAL},
                    str(tmp_path / "config.json"))
    bridge = FakeBridge()
    network = FakeNetwork()
    window = MainWindow(config, Pane(bridge, config, "left"),
                        Pane(bridge, config, "right"), FakeVolumes(),
                        TransferQueue(), None, None, None, None, network)
    return window, bridge, network


def test_a_pane_on_a_mapped_drive_reconnects_the_share(tmp_path, mapped):
    window, _bridge, network = build(tmp_path, MAPPED)
    window._reconnect_pane()
    assert network.asked == [SHARE], "the folder was reconnected, not the share"


def test_a_pane_on_a_unc_path_reconnects_the_share_above_it(tmp_path):
    window, _bridge, network = build(tmp_path, SHARE + r"\Jobs\2026")
    window._reconnect_pane()
    assert network.asked == [SHARE]


def test_a_local_pane_still_just_retries(tmp_path):
    """There is nothing to attach to, and restarting the worker is still the
    right answer for a disk that stopped answering."""
    window, bridge, network = build(tmp_path, LOCAL)
    window._reconnect_pane()
    assert network.asked == []
    assert bridge.retried == [LOCAL]


def test_a_reconnected_share_relists_the_pane_showing_a_letter(tmp_path, mapped):
    """The half that was wrong: the pane says `S:\\Jobs\\2026` and the share
    that came back is `\\\\dc01\\projects`."""
    window, bridge, _network = build(tmp_path, MAPPED)
    window._on_reconnected(SHARE, "")
    assert bridge.retried == [MAPPED]


def test_a_failed_reconnect_relists_nothing(tmp_path, mapped):
    window, bridge, _network = build(tmp_path, MAPPED)
    window._on_reconnected(SHARE, "the network name cannot be found")
    assert bridge.retried == []
