"""Checks on the navigation rail, its capacity meters, and its groups.

Three things here are worth pinning, and each of them is a rule that would be
quiet if it broke.

**The rail must not put a floor under the window.** It is in the same splitter
as the panes, and a layout writes its own minimum onto its widget's
`minimumSize` *property* -- a property that beats every size hint an override
can return. That is how the favorites bar once made a window asked for 820
pixels come back 1596 wide. So the first tests here drag the rail narrow and
check both that it went and that a long name came back *elided* rather than
clipped: clipped and elided look nothing alike, and only one of them says
there is more.

**Capacity is never measured by itself except on a local fixed disk.** A
`disk_usage` on a mapped drive whose server has gone is the 30-45 second block
this whole application exists to avoid, and "the rail opened" is not somebody
asking. The test is on which letters `measure_local` sends anything about.

**A group is made by putting something in it.** There is no list of groups, so
there is nothing to fall out of step with the entries -- and a favourite saved
before 0.12 has no group at all, which has to keep reading back the way it was
written.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QHBoxLayout, QWidget  # noqa: E402

from app.core.capacity import AUTOMATIC, Capacity, Usage  # noqa: E402
from app.core.config import Config  # noqa: E402
from app.core.favorites import UNGROUPED, Favorites  # noqa: E402
from app.core.places import places  # noqa: E402
from app.io.protocol import Op, Reply, Status  # noqa: E402
from app.theme import sheet  # noqa: E402
from app.ui.rail import DRIVES, PLACES, DriveRow, NavigationRail  # noqa: E402

LONG = "A very long favourite name that will not fit in a rail"

DRIVE_LIST = [
    {"letter": "C:", "type": "fixed", "unc": None},
    {"letter": "D:", "type": "fixed", "unc": None},
    {"letter": "E:", "type": "cdrom", "unc": None},
    {"letter": "S:", "type": "remote", "unc": "\\\\vault\\projects"},
]


class FakeBridge:
    """Records what was submitted and hands back the reply the test wants."""

    def __init__(self):
        self.sent = []
        self._handlers = {}

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append((op, path))
        request_id = len(self.sent)
        self._handlers[request_id] = on_reply
        return request_id

    def answer(self, request_id, payload, status=Status.OK):
        handler = self._handlers.pop(request_id, None)
        if handler is not None:
            handler(Reply(request_id, status, payload=payload))

    def cancel(self, request_id):
        pass

    def forget(self, request_id):
        self._handlers.pop(request_id, None)


class FakeVolumes(QObject):
    changed = Signal()

    def __init__(self, drives=None):
        super().__init__()
        self.drives = list(drives if drives is not None else DRIVE_LIST)

    def refresh(self, *, rescan=False):
        pass


@pytest.fixture
def config(tmp_path):
    return Config({}, str(tmp_path / "config.json"))


def build(config, favorites=(), drives=None, network=None):
    config.set("favorites", list(favorites))
    bridge = FakeBridge()
    capacity = Capacity(bridge, config)
    rail = NavigationRail(Favorites(config), FakeVolumes(drives), capacity,
                          config, network)
    rail.apply_tokens(sheet.tokens())
    rail.set_places(places())
    # A host, because a widget with no parent is never given a width and the
    # elision tests are about what happens when it is.
    host = QWidget()
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(rail)
    host.resize(240, 600)
    host.show()
    return rail, host, bridge, capacity


# ----------------------------------------------------------------- the width


def test_the_rail_asks_the_window_for_no_width_at_all(config):
    """The favorites bar's bug, in the widget most able to repeat it: a rail
    is a column of names and one of them is always long."""
    rail, host, _bridge, _capacity = build(
        config, [{"name": LONG, "path": "C:\\x"}])
    assert rail.minimumSize().width() == 0
    assert rail.minimumSizeHint().width() <= 96
    host.hide()


def test_a_long_name_is_elided_rather_than_clipped(config):
    """Clipped and elided look nothing alike, and only one of them says there
    is more. Checked at two widths, because a pass that runs before the scroll
    area has been laid out measures against the wrong number and looks right
    at exactly one size."""
    rail, host, _bridge, _capacity = build(
        config, [{"name": LONG, "path": "C:\\x"}])
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    wide = _text_of(rail, LONG)
    assert wide != LONG and "\u2026" in wide

    host.resize(130, 600)
    QApplication.processEvents()
    narrow = _text_of(rail, LONG)
    assert "\u2026" in narrow
    assert len(narrow) < len(wide)
    host.hide()


def test_the_host_can_still_be_dragged_narrow(config):
    rail, host, _bridge, _capacity = build(
        config, [{"name": LONG, "path": "C:\\x"}])
    from PySide6.QtWidgets import QApplication

    host.resize(110, 600)
    QApplication.processEvents()
    assert host.width() == 110
    host.hide()


def _text_of(rail, full: str) -> str:
    for button, label in rail._elidable:  # noqa: SLF001 - the point of the test
        if label == full:
            return button.text()
    raise AssertionError(f"no row for {full!r}")


# ---------------------------------------------------------------- the drives


def test_only_local_fixed_drives_are_measured_without_being_asked(config):
    """The one rule that keeps a rail from reintroducing the startup probe.
    A remote letter answering slowly is the failure this application is about,
    and opening a rail is not somebody asking about it."""
    _rail, host, bridge, capacity = build(config)
    capacity.measure_local(DRIVE_LIST)
    asked = {path for op, path in bridge.sent if op is Op.FREE_SPACE}
    assert asked == {"C:\\", "D:\\"}
    assert "S:\\" not in asked and "E:\\" not in asked
    host.hide()


def test_a_remote_drive_is_measured_when_it_is_asked_for(config):
    _rail, host, bridge, capacity = build(config)
    capacity.measure("S:")
    assert (Op.FREE_SPACE, "S:\\") in bridge.sent
    host.hide()


def test_asking_twice_while_one_is_in_flight_is_one_question(config):
    _rail, host, bridge, capacity = build(config)
    capacity.measure("C:")
    capacity.measure("C:")
    assert len([p for op, p in bridge.sent if p == "C:\\"]) == 1
    host.hide()


def test_a_drive_that_failed_is_not_asked_again_on_every_redraw(config):
    """A dead share is exactly the drive that costs the deadline to ask, so a
    failure is remembered as a failure rather than dropped."""
    _rail, host, bridge, capacity = build(config)
    capacity.measure("S:")
    bridge.answer(bridge.sent.index((Op.FREE_SPACE, "S:\\")) + 1, None,
                  status=Status.GONE)
    assert capacity.measured("S:")
    capacity.measure_local([{"letter": "S:", "type": "fixed", "unc": None}])
    assert len([p for _op, p in bridge.sent if p == "S:\\"]) == 1
    host.hide()


def test_the_meter_is_what_is_used_not_what_is_free():
    """A meter that filled up as a disk emptied would be read backwards by
    everybody, once, and never trusted again."""
    usage = Usage(total=1000, used=900, free=100)
    assert usage.share == pytest.approx(0.9)


def test_a_volume_that_answered_with_no_size_draws_empty_not_full():
    assert Usage(total=0, used=0, free=0).share == 0.0


def test_the_automatic_set_is_local_only():
    assert "remote" not in AUTOMATIC and "removable" not in AUTOMATIC
    assert "cdrom" not in AUTOMATIC and "fixed" in AUTOMATIC


def test_an_unmeasured_drive_row_draws_no_meter():
    """An empty bar and a bar nobody has asked for look identical, and only
    one of them is a fact."""
    row = DriveRow("S:", "\\\\vault\\projects", "remote", usage=None,
                   tokens=sheet.tokens())
    measured = DriveRow("C:", "", "fixed", usage=Usage(100, 50, 50),
                        tokens=sheet.tokens())
    assert row.sizeHint().height() < measured.sizeHint().height()
    assert "\\\\vault\\projects" in row.toolTip()
    assert "measure" in row.toolTip()


def test_a_drive_row_goes_to_the_root_with_its_separator():
    """`C:` on its own means the current directory on C:, which is not a
    place a pane can be."""
    assert DriveRow("C:", "", "fixed", tokens={}).path == "C:\\"


# --------------------------------------------------------------- the groups


def test_a_favourite_saved_before_groups_existed_reads_back_unchanged(config):
    config.set("favorites", [{"name": "Jobs", "path": "C:\\Jobs"}])
    favorites = Favorites(config)
    assert favorites.entries[0].group == ""
    favorites.add("Drawings", "C:\\Drawings")
    stored = config.get("favorites")
    assert stored[0] == {"name": "Jobs", "path": "C:\\Jobs"}
    assert "group" not in stored[1]


def test_a_group_is_written_only_when_there_is_one(config):
    favorites = Favorites(config)
    favorites.add("Survey", "C:\\Jobs\\Survey", "Current job")
    assert config.get("favorites")[0]["group"] == "Current job"
    favorites.set_group(0, "")
    assert "group" not in config.get("favorites")[0]


def test_renaming_a_favourite_does_not_move_it_out_of_its_group(config):
    favorites = Favorites(config)
    favorites.add("Survey", "C:\\Jobs\\Survey", "Current job")
    favorites.add("Site survey", "C:\\Jobs\\Survey")
    assert favorites.entries[0].name == "Site survey"
    assert favorites.entries[0].group == "Current job"


def test_groups_come_back_in_the_order_the_list_mentions_them(config):
    favorites = Favorites(config)
    favorites.add("B", "C:\\b", "Zulu")
    favorites.add("A", "C:\\a", "Alpha")
    favorites.add("C", "C:\\c", "Zulu")
    assert favorites.groups() == ["Zulu", "Alpha"]


def test_renaming_a_group_takes_everything_under_it(config):
    favorites = Favorites(config)
    favorites.add("A", "C:\\a", "Old")
    favorites.add("B", "C:\\b", "Old")
    favorites.rename_group("Old", "New")
    assert [e.group for e in favorites.entries] == ["New", "New"]


def test_every_favourite_gets_a_heading_even_with_no_groups(config):
    """The ungrouped ones are drawn under one heading rather than none, so a
    list nobody has grouped reads the same as it always did."""
    rail, host, _bridge, _capacity = build(
        config, [{"name": "Jobs", "path": "C:\\Jobs"}])
    assert UNGROUPED.upper() in _headings(rail)
    host.hide()


def test_the_sections_are_places_drives_then_the_groups(config):
    rail, host, _bridge, _capacity = build(
        config, [{"name": "Survey", "path": "C:\\s", "group": "Current job"},
                 {"name": "Jobs", "path": "C:\\Jobs"}])
    assert _headings(rail) == [PLACES.upper(), DRIVES.upper(),
                               "CURRENT JOB", UNGROUPED.upper()]
    host.hide()


def test_a_folded_section_draws_no_rows_and_is_remembered(config):
    rail, host, _bridge, _capacity = build(
        config, [{"name": "Jobs", "path": "C:\\Jobs"}])
    assert any(label == "Jobs" for _b, label in rail._elidable)  # noqa: SLF001
    rail._toggle(UNGROUPED)  # noqa: SLF001
    assert not any(label == "Jobs" for _b, label in rail._elidable)  # noqa: SLF001
    assert config.get("rail.collapsed") == [UNGROUPED]
    host.hide()


def _headings(rail) -> list[str]:
    from PySide6.QtWidgets import QPushButton

    out = []
    for index in range(rail._column.count()):  # noqa: SLF001
        widget = rail._column.itemAt(index).widget()  # noqa: SLF001
        if isinstance(widget, QPushButton) and widget.property("role") == "railhead":
            out.append(widget.text().split("  ", 1)[-1].strip())
    # Upper-cased, because the headings are drawn that way and the case is a
    # presentation choice -- this test is about the order.
    return [name.upper() for name in out]


# ---------------------------------------------------------------- the places


def test_the_places_are_built_without_touching_a_disk():
    """No existence check anywhere: five of those at startup is the probe this
    application refuses. A place that is not there fails when it is clicked."""
    found = places()
    assert [p.label for p in found[1:]] == \
        ["Desktop", "Documents", "Downloads", "Pictures"]
    assert all(p.path for p in found)


def test_the_profile_is_the_first_place():
    """And the four under it are named by their own leaf, which is what makes
    a redirected Documents keep its label and get its real target."""
    found = places()
    assert found
    for place in found[1:]:
        assert place.path.rstrip("\\").endswith(place.label)


# ------------------------------------------------------- network locations
#
# Reported from the window on 15 September, from inside a Hyper-V VM: the
# host's C: drive is shared into the guest and Double Commander lists it, while
# this application had nowhere to put it. The cause was narrow and complete --
# the rail's drive list comes from `GetLogicalDrives`, which reports *letters*,
# and a Hyper-V or Remote Desktop redirected share has none. It is
# `\\tsclient\C` and nothing else, so no amount of drive enumeration would ever
# have found it.
#
# The path layer was never the problem and there is a test below that says so,
# because it is the thing a reasonable person would suspect first.

def test_a_redirected_share_is_a_perfectly_ordinary_unc_path() -> None:
    """Which is why the fix is discovery rather than path handling. `tsclient`
    is a pseudo-server and is keyed like any other, so it gets its own worker
    and a host connection that dies cannot take the rest of the window down.
    """
    from app.io import paths

    target = r"\\tsclient\C"
    assert paths.is_unc(target)
    assert paths.split_unc(target) == ("tsclient", "C", "")
    assert paths.volume_key(target) == r"\\tsclient"
    assert paths.parent(target) is None            # the share root is the top


def test_a_connection_with_no_letter_is_labelled_by_its_share() -> None:
    from app.core.network import Location

    assert Location(r"\\tsclient\C").label == "C on tsclient"


def test_a_connection_with_a_letter_is_labelled_by_it() -> None:
    """Because that is what the user types, and what they are looking for."""
    from app.core.network import Location

    assert Location(r"\\server\jobs", local="S:").label == "S:"


def test_a_location_always_opens_the_unc_even_when_it_has_a_letter() -> None:
    """The letter is a name this session happens to have; the UNC is what the
    thing is, and it is what survives the letter's session dying.
    """
    from app.core.network import Location

    assert Location(r"\\server\jobs", local="S:").path == r"\\server\jobs"


@pytest.mark.parametrize("typed, expected", [
    (r"\\tsclient\C", r"\\tsclient\C"),
    ("  \\\\tsclient\\C\\  ", r"\\tsclient\C"),
    (r"//tsclient/C", r"\\tsclient\C"),
    ("C:\\Jobs", ""),
    ("not a path", ""),
    ("", ""),
])
def test_what_counts_as_a_network_location(typed: str, expected: str) -> None:
    """A string test and nothing more. Whether the share exists is a question
    for the worker that lists it; asking here would be a blocking call in a
    dialog, which is the failure this application is built around.
    """
    from app.core.network import clean

    assert clean(typed) == expected


def test_saved_locations_survive_and_do_not_duplicate() -> None:
    from app.core.config import Config
    from app.core.network import Network

    class Bridge:
        def submit(self, *args, **kwargs):
            return 1

        def forget(self, request_id):
            pass

    config = Config({}, path=os.devnull)
    network = Network(Bridge(), config)
    assert network.add(r"\\tsclient\C") == r"\\tsclient\C"
    assert network.add(r"\\TSCLIENT\c") == r"\\TSCLIENT\c"
    assert [item.remote for item in network.saved] == [r"\\tsclient\C"]
    network.remove(r"\\tsclient\C")
    assert network.saved == []


def test_a_saved_location_that_is_also_connected_is_one_row() -> None:
    """And it is the connected one, because that is the row carrying the
    letter and the live state.
    """
    from app.core.config import Config
    from app.core.network import Location, Network

    class Bridge:
        def submit(self, *args, **kwargs):
            return 1

        def forget(self, request_id):
            pass

    config = Config({}, path=os.devnull)
    network = Network(Bridge(), config)
    network.add(r"\\server\jobs")
    network._connections = [Location(r"\\SERVER\JOBS", local="S:")]
    rows = network.locations
    assert len(rows) == 1
    assert rows[0].local == "S:"


def test_a_location_that_is_not_a_unc_path_is_refused_rather_than_saved() -> None:
    from app.core.config import Config
    from app.core.network import Network

    class Bridge:
        def submit(self, *args, **kwargs):
            return 1

        def forget(self, request_id):
            pass

    network = Network(Bridge(), Config({}, path=os.devnull))
    assert network.add("C:\\Jobs") == ""
    assert network.saved == []


def _has_heading(labels, title: str) -> bool:
    """A heading is drawn with a fold chevron and in capitals, so it is looked
    for rather than compared."""
    return any(title.upper() in label.upper() for label in labels)


def _labels(rail) -> list[str]:
    """Every row's text, headings included -- **before** it is cut to fit.

    `rail._elidable` holds each row beside its full text, and the full text is
    what to assert against: the drawn text has been through `elidedText` with
    `ElideMiddle`, so on a narrower rail, a different font or a scaled display
    "Archive on fileserver" is "Archive on f...server" and an exact comparison
    fails for a reason that has nothing to do with the feature.

    Found by the failure: this passed in the container at 240 px and failed on
    the user's machine on the first run of 0.19.
    """
    from PySide6.QtWidgets import QPushButton

    full = {id(button): text for button, text in rail._elidable}
    return [full.get(id(child), child.text())
            for child in rail.findChildren(QPushButton)]


def test_the_network_section_lists_only_what_drives_cannot_show(config) -> None:
    """A connection with a letter is already a row under Drives with its own
    meter and its own reconnect. Listing it twice invites somebody to wonder
    which is the real one -- and the row this section exists for is the one
    with no letter, which Drives can never show.
    """
    from app.core.network import Location, Network
    from app.ui.rail import NETWORK

    network = Network(FakeBridge(), config)
    network._connections = [
        Location(r"\\tsclient\C"),
        Location(r"\\server\jobs", local="S:"),
    ]
    rail, host, _bridge, _capacity = build(config, network=network)
    labels = _labels(rail)
    assert _has_heading(labels, NETWORK)
    assert "C on tsclient" in labels
    assert "S:" not in labels
    host.hide()


def test_a_saved_location_is_listed_even_though_nothing_is_connected(config) -> None:
    """The reason saved locations exist at all: a share nobody is attached to
    is in no table, and one that vanishes from the sidebar the moment a server
    reboots is one you cannot click to get it back.
    """
    from app.core.network import Network

    network = Network(FakeBridge(), config)
    network.add(r"\\fileserver\Archive")
    rail, host, _bridge, _capacity = build(config, network=network)
    labels = _labels(rail)
    assert "Archive on fileserver" in labels
    host.hide()


def test_a_rail_built_without_a_network_draws_no_network_section(config) -> None:
    """What a preview render and a test get -- the shape of the real thing
    rather than an empty heading that is a different shape from it.
    """
    from app.ui.rail import NETWORK

    rail, host, _bridge, _capacity = build(config)
    labels = _labels(rail)
    assert not _has_heading(labels, NETWORK)
    host.hide()


def test_an_empty_network_list_says_why_when_there_is_a_why(config) -> None:
    """"Nothing here" and "the question could not be asked" look identical and
    are not the same thing. The first run of 0.19 on the user's machine came
    back with no connections and no way to tell which it was, which is the
    reason `connections()` reports a reason at all.
    """
    from app.core.network import Network

    network = Network(FakeBridge(), config)
    network.problem = "the connection list would not open: Access denied (5)"
    rail, host, _bridge, _capacity = build(config, network=network)
    assert any("Access denied" in label for label in _labels(rail))
    host.hide()


def test_an_empty_network_list_with_no_problem_says_so_plainly(config) -> None:
    from app.core.network import Network

    rail, host, _bridge, _capacity = build(
        config, network=Network(FakeBridge(), config))
    assert any("Nothing without a drive letter" in label
               for label in _labels(rail))
    host.hide()


def test_a_failed_enumeration_is_not_reported_as_an_empty_machine() -> None:
    """The same distinction one layer down, where it starts."""
    from app.core.config import Config
    from app.core.network import Network
    from app.io.protocol import Reply, Status

    network = Network(FakeBridge(), Config({}, path=os.devnull))
    network._on_reply(Reply(1, Status.OK, payload={
        "connections": [], "problem": "the connection list would not open"}))
    assert network.problem == "the connection list would not open"
    assert network.locations == []


def test_moving_the_current_mark_keeps_the_rows_that_are_there(config):
    """A click on a row navigates, and navigating moves the mark. Rebuilding
    the column for that destroyed the row under the pointer and left a moment
    with nothing painted, which on glass is a hole to the window behind.
    """
    from PySide6.QtWidgets import QPushButton

    rail, host, _bridge, _capacity = build(
        config, [{"name": "Jobs", "path": "C:\\Jobs"},
                 {"name": "Survey", "path": "C:\\Survey"}])
    rows = rail.findChildren(QPushButton)
    rail.set_current("C:\\Jobs")
    assert rail.findChildren(QPushButton) == rows
    marked = [row for row in rows if row.property("state") == "current"]
    assert [row.property("target") for row in marked] == ["C:\\Jobs"]

    rail.set_current("C:\\Survey\\")
    marked = [row for row in rows if row.property("state") == "current"]
    assert [row.property("target") for row in marked] == ["C:\\Survey"]
    host.hide()
