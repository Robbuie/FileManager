"""The folder map (0.43): a folder's tree as nested rectangles sized by bytes.

One walk -- `Op.WALK`, the one flat view and search already use -- gives every
file under the folder with its size, and that is all a treemap needs: folders
are the sums of what is in them. So there is no second scanner and nothing
new in the worker; this module turns the walk's rows into a tree and the tree
into rectangles, and the widget paints them.

Pure: no Qt, no filesystem. A rectangle is `(x, y, w, h)` in floats, laid out
with the squarified algorithm (Bruls, Huizing and van Wijk), which keeps the
blocks as close to square as it can -- long thin slivers are what make a
treemap unreadable. A folder is drawn as a frame with a strip for its name and
its children inside, down to a depth and a size below which a block is drawn
whole rather than subdivided.

A folder's colour is the family (see `core/filetypes.py`) holding most of its
bytes, so a folder full of drawings reads as drawings from across the room.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.core.filetypes import family as family_of

Rect = tuple[float, float, float, float]


@dataclass
class Node:
    name: str
    size: int = 0
    is_dir: bool = False
    children: dict[str, "Node"] = field(default_factory=dict)
    #: Bytes by family, for a folder; its colour is the largest.
    families: dict[str, int] = field(default_factory=dict)
    files: int = 0

    @property
    def family(self) -> str:
        if not self.is_dir:
            return family_of(self.name)
        if not self.families:
            return "other"
        return max(self.families.items(), key=lambda item: (item[1], item[0]))[0]

    def sorted_children(self) -> list["Node"]:
        return sorted((child for child in self.children.values() if child.size > 0),
                      key=lambda child: (-child.size, child.name.lower()))

    def find(self, parts: list[str]) -> "Node | None":
        node = self
        for part in parts:
            node = node.children.get(part.lower())
            if node is None:
                return None
        return node


def build(name: str, rows: Iterable[tuple[str, int]]) -> Node:
    """The tree for `(relative path, size)` rows from a walk.

    Paths use backslashes, as the walk sends them. Children are keyed by lower
    case, which is how Windows compares names; the name kept is the first one
    seen.
    """
    root = Node(name, is_dir=True)
    for path, size in rows:
        parts = [part for part in path.split("\\") if part]
        if not parts:
            continue
        size = max(0, int(size))
        leaf_family = family_of(parts[-1])
        node = root
        node.size += size
        node.files += 1
        node.families[leaf_family] = node.families.get(leaf_family, 0) + size
        for depth, part in enumerate(parts):
            last = depth == len(parts) - 1
            key = part.lower()
            child = node.children.get(key)
            if child is None:
                child = Node(part, is_dir=not last)
                node.children[key] = child
            if last:
                child.size += size
                child.files += 1
            else:
                child.is_dir = True
                child.size += size
                child.files += 1
                child.families[leaf_family] = child.families.get(leaf_family, 0) + size
            node = child
    return root


def _worst(row: list[float], side: float) -> float:
    total = sum(row)
    if total <= 0 or side <= 0:
        return float("inf")
    biggest, smallest = max(row), min(row)
    return max(side * side * biggest / (total * total),
               (total * total) / (side * side * smallest))


def squarify(sizes: list[float], rect: Rect) -> list[Rect]:
    """Rectangles for `sizes` (largest first) that tile `rect` in proportion."""
    x, y, w, h = rect
    total = sum(sizes)
    if not sizes or total <= 0 or w <= 0 or h <= 0:
        return [(x, y, 0.0, 0.0) for _ in sizes]
    scale = w * h / total
    areas = [size * scale for size in sizes]
    out: list[Rect] = []
    index = 0
    while index < len(areas):
        side = min(w, h)
        row = [areas[index]]
        index += 1
        while index < len(areas) and _worst(row + [areas[index]], side) <= _worst(row, side):
            row.append(areas[index])
            index += 1
        row_total = sum(row)
        if w >= h:
            # A column on the left, as tall as the space.
            column = row_total / h if h else 0.0
            offset = y
            for area in row:
                height = area / column if column else 0.0
                out.append((x, offset, column, height))
                offset += height
            x += column
            w -= column
        else:
            band = row_total / w if w else 0.0
            offset = x
            for area in row:
                width = area / band if band else 0.0
                out.append((offset, y, width, band))
                offset += width
            y += band
            h -= band
    return out


@dataclass
class Tile:
    rect: Rect
    node: Node
    depth: int
    #: Names from the map's root down to this node.
    path: tuple[str, ...]
    #: Whether this folder's children were laid out inside it.
    opened: bool = False


#: A folder's name strip, and the gap between a frame and what is in it.
HEADER = 16.0
PAD = 2.0


def layout(root: Node, rect: Rect, *, max_depth: int = 4, min_side: float = 28.0,
           path: tuple[str, ...] = ()) -> list[Tile]:
    """Every block to draw, outermost first, so painting in order nests them."""
    tiles: list[Tile] = []

    def place(node: Node, box: Rect, depth: int, where: tuple[str, ...]) -> None:
        children = node.sorted_children()
        x, y, w, h = box
        inner = (x + PAD, y + HEADER, w - 2 * PAD, h - HEADER - PAD) if depth > 0 \
            else (x, y, w, h)
        for child, child_box in zip(children, squarify([c.size for c in children], inner)):
            cx, cy, cw, ch = child_box
            if cw < 1 or ch < 1:
                continue
            opened = (child.is_dir and child.children and depth + 1 < max_depth
                      and cw >= min_side * 2 and ch >= HEADER + min_side)
            tile = Tile(child_box, child, depth + 1, where + (child.name,), bool(opened))
            tiles.append(tile)
            if opened:
                place(child, child_box, depth + 1, where + (child.name,))

    place(root, rect, 0, path)
    return tiles


def hit(tiles: list[Tile], x: float, y: float) -> Tile | None:
    """The innermost block under a point."""
    found = None
    for tile in tiles:
        tx, ty, tw, th = tile.rect
        if tx <= x < tx + tw and ty <= y < ty + th:
            if found is None or tile.depth >= found.depth:
                found = tile
    return found
