"""The folder map (0.43): the tree, the layout, and the walk that feeds them."""

from __future__ import annotations

import pytest

from app.core import treemap
from app.io.protocol import Entry, Op, Reply, Status

ROWS = [("PLC\\main.L5X", 600), ("PLC\\old\\v1.L5X", 100), ("Drawings\\a.dwg", 250),
        ("notes.txt", 50)]


def test_folders_are_the_sums_of_what_is_in_them():
    root = treemap.build("Line3", ROWS)
    assert root.size == 1000 and root.files == 4
    plc = root.children["plc"]
    assert plc.is_dir and plc.size == 700 and plc.files == 2
    assert plc.family == "logix" and root.children["drawings"].family == "cad"
    assert [child.name for child in root.sorted_children()] == ["PLC", "Drawings", "notes.txt"]
    assert root.find(["plc", "OLD"]).size == 100


def test_squarify_tiles_the_space_exactly_and_in_proportion():
    sizes = [6, 6, 4, 3, 2, 2, 1]
    rects = treemap.squarify(sizes, (0, 0, 6, 4))
    assert [round(w * h, 6) for _x, _y, w, h in rects] == [6, 6, 4, 3, 2, 2, 1]
    for x, y, w, h in rects:
        assert 0 <= x and 0 <= y and x + w <= 6 + 1e-9 and y + h <= 4 + 1e-9


def test_blocks_stay_near_square():
    rects = treemap.squarify([1] * 16, (0, 0, 400, 400))
    for _x, _y, w, h in rects:
        assert max(w, h) / min(w, h) < 1.5


def test_layout_nests_folders_and_stops_at_small_blocks():
    root = treemap.build("Line3", ROWS)
    tiles = treemap.layout(root, (0, 0, 600, 400))
    by_path = {tile.path: tile for tile in tiles}
    assert by_path[("PLC",)].opened
    assert ("PLC", "main.L5X") in by_path
    px, py, pw, ph = by_path[("PLC",)].rect
    cx, cy, cw, ch = by_path[("PLC", "main.L5X")].rect
    assert px <= cx and py + treemap.HEADER <= cy and cx + cw <= px + pw + 1e-6
    tiny = treemap.layout(root, (0, 0, 40, 30))
    assert all(not tile.opened for tile in tiny)


def test_hit_finds_the_innermost_block():
    root = treemap.build("Line3", ROWS)
    tiles = treemap.layout(root, (0, 0, 600, 400))
    inner = next(tile for tile in tiles if tile.path == ("PLC", "main.L5X"))
    x, y, w, h = inner.rect
    assert treemap.hit(tiles, x + w / 2, y + h / 2) is inner
    assert treemap.hit(tiles, -5, -5) is None


def test_an_empty_folder_lays_out_nothing():
    assert treemap.layout(treemap.build("x", []), (0, 0, 100, 100)) == []


def test_the_walk_collects_files_and_says_what_is_missing(tmp_path):
    from app.core.config import Config
    from app.core.foldermap import FolderMap
    from tests.test_columns import FakeBridge

    bridge = FakeBridge()
    handlers = []
    bridge.submit = (lambda op, path, *, timeout, on_reply, args=None:
                     handlers.append((op, args, on_reply)) or len(handlers))
    folder_map = FolderMap(bridge, Config({}, path=str(tmp_path / "c.json")))
    got = []
    folder_map.ready.connect(lambda root, note: got.append((root, note)))
    folder_map.start(str(tmp_path))
    op, args, reply = handlers[-1]
    assert op is Op.WALK and args["limit"] == 250000
    reply(Reply(1, Status.PARTIAL, payload=[Entry("a\\b.dwg", False, 10, 1.0, 0)]))
    reply(Reply(1, Status.OK, payload=[Entry("c.pdf", False, 5, 1.0, 0)],
                message="limit skipped=3"))
    root, note = got[-1]
    assert root.size == 15 and "3 folders could not be read" in note and "stopped at" in note


def test_the_window_zooms_and_comes_back_out():
    from app.ui.foldermap import FolderMapWindow

    gone = []
    window = FolderMapWindow(None, folder_label="Line3",
                             on_go=lambda where, zoomed: gone.append(where),
                             on_refresh=lambda: None)
    window.show_tree(treemap.build("Line3", ROWS), "")
    window.zoom_to(("PLC",))
    assert window._zoom == ("PLC",) and window._up.isEnabled()  # noqa: SLF001
    window.zoom_to(("notes.txt",))                  # a file is not somewhere to go into
    assert window._zoom == ("PLC",)  # noqa: SLF001
    window.up()
    assert window._zoom == ()  # noqa: SLF001
