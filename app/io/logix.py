"""What a Logix Designer export says about itself (0.36).

An `.L5X` is XML, so the preview pane could always show it -- as the first
sixteen kilobytes of XML, which says "this is XML" and nothing else. What a
person looking at a folder of exports actually wants to know is which
controller it is, which firmware it was saved for, when it was exported, and
roughly how big the program is. All of that is in the file: the controller's
attributes are on the first few elements, and the counts are a matter of
counting elements on the way through.

So this walks the file once with `iterparse`, keeping nothing but counters and
a handful of names, and clears every element behind it. Memory stays flat
however large the export is, and the walk respects the preview's deadline: a
40 MB export on a slow share that is still being read when the time runs out
is reported with the counts so far and says they are partial, rather than
being thrown away.

No Qt here -- this runs in a worker -- and no dependency: `xml.etree` is in
the standard library. The file is only opened through `paths.api`, like every
other read in this layer.

0.40: `.L5K` too. It is the same project written as structured text rather
than XML -- `CONTROLLER Name (...)` down to `END_CONTROLLER`, with `TAG`,
`PROGRAM`, `ROUTINE`, `TASK` and `MODULE` blocks between -- so it is read a line
at a time into the same summary, and `render` draws both the same way.

`.ACD` files are the project in Rockwell's own binary format and are not read
here: their layout is not documented, and a guess that is wrong on the next
version of the software is worse than the ordinary preview.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ElementTree
from typing import Any

from app.io import paths

#: How many modules and tasks the summary names before it only counts.
NAMED = 12


def summarise(path: str, deadline: float) -> dict[str, Any] | None:
    """The export's controller, dates and counts, or None if it is not one.

    None for a file whose root is not `RSLogix5000Content` -- a renamed file,
    or XML that merely ends in `.l5x` -- so the caller falls back to showing
    it as text.
    """
    summary: dict[str, Any] = {
        "controller": "", "processor": "", "firmware": "", "software": "",
        "exported": "", "target": "", "target_type": "",
        "tags": 0, "programs": 0, "routines": 0, "aois": 0, "modules": 0,
        "udts": 0, "rungs": 0,
        "tasks": [], "module_list": [], "partial": False,
    }
    depth = 0
    root_seen = False
    #: The open elements, so a finished one can be taken off its parent.
    #: `clear` alone empties an element but leaves it in its parent's list,
    #: and a 40 MB export is a few hundred thousand of those.
    stack: list = []
    task: dict[str, Any] | None = None
    with open(paths.api(path), "rb") as handle:
        events = ElementTree.iterparse(handle, events=("start", "end"))
        for event, element in events:
            tag = element.tag
            if event == "start":
                depth += 1
                stack.append(element)
                if depth == 1:
                    if tag != "RSLogix5000Content":
                        return None
                    root_seen = True
                    attrs = element.attrib
                    summary["software"] = attrs.get("SoftwareRevision", "")
                    summary["exported"] = attrs.get("ExportDate", "")
                    summary["target"] = attrs.get("TargetName", "")
                    summary["target_type"] = attrs.get("TargetType", "")
                elif tag == "Controller" and not summary["controller"]:
                    attrs = element.attrib
                    summary["controller"] = attrs.get("Name", "")
                    summary["processor"] = attrs.get("ProcessorType", "")
                    major, minor = attrs.get("MajorRev", ""), attrs.get("MinorRev", "")
                    if major:
                        summary["firmware"] = f"{major}.{minor}" if minor else major
                elif tag == "Task":
                    attrs = element.attrib
                    task = {"name": attrs.get("Name", ""),
                            "type": attrs.get("Type", "").lower(),
                            "rate": attrs.get("Rate", ""), "programs": []}
                elif tag == "ScheduledProgram" and task is not None:
                    task["programs"].append(element.attrib.get("Name", ""))
                continue

            # "end": count, then drop what was built.
            depth -= 1
            stack.pop()
            if tag == "Tag":
                summary["tags"] += 1
            elif tag == "Program":
                summary["programs"] += 1
            elif tag == "Routine":
                summary["routines"] += 1
            elif tag == "Rung":
                summary["rungs"] += 1
            elif tag == "AddOnInstructionDefinition":
                summary["aois"] += 1
            elif tag == "DataType":
                summary["udts"] += 1
            elif tag == "Module":
                summary["modules"] += 1
                if len(summary["module_list"]) < NAMED:
                    attrs = element.attrib
                    summary["module_list"].append(
                        (attrs.get("Name", ""), attrs.get("CatalogNumber", "")))
            elif tag == "Task" and task is not None:
                if len(summary["tasks"]) < NAMED:
                    summary["tasks"].append(task)
                task = None
            # Only a finished element may be cleared: the ones still open
            # carry the attributes being read above.
            element.clear()
            if stack:
                try:
                    stack[-1].remove(element)
                except ValueError:
                    pass
            if time.monotonic() > deadline:
                summary["partial"] = True
                break
    return summary if root_seen else None


def _empty() -> dict[str, Any]:
    return {
        "controller": "", "processor": "", "firmware": "", "software": "",
        "exported": "", "target": "", "target_type": "",
        "tags": 0, "programs": 0, "routines": 0, "aois": 0, "modules": 0,
        "udts": 0, "rungs": 0,
        "tasks": [], "module_list": [], "partial": False,
    }


#: A block keyword at the start of a line, and the name after it.
_OPEN = re.compile(r"^\s*(CONTROLLER|PROGRAM|ROUTINE|FBD_ROUTINE|SFC_ROUTINE|ST_ROUTINE|"
                   r"TASK|MODULE|DATATYPE|ADD_ON_INSTRUCTION_DEFINITION|TAG)\b\s*([\w:.\[\]-]*)")
_CLOSE = re.compile(r"^\s*END_(\w+)")
#: `Name := value` inside a block's header, with the value unquoted.
_ATTR = re.compile(r"\b(\w+)\s*:=\s*(\"(?:[^\"$]|\$.)*\"|[^,)\s]+)")
#: The first line of a tag declaration: `Name : TYPE` or `Name OF alias`.
_TAG_LINE = re.compile(r"^\s*[A-Za-z_]\w*\s*(?::(?!=)|\bOF\b)")
#: A ladder rung, and a program named on its own line inside a TASK.
_RUNG = re.compile(r"^\s*N:")
_SCHEDULED = re.compile(r"^\s*([A-Za-z_]\w*)\s*;\s*$")
#: The export header's comment: `Version := RSLogix 5000 v33.00`, `Exported := ...`.
_HEADER = re.compile(r"^\s*(Version|Exported)\s*:=\s*(.+?)\s*$")

_BLOCK_KEYS = {"FBD_ROUTINE": "ROUTINE", "SFC_ROUTINE": "ROUTINE", "ST_ROUTINE": "ROUTINE"}


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


def summarise_l5k(path: str, deadline: float) -> dict[str, Any] | None:
    """The same summary as `summarise`, read from an `.L5K` text export.

    None when no `CONTROLLER` line turns up before the file ends, so a text
    file that merely ends in `.l5k` is shown as text. Line by line, keeping
    only counters, and the deadline is checked every few hundred lines.
    """
    summary = _empty()
    stack: list[str] = []
    #: The block whose header is still being read: attributes arrive over
    #: several lines until its closing parenthesis.
    header: tuple[str, dict[str, Any]] | None = None
    task: dict[str, Any] | None = None
    seen_controller = False
    with open(paths.api(path), "r", encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle):
            if number % 256 == 0 and time.monotonic() > deadline:
                summary["partial"] = True
                break
            if not seen_controller:
                found = _HEADER.match(line)
                if found:
                    key, value = found.groups()
                    if key == "Version":
                        summary["software"] = value.replace("RSLogix 5000", "").strip(" v")
                    else:
                        summary["exported"] = value
            if header is not None:
                kind, attrs = header
                for name, value in _ATTR.findall(line):
                    attrs.setdefault(name, _unquote(value))
                if ")" in line and not line.strip().startswith("("):
                    _header_done(summary, kind, attrs, task)
                    header = None
                continue
            closing = _CLOSE.match(line)
            if closing:
                kind = _BLOCK_KEYS.get(closing.group(1), closing.group(1))
                if stack and stack[-1] == kind:
                    stack.pop()
                if kind == "TASK" and task is not None:
                    if len(summary["tasks"]) < NAMED:
                        summary["tasks"].append(task)
                    task = None
                continue
            opening = _OPEN.match(line)
            if opening:
                kind = _BLOCK_KEYS.get(opening.group(1), opening.group(1))
                name = opening.group(2)
                stack.append(kind)
                if kind == "CONTROLLER":
                    seen_controller = True
                    summary["controller"] = summary["controller"] or name
                elif kind == "PROGRAM":
                    summary["programs"] += 1
                elif kind == "ROUTINE":
                    summary["routines"] += 1
                elif kind == "DATATYPE":
                    summary["udts"] += 1
                elif kind == "ADD_ON_INSTRUCTION_DEFINITION":
                    summary["aois"] += 1
                elif kind == "MODULE":
                    summary["modules"] += 1
                elif kind == "TASK":
                    task = {"name": name, "type": "", "rate": "", "programs": []}
                if kind != "TAG":
                    attrs = {"Name": name}
                    for key, value in _ATTR.findall(line):
                        attrs.setdefault(key, _unquote(value))
                    if "(" in line and ")" not in line[line.index("("):]:
                        header = (kind, attrs)
                    else:
                        _header_done(summary, kind, attrs, task)
                continue
            inside = stack[-1] if stack else ""
            if inside == "TAG" and _TAG_LINE.match(line):
                summary["tags"] += 1
            elif inside == "ROUTINE" and _RUNG.match(line):
                summary["rungs"] += 1
            elif inside == "TASK" and task is not None:
                scheduled = _SCHEDULED.match(line)
                if scheduled:
                    task["programs"].append(scheduled.group(1))
    return summary if seen_controller else None


def _header_done(summary: dict[str, Any], kind: str, attrs: dict[str, Any],
                 task: dict[str, Any] | None) -> None:
    """A block's header has been read whole; keep what the summary shows."""
    if kind == "CONTROLLER":
        summary["processor"] = summary["processor"] or attrs.get("ProcessorType", "")
        major, minor = attrs.get("Major", ""), attrs.get("Minor", "")
        if major and not summary["firmware"]:
            summary["firmware"] = f"{major}.{minor}" if minor else major
    elif kind == "MODULE" and len(summary["module_list"]) < NAMED:
        summary["module_list"].append((attrs.get("Name", ""), attrs.get("CatalogNumber", "")))
    elif kind == "TASK" and task is not None:
        task["type"] = str(attrs.get("Type", "")).lower()
        task["rate"] = str(attrs.get("Rate", ""))


def render(summary: dict[str, Any]) -> str:
    """The summary as the few lines the preview pane and the viewer show."""
    lines = []
    what = "Logix Designer export"
    if summary.get("target_type") and summary["target_type"] != "Controller":
        what = f"Logix Designer export of a {summary['target_type'].lower()}"
        if summary.get("target"):
            what += f" ({summary['target']})"
    lines.append(what)
    lines.append("")
    for label, key in (("Controller", "controller"), ("Processor", "processor"),
                       ("Firmware", "firmware"), ("Saved by", "software"),
                       ("Exported", "exported")):
        value = summary.get(key)
        if value:
            if key == "software":
                value = f"Logix Designer {value}"
            lines.append(f"{label:<11} {value}")
    lines.append("")
    counts = [("Tags", "tags"), ("Programs", "programs"), ("Routines", "routines"),
              ("Rungs", "rungs"), ("AOIs", "aois"), ("UDTs", "udts"),
              ("Modules", "modules")]
    shown = [f"{label} {summary.get(key, 0):,}" for label, key in counts
             if summary.get(key)]
    if shown:
        lines.append("   ".join(shown[:4]))
        if shown[4:]:
            lines.append("   ".join(shown[4:]))
    if summary.get("partial"):
        lines.append("(counted as far as the time allowed -- at least this many)")
    tasks = summary.get("tasks") or []
    if tasks:
        lines.append("")
        lines.append("Tasks")
        for task in tasks:
            kind = task.get("type", "")
            rate = task.get("rate", "")
            detail = kind + (f", {rate} ms" if rate and kind == "periodic" else "")
            lines.append(f"  {task.get('name', '')}" + (f"  ({detail})" if detail else ""))
            for program in task.get("programs", [])[:NAMED]:
                lines.append(f"    {program}")
    modules = summary.get("module_list") or []
    if modules:
        lines.append("")
        lines.append("I/O")
        width = max(len(name) for name, _catalog in modules)
        for name, catalog in modules:
            lines.append(f"  {name:<{width}}  {catalog}")
        extra = summary.get("modules", 0) - len(modules)
        if extra > 0:
            lines.append(f"  and {extra:,} more")
    return "\n".join(lines)
