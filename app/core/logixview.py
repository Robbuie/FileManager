"""What the Logix card draws, worked out from the summary (0.53).

`app/io/logix.py` reads an export into plain counts and lists; this decides
what they mean on screen -- which modules are the local chassis and in which
slot, which are on the network under which bridge, what a catalog number is
called in a slot two words wide, how a task's rate is said. Pure functions on
plain data, so the card is a layout and every rule it follows is tested here
without a window.

Nothing here guesses beyond what the export states. A slot is a module whose
upstream port is the backplane of the local chassis and whose address is a
number; a network device is one whose upstream port is Ethernet. A module
that is neither -- a Point I/O card under its adapter, a DeviceNet node -- is
counted under its parent rather than drawn, because the export says where it
is attached and not much else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Catalog prefixes, after the family number, and what kind of module they are.
#: Rockwell's own naming, which is regular enough to be read: `1756-IB16` is a
#: 16-point digital input, `1756-OF8` an 8-channel analog output.
_KINDS: tuple[tuple[str, str], ...] = (
    ("L8", "controller"), ("L7", "controller"), ("L6", "controller"),
    ("L3", "controller"), ("L2", "controller"), ("L1", "controller"),
    ("EN", "Ethernet"), ("EWEB", "Ethernet"), ("AENT", "adapter"),
    ("CN", "ControlNet"), ("DNB", "DeviceNet"), ("DHRIO", "DH+"),
    ("IB", "in"), ("IV", "in"), ("IA", "in"), ("IM", "in"), ("IG", "in"),
    ("OB", "out"), ("OV", "out"), ("OA", "out"), ("OW", "relay"), ("OX", "relay"),
    ("IF", "analog in"), ("IR", "RTD"), ("IT", "thermo"), ("IY", "analog in"),
    ("OF", "analog out"), ("HSC", "counter"), ("CFM", "flow"), ("M0", "motion"),
    ("PA", "power"), ("PB", "power"), ("RM", "redundancy"),
)


def short_catalog(catalog: str) -> str:
    """`1756-IB16/A` -> `IB16`. What fits in a slot two words wide."""
    text = (catalog or "").split("/")[0]
    if "-" in text and text.split("-")[0].isdigit():
        text = text.split("-", 1)[1]
    return text


def kind_of(catalog: str) -> str:
    """A few words for what a module is, from its catalog number, or ""."""
    short = short_catalog(catalog).upper()
    for prefix, kind in _KINDS:
        if short.startswith(prefix):
            if kind in ("in", "out", "relay", "analog in", "analog out"):
                points = re.match(rf"{prefix}(\d+)", short)
                return f"{points.group(1)} {kind}" if points else kind
            return kind
    return ""


@dataclass
class Slot:
    number: int
    name: str = ""
    catalog: str = ""
    kind: str = ""
    controller: bool = False
    inhibited: bool = False

    @property
    def empty(self) -> bool:
        return not self.name


@dataclass
class Device:
    name: str
    catalog: str
    address: str
    #: Modules attached below this one (Point I/O cards under an adapter).
    children: int = 0


@dataclass
class Network:
    #: The module the devices hang off -- the bridge in the chassis.
    via: str
    devices: list[Device] = field(default_factory=list)


def chassis(summary: dict[str, Any]) -> list[Slot]:
    """The local chassis, one entry per slot, empty slots included.

    The size comes from the controller's backplane port when the export says
    it; otherwise the chassis is as long as its last occupied slot.
    """
    modules = summary.get("module_detail") or []
    controller = next((m for m in modules if m.get("name") == "Local"), None)
    taken: dict[int, Slot] = {}
    for module in modules:
        local = module.get("name") == "Local" or module.get("parent") == "Local"
        if not local or module.get("port") != "ICP":
            continue
        try:
            number = int(module.get("address", ""))
        except ValueError:
            continue
        taken[number] = Slot(number, module.get("name", ""), module.get("catalog", ""),
                             kind_of(module.get("catalog", "")),
                             controller=module.get("name") == "Local",
                             inhibited=bool(module.get("inhibited")))
    if not taken:
        return []
    size = int((controller or {}).get("bus") or 0)
    size = max(size, max(taken) + 1)
    return [taken.get(n, Slot(n)) for n in range(min(size, 32))]


def networks(summary: dict[str, Any]) -> list[Network]:
    """Devices on Ethernet, grouped by the module they are reached through."""
    modules = summary.get("module_detail") or []
    children: dict[str, int] = {}
    for module in modules:
        parent = module.get("parent", "")
        if module.get("port") not in ("ICP", "Ethernet") and parent:
            children[parent] = children.get(parent, 0) + 1
    grouped: dict[str, Network] = {}
    for module in modules:
        if module.get("port") != "Ethernet" or not module.get("upstream", True):
            continue
        via = module.get("parent") or "Local"
        grouped.setdefault(via, Network(via)).devices.append(Device(
            module.get("name", ""), module.get("catalog", ""), module.get("address", ""),
            children.get(module.get("name", ""), 0)))
    return list(grouped.values())


def task_rate(task: dict[str, Any]) -> str:
    """`continuous`, `every 10 ms`, `event`."""
    kind = str(task.get("type", "")).lower()
    rate = str(task.get("rate", ""))
    if kind == "periodic" and rate:
        try:
            value = float(rate)
            rate = f"{value:g}"
        except ValueError:
            pass
        return f"every {rate} ms"
    return kind or ""


def program_line(program: dict[str, Any]) -> str:
    """`4 routines · 220 rungs`, with any routine that is not ladder named."""
    routines = int(program.get("routines", 0))
    rungs = int(program.get("rungs", 0))
    parts = [f"{routines} routine{'s' if routines != 1 else ''}"]
    if rungs:
        parts.append(f"{rungs:,} rung{'s' if rungs != 1 else ''}")
    other = {kind: count for kind, count in (program.get("kinds") or {}).items()
             if kind != "RLL"}
    names = {"ST": "ST", "FBD": "FBD", "SFC": "SFC"}
    for kind, count in sorted(other.items()):
        parts.append(f"{count} {names.get(kind, kind)}")
    return " · ".join(parts)


def address(comm_path: str) -> str:
    """The controller's IP and slot out of its saved comm path, or "".

    `AB_ETHIP-1\\10.20.4.10\\Backplane\\0` -> `10.20.4.10, slot 0`. The driver
    name is RSLinx's and means nothing on another machine, so it is dropped.
    """
    parts = [part for part in re.split(r"[\\/]", comm_path or "") if part]
    ip = next((part for part in parts
               if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", part)), "")
    slot = ""
    for here, part in enumerate(parts[:-1]):
        if part.lower() == "backplane":
            slot = parts[here + 1]
    if ip and slot:
        return f"{ip}, slot {slot}"
    return ip or ""


def when(text: str) -> str:
    """Logix's date form, `Thu Sep 24 13:58:40 2026`, as `24 Sep 2026 13:58`."""
    match = re.match(r"\w{3} (\w{3}) +(\d{1,2}) (\d{2}):(\d{2}):\d{2} (\d{4})", text or "")
    if not match:
        return text or ""
    month, day, hour, minute, year = match.groups()
    return f"{int(day)} {month} {year} {hour}:{minute}"


def tiles(summary: dict[str, Any]) -> list[tuple[str, str]]:
    """The counts across the top, as (number, label), only those there are."""
    out = []
    for label, key in (("Programs", "programs"), ("Routines", "routines"),
                       ("Rungs", "rungs"), ("Tags", "tags"), ("AOIs", "aois"),
                       ("UDTs", "udts"), ("Modules", "modules")):
        value = int(summary.get(key) or 0)
        if value:
            out.append((f"{value:,}", label))
    return out
