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

#: 0.53: how many modules, programs, AOIs and types the card is given in
#: detail. More than `NAMED` because the card lays the chassis and the network
#: out rather than listing them, and a chassis of seventeen slots should not
#: stop at twelve; still a bound, because a summary crosses a process boundary
#: on every cursor move in quick view.
DETAILED = 96


def summarise(path: str, deadline: float) -> dict[str, Any] | None:
    """The export's controller, dates and counts, or None if it is not one.

    None for a file whose root is not `RSLogix5000Content` -- a renamed file,
    or XML that merely ends in `.l5x` -- so the caller falls back to showing
    it as text.
    """
    summary = _empty()
    depth = 0
    root_seen = False
    #: The open elements, so a finished one can be taken off its parent.
    #: `clear` alone empties an element but leaves it in its parent's list,
    #: and a 40 MB export is a few hundred thousand of those.
    stack: list = []
    task: dict[str, Any] | None = None
    #: 0.53: the program and module being read, so a routine, a rung or a
    #: port can be credited to it on the way past.
    program: dict[str, Any] | None = None
    module: dict[str, Any] | None = None
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
                    summary["comm_path"] = attrs.get("CommPath", "")
                    summary["modified"] = attrs.get("LastModificationDate", "")
                    summary["created"] = attrs.get("ProjectCreationDate", "")
                elif tag == "Program":
                    program = {"name": element.attrib.get("Name", ""),
                               "routines": 0, "rungs": 0, "kinds": {}}
                    if len(summary["program_list"]) < DETAILED:
                        summary["program_list"].append(program)
                elif tag == "Module":
                    attrs = element.attrib
                    module = {"name": attrs.get("Name", ""),
                              "catalog": attrs.get("CatalogNumber", ""),
                              "parent": attrs.get("ParentModule", ""),
                              "port": "", "address": "", "inhibited":
                                  attrs.get("Inhibited", "false").lower() == "true"}
                elif tag == "Port" and module is not None:
                    attrs = element.attrib
                    # The upstream port is how the module is reached: a slot
                    # number on a backplane, an IP address on Ethernet. The
                    # controller itself has no upstream port, so its first
                    # port stands in.
                    upstream = attrs.get("Upstream", "").lower() == "true"
                    if upstream or not module["port"]:
                        module["port"] = attrs.get("Type", "")
                        module["address"] = attrs.get("Address", "")
                        module["upstream"] = upstream
                elif tag == "Bus" and module is not None:
                    # A backplane port says how many slots the chassis has.
                    try:
                        module["bus"] = int(element.attrib.get("Size", "0") or 0)
                    except ValueError:
                        pass
                elif tag == "AddOnInstructionDefinition":
                    if len(summary["aoi_list"]) < DETAILED:
                        summary["aoi_list"].append((element.attrib.get("Name", ""),
                                                    element.attrib.get("Revision", "")))
                elif tag == "DataType":
                    if len(summary["udt_list"]) < DETAILED:
                        summary["udt_list"].append(element.attrib.get("Name", ""))
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
                if program is not None:
                    program["routines"] += 1
                    kind = element.attrib.get("Type", "RLL")
                    program["kinds"][kind] = program["kinds"].get(kind, 0) + 1
            elif tag == "Rung":
                summary["rungs"] += 1
                if program is not None:
                    program["rungs"] += 1
            elif tag == "AddOnInstructionDefinition":
                summary["aois"] += 1
            elif tag == "DataType":
                summary["udts"] += 1
            elif tag == "Program":
                program = None
            elif tag == "Module":
                summary["modules"] += 1
                if len(summary["module_list"]) < NAMED:
                    attrs = element.attrib
                    summary["module_list"].append(
                        (attrs.get("Name", ""), attrs.get("CatalogNumber", "")))
                if module is not None and len(summary["module_detail"]) < DETAILED:
                    summary["module_detail"].append(module)
                module = None
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
        # 0.53, for the card.
        "comm_path": "", "modified": "", "created": "",
        "program_list": [], "module_detail": [], "aoi_list": [], "udt_list": [],
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
    program: dict[str, Any] | None = None
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
                if kind == "PROGRAM":
                    program = None
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
                    program = {"name": name, "routines": 0, "rungs": 0, "kinds": {}}
                    if len(summary["program_list"]) < DETAILED:
                        summary["program_list"].append(program)
                elif kind == "ROUTINE":
                    summary["routines"] += 1
                    if program is not None:
                        program["routines"] += 1
                        sort = {"ST_ROUTINE": "ST", "FBD_ROUTINE": "FBD",
                                "SFC_ROUTINE": "SFC"}.get(opening.group(1), "RLL")
                        program["kinds"][sort] = program["kinds"].get(sort, 0) + 1
                elif kind == "DATATYPE":
                    summary["udts"] += 1
                    if len(summary["udt_list"]) < DETAILED:
                        summary["udt_list"].append(name)
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
                if program is not None:
                    program["rungs"] += 1
            elif inside == "TASK" and task is not None:
                scheduled = _SCHEDULED.match(line)
                if scheduled:
                    task["programs"].append(scheduled.group(1))
    return summary if seen_controller else None


def _header_done(summary: dict[str, Any], kind: str, attrs: dict[str, Any],
                 task: dict[str, Any] | None) -> None:
    """A block's header has been read whole; keep what the summary shows."""
    if kind == "CONTROLLER":
        summary["comm_path"] = summary["comm_path"] or str(attrs.get("CommPath", ""))
        summary["modified"] = summary["modified"] or str(attrs.get("LastModificationDate", ""))
        summary["processor"] = summary["processor"] or attrs.get("ProcessorType", "")
        major, minor = attrs.get("Major", ""), attrs.get("Minor", "")
        if major and not summary["firmware"]:
            summary["firmware"] = f"{major}.{minor}" if minor else major
    elif kind == "MODULE":
        if len(summary["module_list"]) < NAMED:
            summary["module_list"].append((attrs.get("Name", ""), attrs.get("CatalogNumber", "")))
        if len(summary["module_detail"]) < DETAILED:
            # An .L5K names a module's slot but not its network address, so
            # only the chassis can be drawn from one.
            slot = str(attrs.get("Slot", ""))
            summary["module_detail"].append({
                "name": attrs.get("Name", ""), "catalog": attrs.get("CatalogNumber", ""),
                "parent": attrs.get("Parent", ""), "port": "ICP" if slot.isdigit() else "",
                "address": slot, "upstream": True, "inhibited": False})
    elif kind == "ADD_ON_INSTRUCTION_DEFINITION" and len(summary["aoi_list"]) < DETAILED:
        summary["aoi_list"].append((attrs.get("Name", ""), str(attrs.get("Revision", ""))))
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


# ------------------------------------------------------------------ 0.55 browse
#
# The F3 browser needs what the summary deliberately throws away: every rung's
# text. So this is a second reader, used only when somebody presses F3 on an
# export, that keeps the logic and nothing else -- no tags, no data, no I/O
# configuration -- and is kept by the worker for the export last browsed, so
# moving from routine to routine is a lookup rather than another forty
# megabytes off a share.

import os  # noqa: E402 - only this half needs it
from dataclasses import dataclass, field  # noqa: E402

#: The most rungs one routine reply carries, and the most search hits. Both
#: far beyond a real routine; both a bound on one message across the pipe.
MAX_RUNGS = 5000
MAX_HITS = 1000


class NotAnExport(Exception):
    """A file named like an export whose content is not one."""


@dataclass
class Routine:
    name: str
    kind: str = "RLL"              # RLL, ST, FBD, SFC
    #: (number, comment, text) for ladder; text is Logix's neutral form.
    rungs: list = field(default_factory=list)
    #: Lines of structured text.
    lines: list = field(default_factory=list)
    #: Source-protected: the logic is encrypted in the file.
    protected: bool = False


@dataclass
class Container:
    name: str
    kind: str                      # "program" or "aoi"
    routines: dict = field(default_factory=dict)


@dataclass
class Project:
    controller: str = ""
    containers: list = field(default_factory=list)

    def _find(self, program: str, routine: str) -> Routine | None:
        for container in self.containers:
            if container.name == program:
                return container.routines.get(routine)
        return None

    def outline(self) -> dict:
        return {"controller": self.controller, "containers": [
            {"name": c.name, "kind": c.kind, "routines": [
                {"name": r.name, "kind": r.kind, "protected": r.protected,
                 "count": len(r.rungs) if r.kind == "RLL" else len(r.lines)}
                for r in c.routines.values()]}
            for c in self.containers]}

    def routine(self, program: str, name: str) -> dict:
        found = self._find(program, name)
        if found is None:
            return {"program": program, "routine": name, "missing": True}
        return {"program": program, "routine": name, "kind": found.kind,
                "protected": found.protected,
                "rungs": [list(r) for r in found.rungs[:MAX_RUNGS]],
                "lines": list(found.lines[:MAX_RUNGS]),
                "more": max(len(found.rungs), len(found.lines)) > MAX_RUNGS}

    def search(self, text: str) -> dict:
        needle = text.strip().lower()
        hits: list[dict] = []
        if not needle:
            return {"text": text, "hits": [], "more": False}
        for container in self.containers:
            for routine in container.routines.values():
                for number, comment, rung in routine.rungs:
                    if needle in rung.lower() or needle in (comment or "").lower():
                        hits.append({"program": container.name, "routine": routine.name,
                                     "at": number})
                        if len(hits) >= MAX_HITS:
                            return {"text": text, "hits": hits, "more": True}
                for number, line in enumerate(routine.lines):
                    if needle in line.lower():
                        hits.append({"program": container.name, "routine": routine.name,
                                     "at": number})
                        if len(hits) >= MAX_HITS:
                            return {"text": text, "hits": hits, "more": True}
        return {"text": text, "hits": hits, "more": False}


_loaded: dict = {}


def load(path: str, deadline: float) -> Project:
    """The export's logic, read now or remembered from the last time.

    One export is kept: browsing is one file at a time, and the logic of a
    large project is tens of megabytes of strings.
    """
    stat = os.stat(paths.api(path))
    key = (os.path.normcase(path), int(stat.st_size), float(stat.st_mtime))
    if _loaded.get("key") == key:
        return _loaded["project"]
    if path.lower().endswith(".l5k"):
        project = _load_l5k(path, deadline)
    else:
        project = _load_l5x(path, deadline)
    _loaded.clear()
    _loaded.update(key=key, project=project)
    return project


def _load_l5x(path: str, deadline: float) -> Project:
    project = Project()
    container: Container | None = None
    routine: Routine | None = None
    rung: dict | None = None
    stack: list = []
    count = 0
    with open(paths.api(path), "rb") as handle:
        for event, element in ElementTree.iterparse(handle, events=("start", "end")):
            tag = element.tag
            if event == "start":
                stack.append(element)
                if len(stack) == 1 and tag != "RSLogix5000Content":
                    raise NotAnExport("not a Logix Designer export")
                if tag == "Controller" and not project.controller:
                    project.controller = element.attrib.get("Name", "")
                elif tag == "Program":
                    container = Container(element.attrib.get("Name", ""), "program")
                    project.containers.append(container)
                elif tag == "AddOnInstructionDefinition":
                    container = Container(element.attrib.get("Name", ""), "aoi")
                    project.containers.append(container)
                elif tag == "Routine" and container is not None:
                    routine = Routine(element.attrib.get("Name", ""),
                                      element.attrib.get("Type", "RLL"))
                    container.routines[routine.name] = routine
                elif tag == "Rung" and routine is not None:
                    rung = {"number": element.attrib.get("Number", ""),
                            "comment": "", "text": ""}
                elif tag == "EncodedData" and routine is not None:
                    routine.protected = True
                continue
            stack.pop()
            if tag == "Comment" and rung is not None:
                rung["comment"] = (element.text or "").strip()
            elif tag == "Text" and rung is not None:
                rung["text"] = (element.text or "").strip()
            elif tag == "Rung" and rung is not None and routine is not None:
                try:
                    number = int(rung["number"])
                except ValueError:
                    number = len(routine.rungs)
                routine.rungs.append((number, rung["comment"], rung["text"]))
                rung = None
            elif tag == "Line" and routine is not None and routine.kind == "ST":
                routine.lines.append(element.text or "")
            elif tag == "Routine":
                routine = None
            elif tag in ("Program", "AddOnInstructionDefinition"):
                container = None
            element.clear()
            if stack:
                try:
                    stack[-1].remove(element)
                except ValueError:
                    pass
            count += 1
            if count % 4096 == 0 and time.monotonic() > deadline:
                raise TimeoutError("reading the export took too long")
    if not project.controller and not project.containers:
        raise NotAnExport("not a Logix Designer export")
    # AOIs after programs: what a person browsing a project looks for first.
    project.containers.sort(key=lambda c: c.kind != "program")
    return project


_ROUTINE_LINE = re.compile(r"^\s*(ROUTINE|ST_ROUTINE|FBD_ROUTINE|SFC_ROUTINE)\s+(\w+)")
_RUNG_LINE = re.compile(r"^\s*N:\s*(.*?)\s*$")
_COMMENT_LINE = re.compile(r'^\s*RC:\s*"(.*)$')


def _load_l5k(path: str, deadline: float) -> Project:
    project = Project()
    container: Container | None = None
    routine: Routine | None = None
    comment: list[str] | None = None
    pending = ""
    with open(paths.api(path), "r", encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle):
            if number % 512 == 0 and time.monotonic() > deadline:
                raise TimeoutError("reading the export took too long")
            if comment is not None:
                # A rung comment runs on until its closing `";`.
                if line.rstrip().endswith('";'):
                    comment.append(line.rstrip()[:-2])
                    pending = " ".join(part.strip() for part in comment)
                    comment = None
                else:
                    comment.append(line.strip())
                continue
            opening = _OPEN.match(line)
            if opening and opening.group(1) == "CONTROLLER":
                project.controller = opening.group(2)
                continue
            if opening and opening.group(1) == "PROGRAM":
                container = Container(opening.group(2), "program")
                project.containers.append(container)
                continue
            if opening and opening.group(1) == "ADD_ON_INSTRUCTION_DEFINITION":
                container = Container(opening.group(2), "aoi")
                project.containers.append(container)
                continue
            found = _ROUTINE_LINE.match(line)
            if found and container is not None:
                kind = {"ST_ROUTINE": "ST", "FBD_ROUTINE": "FBD",
                        "SFC_ROUTINE": "SFC"}.get(found.group(1), "RLL")
                routine = Routine(found.group(2), kind)
                container.routines[routine.name] = routine
                continue
            closing = _CLOSE.match(line)
            if closing:
                kind = closing.group(1)
                if kind.endswith("ROUTINE"):
                    routine = None
                elif kind in ("PROGRAM", "ADD_ON_INSTRUCTION_DEFINITION"):
                    container = None
                continue
            if routine is None:
                continue
            if routine.kind == "RLL":
                started = _COMMENT_LINE.match(line)
                if started:
                    rest = started.group(1).rstrip()
                    if rest.endswith('";'):
                        pending = rest[:-2]
                    else:
                        comment = [rest]
                    continue
                rung = _RUNG_LINE.match(line)
                if rung:
                    routine.rungs.append((len(routine.rungs), pending, rung.group(1)))
                    pending = ""
            elif routine.kind == "ST":
                routine.lines.append(line.rstrip("\r\n"))
    if not project.controller:
        raise NotAnExport("not a Logix Designer export")
    project.containers.sort(key=lambda c: c.kind != "program")
    return project
