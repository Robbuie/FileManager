r"""Where Windows is keeping a network location this application cannot find.

Written on 15 September, after 0.19's whole premise came back empty. The report
was from a Hyper-V guest: the host's C: drive is shared into it, Double
Commander lists it, and `harness network` -- which reads the redirector's table
of current connections -- answered `connections 0`. The drive list showed two
fixed disks and nothing else.

So one of these is true, and only this machine can say which:

  * the share is held under a **scope this does not ask about** -- remembered
    rather than connected, or a type other than disk;
  * it is held by a **provider that does not publish it** through
    `WNetEnumResource` at all, which is possible for the Remote Desktop
    redirector;
  * the enumeration **failed** and the failure was swallowed, which it was
    until this was written (`paths.connections` now says why);
  * or it is not `\\tsclient\...` on this machine at all, and Double Commander
    is showing something else entirely.

This asks every question at once and prints the raw answers. It is deliberately
not tidy: the fields are printed as they come back, including the ones that
look redundant, because the useful detail is likely to be the one a summary
would have dropped -- `lpProvider` above all, which names which piece of
Windows is holding the thing.

    .venv\Scripts\python.exe tools\diagnose_network.py

It writes to the console and to `Claude outputs\network-diagnosis.txt`.

One warning about what is in here. `RESOURCE_GLOBALNET` **is** used below, and
it is the call `app/io/paths.py` refuses to make: it asks the network what
exists, which is a real round trip and is how a file manager hangs while
drawing a sidebar. It is acceptable in a diagnostic somebody runs on purpose
and waits for. It must not migrate into the application.
"""

from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "Claude outputs", "network-diagnosis.txt")

#: The scopes worth asking, and what each one means. The first is what
#: `paths.connections` uses today; the rest are the places the answer could be
#: hiding instead.
SCOPES = (
    (1, "RESOURCE_CONNECTED", "attached right now"),
    (3, "RESOURCE_REMEMBERED", "persistent, whether attached or not"),
    (5, "RESOURCE_CONTEXT", "whatever the provider calls its own context"),
)

TYPES = ((1, "RESOURCETYPE_DISK"), (0, "RESOURCETYPE_ANY"))

#: Paths worth trying by hand. The first is what a Hyper-V or Remote Desktop
#: redirected host drive is called; the rest are the shapes a Hyper-V share can
#: take when it is not that.
CANDIDATES = (
    r"\\tsclient",
    r"\\tsclient\C",
    r"\\tsclient\c",
    r"\\localhost\C$",
    r"\\127.0.0.1\C$",
)

_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)


def heading(text: str) -> None:
    say()
    say(text)
    say("-" * len(text))


def describe(exc: BaseException) -> str:
    detail = str(getattr(exc, "strerror", None) or exc).strip()
    number = getattr(exc, "winerror", None)
    return f"{detail} ({number})" if number is not None else detail


def fields(item) -> str:
    """Every field of a NETRESOURCE, printed as it came back.

    Including `dwDisplayType` and `lpProvider`, which are the two most likely
    to be the answer: the provider names which piece of Windows is holding the
    connection, and that is the thing to go and ask next.
    """
    names = ("lpLocalName", "lpRemoteName", "lpProvider", "lpComment",
             "dwScope", "dwType", "dwDisplayType", "dwUsage")
    parts = []
    for name in names:
        value = getattr(item, name, None)
        if value not in (None, ""):
            parts.append(f"{name}={value!r}")
    return "    " + "  ".join(parts) if parts else "    (no fields set)"


def main() -> int:
    say("Where Windows keeps this session's network locations")
    say(f"python  {sys.version.split()[0]}")
    say(f"root    {ROOT}")

    try:
        import win32wnet
    except Exception as exc:  # noqa: BLE001
        say(f"win32wnet is not importable: {describe(exc)}")
        _write()
        return 1

    heading("What this application currently sees")
    from app.io import paths

    found, problem = paths.connections()
    say(f"paths.connections() -> {len(found)} entries")
    say(f"problem: {problem or '(none reported)'}")
    for item in found:
        say(f"    local={item.local!r}  remote={item.remote!r}  "
            f"provider={item.provider!r}  label={item.label!r}")

    heading("Drive letters, and what each one is")
    for letter in paths.logical_drives():
        kind = paths.drive_type(letter)
        mapped = ""
        try:
            mapped = win32wnet.WNetGetConnection(letter) or ""
        except Exception as exc:  # noqa: BLE001 - a local disk raises here
            mapped = f"({describe(exc)})"
        say(f"    {letter:<4} {kind:<10} {mapped}")

    heading("Every enumeration scope, asked separately")
    for scope, scope_name, meaning in SCOPES:
        for kind, kind_name in TYPES:
            label = f"{scope_name} / {kind_name}"
            try:
                handle = win32wnet.WNetOpenEnum(scope, kind, 0, None)
            except Exception as exc:  # noqa: BLE001
                say(f"{label}: would not open -- {describe(exc)}")
                continue
            entries = []
            note = ""
            try:
                while True:
                    try:
                        batch = win32wnet.WNetEnumResource(handle, 32)
                    except Exception as exc:  # noqa: BLE001
                        if getattr(exc, "winerror", None) not in (259, None):
                            note = f" (stopped: {describe(exc)})"
                        break
                    if not batch:
                        break
                    entries.extend(batch)
            finally:
                try:
                    win32wnet.WNetCloseEnum(handle)
                except Exception:  # noqa: BLE001
                    pass
            say(f"{label}: {len(entries)} entries{note}    [{meaning}]")
            for item in entries:
                say(fields(item))

    heading("Asking the network itself about the likely servers")
    say("This is the call the application refuses to make -- it is a real")
    say("round trip and it can take a while. It is here because a diagnostic")
    say("may wait where a sidebar may not.")
    for name in (r"\\tsclient",):
        resource = win32wnet.NETRESOURCE()
        resource.dwScope = 2                  # RESOURCE_GLOBALNET
        resource.dwType = 1                   # RESOURCETYPE_DISK
        resource.lpRemoteName = name
        try:
            handle = win32wnet.WNetOpenEnum(2, 1, 0, resource)
        except Exception as exc:  # noqa: BLE001
            say(f"{name}: would not open -- {describe(exc)}")
            continue
        try:
            while True:
                try:
                    batch = win32wnet.WNetEnumResource(handle, 32)
                except Exception:  # noqa: BLE001
                    break
                if not batch:
                    break
                for item in batch:
                    say(fields(item))
        finally:
            try:
                win32wnet.WNetCloseEnum(handle)
            except Exception:  # noqa: BLE001
                pass

    heading("Paths tried by hand")
    say("`exists` and a listing, which is the only question that really")
    say("matters: if the share can be listed, the application can use it and")
    say("all that is missing is somewhere to show it.")
    for candidate in CANDIDATES:
        try:
            there = os.path.exists(candidate)
        except Exception as exc:  # noqa: BLE001
            say(f"    {candidate:<22} exists -> {describe(exc)}")
            continue
        listing = ""
        if there:
            try:
                names = os.listdir(candidate)
                listing = f", {len(names)} entries: {names[:8]}"
            except Exception as exc:  # noqa: BLE001
                listing = f", listing failed: {describe(exc)}"
        say(f"    {candidate:<22} exists={there}{listing}")

    heading("What to do with this")
    say("If any section above names the host share, say which one and the")
    say("application can read it from there. If `\\\\tsclient\\C` can be")
    say("listed under 'Paths tried by hand' while every enumeration is empty,")
    say("then Windows is holding it somewhere it does not publish -- and the")
    say("answer is the saved-location list, which 0.19 already has.")
    _write()
    return 0


def _write() -> None:
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as handle:
            handle.write("\n".join(_lines) + "\n")
        print(f"\nwritten to {OUT}")
    except OSError as exc:
        print(f"\ncould not write the report: {exc}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a diagnostic that dies says why
        traceback.print_exc()
        _write()
        sys.exit(1)
