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

`.ACD` files are the project in Rockwell's own binary format and are not read
here: their layout is not documented, and a guess that is wrong on the next
version of the software is worse than the ordinary preview.
"""

from __future__ import annotations

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
