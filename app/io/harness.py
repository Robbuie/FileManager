"""Headless CLI over the io layer. No Qt, no window, nothing from `app.ui`.

This is how step 1 of the build order is verified, because the checks that
matter cannot be made from a GUI:

  * a 50,000-file listing over SMB, timed, with the time to the first batch
    reported separately — a listing that streams is one where the first batch
    lands long before the last;
  * a connection yanked mid-listing (`soak`, or `list` against a share while
    the cable comes out);
  * a worker killed while requests are outstanding (`list --kill-after`),
    which must settle the outstanding request rather than hang;
  * a reconnect after a mapped drive's session has died (`drives`, then `list`
    again once the volume is marked unreachable, then `retry`).

Every command prints plain text and exits non-zero if the operation did not
settle OK, so any of it can go in a script.

    python -m app.io.harness --help
"""

from __future__ import annotations

import argparse
import queue
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from app.io import elevate, ops, paths
from app.io.pool import WorkerPool
from app.io.protocol import (
    MENU_SEPARATOR,
    MENU_SUBMENU,
    PREVIEW_BOX,
    PREVIEW_TEXT_BYTES,
    Entry,
    MenuItem,
    Op,
    Preview,
    PreviewForm,
    Reply,
    Status,
    icon_key,
    own_icon_kind,
    preview_family,
)

_SETTLED = {Status.OK, Status.TIMEOUT, Status.CANCELLED,
            Status.DENIED, Status.GONE, Status.ERROR}

#: The path an ICON request carries. Empty keys the local worker, which is
#: where an association lookup belongs whatever volume the rows came from.
LOCAL = ""


@dataclass
class Outcome:
    """What one request did, in the terms worth reporting."""

    status: Status = Status.ERROR
    message: str = ""
    payload: Any = None
    rows: int = 0
    batches: int = 0
    first_batch: float | None = None
    elapsed: float = 0.0
    names: list[str] = field(default_factory=list)
    kinds: set = field(default_factory=set)


def _run(
    pool: WorkerPool,
    op: Op,
    path: str,
    *,
    timeout: float,
    keep_names: int = 0,
    keep_kinds: bool = False,
    cancel_after: int = 0,
    kill_after: int = 0,
    args: dict | None = None,
) -> Outcome:
    """Submit one request and block until it settles.

    Blocking is fine here and nowhere else: this process has no UI thread to
    protect. The interesting part is that it blocks on the reply, never on the
    filesystem — a hung share settles this call through the pool's watchdog
    rather than leaving it waiting.
    """
    replies: queue.Queue[Reply] = queue.Queue()
    started = time.monotonic()
    outcome = Outcome()
    request_id = pool.submit(op, path, timeout=timeout, args=args, handler=replies.put)

    while True:
        try:
            reply = replies.get(timeout=timeout * 4 + 30)
        except queue.Empty:
            outcome.status = Status.ERROR
            outcome.message = ("no reply and no watchdog action: this is a bug "
                               "in the pool, not a slow share")
            break

        if reply.status is Status.PARTIAL:
            rows = reply.payload or []
            outcome.batches += 1
            outcome.rows += len(rows)
            if outcome.first_batch is None:
                outcome.first_batch = time.monotonic() - started
            if keep_names:
                outcome.names.extend(e.name for e in rows[: keep_names - len(outcome.names)])
            if keep_kinds:
                outcome.kinds.update(icon_key(e) for e in rows)
            if cancel_after and outcome.rows >= cancel_after:
                cancel_after = 0
                pool.cancel(request_id)
            if kill_after and outcome.rows >= kill_after:
                kill_after = 0
                print(f"killing the worker for {paths.volume_key(path)} "
                      f"after {outcome.rows} rows", flush=True)
                pool.kill(path)
            continue

        outcome.status = reply.status
        outcome.message = reply.message
        outcome.payload = reply.payload
        if isinstance(reply.payload, list):
            outcome.rows += len(reply.payload)
            if outcome.first_batch is None:
                outcome.first_batch = time.monotonic() - started
            if keep_names:
                outcome.names.extend(
                    e.name for e in reply.payload[: keep_names - len(outcome.names)]
                )
            if keep_kinds:
                outcome.kinds.update(icon_key(e) for e in reply.payload)
        break

    outcome.elapsed = time.monotonic() - started
    return outcome


def _report(label: str, value: Any) -> None:
    print(f"{label:<14}{value}")


def _report_outcome(path: str, outcome: Outcome, *, rows: bool = True) -> None:
    _report("path", paths.normalize(path))
    _report("resolved", paths.resolve(path))
    _report("volume", paths.volume_key(path))
    if rows:
        _report("rows", outcome.rows)
        _report("batches", outcome.batches)
        first = outcome.first_batch
        _report("first batch", f"{first:.3f}s" if first is not None else "-")
    _report("elapsed", f"{outcome.elapsed:.3f}s")
    if rows and outcome.elapsed > 0 and outcome.rows:
        _report("rate", f"{outcome.rows / outcome.elapsed:,.0f} rows/s")
    _report("status", outcome.status.value)
    if outcome.message:
        _report("message", outcome.message)


def _exit_code(outcome: Outcome) -> int:
    return 0 if outcome.status is Status.OK else 1


# --------------------------------------------------------------------------
# Commands.
# --------------------------------------------------------------------------


def cmd_resolve(args: argparse.Namespace) -> int:
    _report("input", args.path)
    _report("normalized", paths.normalize(args.path))
    _report("resolved", paths.resolve(args.path))
    _report("volume", paths.volume_key(args.path))
    _report("as letter", paths.display(args.path, prefer_letter=True))
    _report("as unc", paths.display(args.path, prefer_letter=False))
    resolved = paths.resolve(args.path)
    _report("file call", paths.api(resolved))
    _report("length", f"{len(resolved)} characters"
                      + (f" -- past {paths.MAX_PATH}, so the prefix is what "
                         f"makes it reachable" if len(resolved) >= paths.MAX_PATH
                         else ""))
    return 0


def cmd_eject(args: argparse.Namespace) -> int:
    """Eject a USB drive, or with --check only say whether it could be.

    Directly, not through a worker: this is for trying the Windows call, and
    the harness has no panes standing on the drive to move off it first.
    """
    from app.io import eject

    letter = args.letter.rstrip("\\").upper()
    kind = paths.drive_type(letter)
    _report("type", kind)
    _report("ejectable", "yes" if eject.is_ejectable(letter, kind) else "no")
    if args.check:
        return 0
    outcome = eject.eject(letter)
    _report("result", outcome.message)
    return 0 if outcome.ok else 1


def cmd_space(args: argparse.Namespace) -> int:
    """Whether a transfer of a given size would be refused, before running one.

    The same call the queue makes, through the same function, so what this
    prints is what a copy would decide -- which is the only reason it is worth
    printing.
    """
    try:
        usage = shutil.disk_usage(paths.api(paths.resolve(args.path)))
    except (OSError, ValueError) as exc:
        print(f"{args.path}: {exc}")
        print("An unknown is not a refusal: a transfer here would proceed.")
        return 1
    _report("total", f"{usage.total:,} bytes")
    _report("used", f"{usage.used:,} bytes")
    _report("free", f"{usage.free:,} bytes")
    if not args.need:
        return 0
    short = ops.room_for(paths.resolve(args.path), args.need)
    if short is None:
        _report("verdict", f"{args.need:,} bytes would be written")
        return 0
    needed, free = short
    _report("verdict", f"refused: {needed:,} bytes to write, {free:,} free")
    return 1


def cmd_drives(args: argparse.Namespace) -> int:
    found = paths.drives(refresh=args.refresh)
    if not found:
        print(paths.win32_problem() or "no drive letters in this session")
        return 1
    for drive in found:
        print(f"{drive.letter:<5}{drive.type:<11}{drive.unc or ''}")
    print()
    print("Presence is not reachability: a mapping whose session has died "
          "still lists here.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    pool = WorkerPool()
    try:
        code = 0
        for pass_number in range(1, args.repeat + 1):
            if args.repeat > 1:
                print(f"--- pass {pass_number} of {args.repeat} ---")
            outcome = _run(
                pool, Op.LIST, args.path,
                timeout=args.timeout,
                keep_names=args.names,
                cancel_after=args.cancel_after,
                kill_after=args.kill_after,
            )
            _report_outcome(args.path, outcome)
            for name in outcome.names:
                print(f"  {name}")
            code = code or _exit_code(outcome)
        return code
    finally:
        pool.shutdown()


def cmd_stat(args: argparse.Namespace) -> int:
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.STAT, args.path, timeout=args.timeout)
        _report_outcome(args.path, outcome, rows=False)
        entry = outcome.payload
        if isinstance(entry, Entry):
            _report("name", entry.name)
            _report("kind", "directory" if entry.is_dir else "file")
            _report("size", f"{entry.size:,}")
            _report("modified", time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(entry.mtime)))
            _report("attributes", hex(entry.attributes))
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_dirsize(args: argparse.Namespace) -> int:
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.DIR_SIZE, args.path, timeout=args.timeout)
        _report_outcome(args.path, outcome, rows=False)
        totals = outcome.payload
        if isinstance(totals, dict):
            _report("bytes", f"{totals.get('bytes', 0):,}")
            _report("files", f"{totals.get('files', 0):,}")
            _report("folders", f"{totals.get('folders', 0):,}")
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_folders(args: argparse.Namespace) -> int:
    """What a breadcrumb chevron drops down, and what it cost.

    The number worth looking at is `more`: on a folder with more subfolders
    than the limit it must be true, and the request must come back in about
    the time the first batch of a listing takes rather than the time the whole
    listing takes. If it does not, the cap is not doing its job and a chevron
    on a job folder is an enumeration.
    """
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.FOLDERS, args.path, timeout=args.timeout,
                       args={"limit": args.limit})
        _report_outcome(args.path, outcome, rows=False)
        payload = outcome.payload
        if isinstance(payload, dict):
            names = payload.get("names") or []
            _report("folders", f"{len(names):,}")
            _report("more", "yes" if payload.get("more") else "no")
            for name in names[:args.names]:
                print(f"  {name}")
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_open(args: argparse.Namespace) -> int:
    """Hand a path to the shell, once, from the same code path the window uses.

    Worth having its own command: when a double click opens two copies of
    something, this says which half is at fault. One invocation here that opens
    one window means the shell call is right and the window is asking twice.
    """
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.OPEN, args.path, timeout=args.timeout,
                       args={"verb": args.verb} if args.verb else None)
        _report_outcome(args.path, outcome, rows=False)
        if isinstance(outcome.payload, dict):
            _report("opened", outcome.payload.get("path"))
            _report("verb", outcome.payload.get("verb") or "(default)")
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_network(args: argparse.Namespace) -> int:
    r"""What this session is attached to, letters or not.

    `drives` reports letters, and this reports connections -- which is the
    whole point: on a machine where the host drive arrives as `\\tsclient\C`
    with no letter at all, `drives` cannot see it and this can. Running the two
    side by side is the fastest way to see which kind of thing a share is.
    """
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.NETWORK, "", timeout=args.timeout)
        _report_outcome("", outcome, rows=False)
        payload = outcome.payload if isinstance(outcome.payload, dict) else {}
        entries = payload.get("connections", [])
        _report("connections", len(entries))
        for item in entries:
            letter = item.get("local") or "(no letter)"
            print(f"  {letter:<12} {item.get('remote', '')}"
                  f"    [{item.get('provider', '') or 'no provider named'}]")
        # An empty list and a failed call look identical from the outside and
        # mean entirely different things. On 15 September this said nothing
        # and a Hyper-V guest reported no connections; whether the call had
        # even worked was unanswerable. It answers now.
        problem = payload.get("problem", "")
        if problem:
            _report("problem", problem)
        elif not entries:
            _report("note", "the enumeration worked and this session holds "
                            "no network connections")
            _report("next", "tools\\diagnose_network.py asks every other "
                            "place Windows could be keeping one")
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_connect(args: argparse.Namespace) -> int:
    """Attach to a share again, from the same code path the rail's Reconnect
    uses. The one command here that is a network call by nature, so it is the
    one worth pointing at a server that has just come back.
    """
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.CONNECT, args.path, timeout=args.timeout,
                       args={"remember": args.remember})
        _report_outcome(args.path, outcome, rows=False)
        if outcome.status is Status.OK:
            _report("connected", args.path)
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_run(args: argparse.Namespace) -> int:
    """Expand one command from the table and start it, saying what it found.

    The half of this feature that cannot be tested off Windows, in the one form
    that answers it without a window: `program` says which of the candidates
    was found and where, which is the only question a compare tool that "does
    not work" is ever asking. A machine without Beyond Compare answers with the
    sentence the status line would have shown.

    `--dry-run` stops before anything starts and prints the argument vector.
    Worth having because the vector is what a mis-quoted template gets wrong,
    and reading it beside the template is faster than watching a program open
    the wrong file.
    """
    from app.core import commands as table

    known = table.with_defaults(table.DEFAULTS)
    command = table.find(known, args.command)
    if command is None:
        _report("no such command", args.command)
        _report("known", ", ".join(item.id for item in known))
        return 2

    context = table.Context(
        path=args.path,
        other_path=args.other,
        name=args.name,
        names=tuple(args.mark or ()),
    )
    why = table.refusal(command, context)
    if why:
        _report("refused", why)
        return 2

    launch = table.expand(command, context)
    _report("program", launch.program)
    if launch.alternatives:
        _report("or", ", ".join(launch.alternatives))
    _report("arguments", " | ".join(launch.arguments) or "(none)")
    _report("start in", launch.working or "(the worker's own)")
    if args.dry_run:
        return 0

    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.RUN, args.path or launch.working,
                       timeout=args.timeout,
                       args={"program": launch.program,
                             "alternatives": list(launch.alternatives),
                             "arguments": list(launch.arguments),
                             "working": launch.working,
                             "list": list(launch.list_names)})
        _report_outcome(args.path, outcome, rows=False)
        if isinstance(outcome.payload, dict):
            _report("found", outcome.payload.get("program"))
            _report("pid", outcome.payload.get("pid"))
            if outcome.payload.get("list"):
                _report("list file", outcome.payload["list"])
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_icons(args: argparse.Namespace) -> int:
    """List a folder, work out the kinds in it, and ask the shell for each.

    Two commands in one because the interesting number is the ratio: a folder
    of 50,000 rows holding thirty distinct extensions is thirty association
    lookups, and if this reports otherwise then something is keying on the row
    rather than the kind.

    The other thing it says is which kinds came back empty. An extension the
    shell has no icon for is not a failure -- the listing shows the generic
    file icon for it -- but a run where nothing at all came back is pywin32
    missing or the shell refusing, and that is worth telling apart from a
    quiet afternoon.
    """
    pool = WorkerPool()
    try:
        listing = _run(pool, Op.LIST, args.path, timeout=args.timeout, keep_kinds=True)
        _report_outcome(args.path, listing)
        if listing.status is not Status.OK and not listing.kinds:
            return _exit_code(listing)

        keys = sorted(listing.kinds)
        _report("kinds", f"{len(keys)} distinct in {listing.rows:,} rows")
        icons = _run(pool, Op.ICON, LOCAL, timeout=args.icon_timeout,
                     args={"keys": keys, "size": args.size})
        payload = icons.payload if isinstance(icons.payload, dict) else {}
        drawn = payload.get("icons") or {}
        size = payload.get("size", args.size)
        _report("icon status", icons.status.value)
        if icons.message:
            _report("message", icons.message)
        _report("size", f"{size}x{size}")
        _report("with icons", f"{len(drawn)} of {len(keys)}")
        _report("elapsed", f"{icons.elapsed:.3f}s")

        expected = int(size) * int(size) * 4
        for key in keys:
            pixels = drawn.get(key)
            if pixels is None:
                print(f"  {key:<12} -")
                continue
            # The alpha bytes are the check that matters. An icon that came
            # back the right length but fully transparent is the failure this
            # is most likely to have: bytes arrived, and nothing draws.
            visible = sum(1 for value in pixels[3::4] if value)
            size_note = "" if len(pixels) == expected else f"  (wrong length {len(pixels)})"
            print(f"  {key:<12} {visible:>5} of {len(pixels) // 4} pixels{size_note}")
        return _exit_code(icons)
    finally:
        pool.shutdown()


def cmd_menu(args: argparse.Namespace) -> int:
    """Build the Explorer context menu for a selection and print it.

    The command that answers the question this feature exists for: are
    TortoiseSVN and 7-Zip actually in there. It prints the tree the window
    would draw, with each entry's verb, so an entry that appears with no text
    can still be told apart from one that is missing.

    With `--invoke` it runs one of them, by the id printed beside it. That is
    a real invocation of somebody's shell extension: it can open a dialog, and
    it can change files.
    """
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.MENU, args.path, timeout=args.timeout,
                       args={"names": args.names, "extended": args.extended})
        _report("path", paths.normalize(args.path))
        _report("names", len(args.names) or "the folder itself")
        _report("status", outcome.status.value)
        _report("elapsed", f"{outcome.elapsed:.3f}s")
        if outcome.message:
            _report("message", outcome.message)
        payload = outcome.payload if isinstance(outcome.payload, dict) else {}
        items = list(payload.get("items") or ())
        token = payload.get("token")
        if outcome.status is not Status.OK:
            return _exit_code(outcome)
        _report("token", token)
        _report("built", f"{payload.get('positions', '?')} entries at the top level, "
                         f"before anything was read")
        _report("entries", _count_items(items))
        _print_items(items, indent=2)
        # What did not make it, and why. An entry that never arrives is
        # invisible from the window, so a short menu and a menu this file
        # mangled look the same from there.
        dropped = list(payload.get("skipped") or ())
        if dropped:
            print(f"\nskipped {len(dropped)}:")
            for note in dropped:
                print(f"  {note}")

        if not args.invoke:
            release = _run(pool, Op.MENU_RELEASE, args.path, timeout=args.timeout,
                           args={"token": token})
            _report("released", release.status.value)
            return _exit_code(outcome)

        print(f"\ninvoking command {args.invoke}")
        ran = _run(pool, Op.MENU_INVOKE, args.path, timeout=args.invoke_timeout,
                   args={"token": token, "item": args.invoke})
        _report("status", ran.status.value)
        if isinstance(ran.payload, dict):
            _report("verb", ran.payload.get("verb") or "-")
        if ran.message:
            _report("message", ran.message)
        return _exit_code(ran)
    finally:
        pool.shutdown()


def _count_items(items) -> int:
    total = 0
    for item in items:
        if item.kind == MENU_SEPARATOR:
            continue
        total += 1 + _count_items(item.items)
    return total


def _print_items(items, *, indent: int) -> None:
    """The menu as a tree, with the ids an invoke takes."""
    pad = " " * indent
    for item in items:
        if not isinstance(item, MenuItem):
            continue
        if item.kind == MENU_SEPARATOR:
            print(f"{pad}{'-' * 20}")
            continue
        marks = "".join((
            "" if item.enabled else " [disabled]",
            " [checked]" if item.checked else "",
            " [default]" if item.default else "",
            " [icon]" if item.icon else "",
        ))
        if item.kind == MENU_SUBMENU:
            print(f"{pad}{item.text}{marks}")
            _print_items(item.items, indent=indent + 2)
            continue
        verb = f"  ({item.verb})" if item.verb else ""
        print(f"{pad}{item.id:>5}  {item.text}{verb}{marks}")


def cmd_overlays(args: argparse.Namespace) -> int:
    """List a folder and ask the shell which of its rows carry a badge.

    Two commands in one, like `icons`, and for the opposite reason: the ratio
    to watch here is how many *pictures* come back against how many rows were
    asked about. One image for forty badged files is the design working. One
    image per file means the key is wrong and this is as expensive as the
    thing `CLAUDE.md` says not to build.
    """
    pool = WorkerPool()
    try:
        listing = _run(pool, Op.LIST, args.path, timeout=args.timeout,
                       keep_names=args.rows)
        _report_outcome(args.path, listing)
        names = listing.names[: args.rows]
        if not names:
            _report("rows asked", 0)
            return _exit_code(listing)

        overlays = _run(pool, Op.OVERLAY, args.path, timeout=args.overlay_timeout,
                        args={"names": names, "size": args.size})
        payload = overlays.payload if isinstance(overlays.payload, dict) else {}
        rows = payload.get("rows") or {}
        images = payload.get("images") or {}
        _report("rows asked", len(names))
        _report("overlay", overlays.status.value)
        if overlays.message:
            _report("message", overlays.message)
        _report("badged", f"{len(rows)} of {len(names)}")
        _report("images", f"{len(images)} distinct")
        _report("elapsed", f"{overlays.elapsed:.3f}s")
        for name in names:
            key = rows.get(name)
            if not key:
                continue
            pixels = images.get(key)
            drawn = (f"{sum(1 for value in pixels[3::4] if value)} visible pixels"
                     if pixels else "no image")
            print(f"  {name:<40} {key:<12} {drawn}")
        return _exit_code(overlays)
    finally:
        pool.shutdown()


def cmd_fileicons(args: argparse.Namespace) -> int:
    """List a folder and read the icon out of every file that carries one.

    The number to watch is the first one printed: how many of the rows are a
    kind that could carry its own icon at all. In a folder of drawings or
    documents it is zero, and zero is the design working -- nothing is opened,
    and the listing draws by kind exactly as it did before this existed. In a
    Start-menu folder it is all of them, which is the case worth timing over a
    share.

    The second number is how many distinct pictures came back against how many
    files answered. Forty shortcuts to the same program should be one image;
    one image per file means the digest is not doing its job and this costs
    what a naive version would.
    """
    pool = WorkerPool()
    try:
        listing = _run(pool, Op.LIST, args.path, timeout=args.timeout,
                       keep_names=args.rows)
        _report_outcome(args.path, listing)
        names = listing.names[: args.rows]
        wanted = [name for name in names if own_icon_kind(name)]
        _report("rows read", len(names))
        _report("own icons", f"{len(wanted)} of {len(names)} rows")
        if not wanted:
            return _exit_code(listing)

        icons = _run(pool, Op.FILE_ICON, args.path, timeout=args.icon_timeout,
                     args={"names": wanted, "size": args.size})
        payload = icons.payload if isinstance(icons.payload, dict) else {}
        rows = payload.get("rows") or {}
        images = payload.get("images") or {}
        _report("status", icons.status.value)
        if icons.message:
            _report("message", icons.message)
        _report("answered", f"{len(rows)} of {len(wanted)}")
        _report("images", f"{len(images)} distinct")
        _report("elapsed", f"{icons.elapsed:.3f}s")
        for name in wanted:
            key = rows.get(name)
            if not key:
                print(f"  {name:<40} -")
                continue
            pixels = images.get(key)
            # The alpha bytes again: a picture that arrived the right length
            # and fully transparent is the failure that looks like success.
            drawn = (f"{sum(1 for value in pixels[3::4] if value)} visible pixels"
                     if pixels else "no image")
            print(f"  {name:<40} {key:<16} {drawn}")
        return _exit_code(icons)
    finally:
        pool.shutdown()


def cmd_preview(args: argparse.Namespace) -> int:
    """Decode one file and say what came out of it, without drawing anything.

    The only way to exercise the decoder against a real file on a real share,
    and the numbers worth watching are the last three.

    **Which rung answered** (`source`) is the first thing to read: `qt` means
    Qt's own plugins, `raw` the JPEG inside a camera file, `shell` a thumbnail
    provider Windows already had, `text` and `hex` the floor. A `.heic` that
    comes back `hex` is a missing codec pack on this machine rather than a
    fault here, and this line is how the two are told apart.

    **`decoded` against `natural`** is what the scaling is for. A 6,000 pixel
    photograph asked for at 400 must report `natural 6000x4000` and `decoded
    400`, and the bytes must be tens of kilobytes rather than megabytes -- if
    the two sizes agree on a large picture then `setScaledSize` is not being
    honoured and every preview is carrying the whole file across the process
    boundary.

    **`elapsed`** is the one to take to a share. A local JPEG is single-digit
    milliseconds; the same file over SMB is the read, and that is the cost this
    whole feature is bounded against.

    `--write` saves what came back, which is the only way to find out whether
    the picture is actually of the file rather than of something else -- a
    thumbnail handler that answers with a generic page icon is an answer, and
    it looks like a working preview from here.
    """
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.PREVIEW, args.path, timeout=args.timeout,
                       args={"box": args.box, "text_bytes": args.text_bytes,
                             "shell": not args.no_shell})
        _report_outcome(args.path, outcome, rows=False)
        answer = outcome.payload
        if not isinstance(answer, Preview):
            _report("preview", "nothing came back that is a preview")
            return _exit_code(outcome)

        _report("form", answer.form.value)
        _report("source", answer.source or "-")
        _report("file size", f"{answer.size:,} bytes")
        if answer.form is PreviewForm.IMAGE:
            _report("natural", f"{answer.width} x {answer.height}")
            _report("decoded", f"{answer.shown} px on the long edge")
            _report("png", f"{len(answer.image or b''):,} bytes")
            if answer.pages:
                _report("pages", answer.pages)
        elif answer.form is PreviewForm.TEXT:
            _report("encoding", answer.encoding)
            _report("lines", f"{answer.lines:,}"
                             f"{' (and more)' if answer.truncated else ''}")
            for line in answer.text.splitlines()[: args.lines]:
                print(f"  {line[:110]}")
        elif answer.form is PreviewForm.HEX:
            data = answer.data or b""
            _report("bytes", f"{len(data):,} read")
            for at in range(0, min(len(data), args.lines * 16), 16):
                chunk = data[at:at + 16]
                shown = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                print(f"  {at:08x}  {chunk.hex(' '):<47}  {shown}")
        if answer.note:
            _report("note", answer.note)

        if args.write and answer.form is PreviewForm.IMAGE and answer.image:
            with open(args.write, "wb") as handle:
                handle.write(answer.image)
            _report("written", args.write)
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_thumbnails(args: argparse.Namespace) -> int:
    """List a folder and make a thumbnail for every row that could have one.

    The grid's cost, measured. Three numbers, and the first is the bound: how
    many rows are a kind anything could draw at all. In a folder of text files
    it is zero and zero is the design working -- nothing is opened and the grid
    draws icons, exactly as the listing does. In a folder of photographs it is
    all of them, which is the case worth timing over a share and the reason
    this command exists.

    The second is distinct pictures against files answered. A folder holding
    forty copies of one drawing should come back as one image; one image per
    file means the digest is not doing its job and every cell is carrying its
    own copy.

    The third is `per file`, which is what decides whether the grid is usable.
    A screenful is forty to ninety cells, so anything much past twenty
    milliseconds a file is a grid that fills in visibly rather than appearing.
    """
    pool = WorkerPool()
    try:
        listing = _run(pool, Op.LIST, args.path, timeout=args.timeout,
                       keep_names=args.rows)
        _report_outcome(args.path, listing)
        names = listing.names[: args.rows]
        wanted = [name for name in names
                  if preview_family(name) in ("image", "raw", "shell")]
        _report("rows read", len(names))
        _report("thumbnails", f"{len(wanted)} of {len(names)} rows could draw one")
        if not wanted:
            return _exit_code(listing)

        thumbs = _run(pool, Op.THUMBNAIL, args.path, timeout=args.thumb_timeout,
                      args={"names": wanted, "size": args.size,
                            "shell": not args.no_shell})
        payload = thumbs.payload if isinstance(thumbs.payload, dict) else {}
        rows = payload.get("rows") or {}
        images = payload.get("images") or {}
        _report("status", thumbs.status.value)
        if thumbs.message:
            _report("message", thumbs.message)
        _report("answered", f"{len(rows)} of {len(wanted)}")
        _report("images", f"{len(images)} distinct")
        _report("bytes", f"{sum(len(v) for v in images.values()):,} in total")
        _report("elapsed", f"{thumbs.elapsed:.3f}s")
        if wanted:
            _report("per file", f"{thumbs.elapsed / len(wanted) * 1000:.1f} ms")
        for name in wanted:
            key = rows.get(name)
            if not key:
                print(f"  {name:<44} -")
                continue
            print(f"  {name:<44} {key:<16} {len(images.get(key) or b''):,} bytes")
        return _exit_code(thumbs)
    finally:
        pool.shutdown()


def cmd_elevate(args: argparse.Namespace) -> int:
    """Run one operation with administrator rights, prompt and all.

    The only way to exercise the elevation path without the window, and worth
    running from a console at least once: it is the one place in this
    application where a second process is started, and the failure that
    matters -- the consent prompt being declined -- has to come back as a
    refusal rather than as a hang.
    """
    plan: dict[str, Any] = {"action": args.action, "path": args.path, "args": {}}
    if args.action == "rename":
        if not args.name:
            print("rename needs --name")
            return 2
        plan["args"] = {"name": args.name}
    elif args.action == "delete":
        if not args.names:
            print("delete needs one or more names")
            return 2
        plan["args"] = {"names": args.names, "permanent": args.permanent}
    print(f"asking Windows to {elevate.describe(plan)}")

    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.ELEVATE, args.path, timeout=args.timeout,
                       args={"plan": plan})
        _report_outcome(args.path, outcome, rows=False)
        if isinstance(outcome.payload, dict):
            for key, value in outcome.payload.items():
                _report(key, value)
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_mkdir(args: argparse.Namespace) -> int:
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.MKDIR, args.path, timeout=args.timeout)
        _report_outcome(args.path, outcome, rows=False)
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_rename(args: argparse.Namespace) -> int:
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.RENAME, args.path, timeout=args.timeout,
                       args={"name": args.name})
        _report_outcome(args.path, outcome, rows=False)
        if isinstance(outcome.payload, dict):
            _report("now", outcome.payload.get("path"))
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_delete(args: argparse.Namespace) -> int:
    """Delete for real, from a console, so the Recycle Bin can be checked.

    It prints what it is about to do and where it will go first. This command
    is the one place in the harness that changes something the user would miss,
    and a line saying which of the two kinds of delete is about to happen is
    cheap next to that.
    """
    kind = "permanently" if args.permanent else "to the Recycle Bin"
    print(f"deleting {len(args.names)} item(s) from {args.path} {kind}")
    for name in args.names:
        print(f"  {name}")
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.DELETE, args.path, timeout=args.timeout,
                       args={"names": args.names, "permanent": args.permanent})
        _report_outcome(args.path, outcome, rows=False)
        if isinstance(outcome.payload, dict):
            _report("deleted", outcome.payload.get("deleted"))
            _report("recoverable", "no" if outcome.payload.get("permanent") else "yes")
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_transfer(args: argparse.Namespace) -> int:
    """Run a real job and print what the window would be showing.

    The whole engine, minus the window: the queue, the scan, the conflict rule,
    pause and cancel. A 50,000-file copy over SMB can be watched here, and a
    cancel part way through can be seen to settle, without a UI existing.

    All four kinds go through here, which is the point of it being one command
    rather than four. `queue erase` against a folder of 30,000 files on a share
    is the one worth running: a delete is now a job, and the thing to see is
    that it reports as it goes and that Ctrl+C leaves the rest of the tree
    alone.
    """
    import queue as _queue

    from app.io.ops import Transfers
    from app.io.protocol import Conflict, Event, Progress, JobKind

    events: "_queue.Queue[Event]" = _queue.Queue()
    transfers = Transfers(events.put)
    kind = JobKind(args.kind)
    conflict = Conflict(args.conflict)
    destination = getattr(args, "destination", "") or ""

    print(f"{kind.value} {len(args.sources)} source(s)"
          + (f" -> {destination}" if destination else ""))
    if kind.asks:
        print(f"conflicts: {conflict.value}")
    started = time.monotonic()
    job = transfers.submit(kind, args.sources, destination, conflict=conflict)
    cancelled = False
    code = 0

    try:
        while True:
            try:
                event = events.get(timeout=args.timeout)
            except _queue.Empty:
                print("no event within the timeout; the transfer is not reporting")
                code = 1
                break
            if event.kind is Progress.SCANNED:
                verb = "to remove" if kind.removes else "to move"
                print(f"  {verb}: {event.payload['files']:,} files, "
                      f"{event.payload['bytes']:,} bytes")
            elif event.kind is Progress.COPYING:
                done = event.payload.get("done", 0)
                total = event.payload.get("total", 0) or 1
                print(f"\r  {done * 100 // total:3d}%  {event.payload.get('name', '')[:48]:<48}",
                      end="", flush=True)
                if args.cancel_after and done >= args.cancel_after and not cancelled:
                    cancelled = True
                    print("\n  cancelling")
                    transfers.cancel(job)
            elif event.kind is Progress.CONFLICT:
                # The harness never answers interactively: a console prompt in
                # the middle of a 50,000-file copy is not a test, it is a trap.
                print(f"\n  conflict on {event.payload.get('name')}; "
                      f"answering {args.on_conflict}")
                transfers.answer(job, Conflict(args.on_conflict), apply_to_all=True)
            elif event.kind is Progress.REMOVING:
                done = event.payload.get("done", 0)
                total = event.payload.get("total", 0) or 1
                if not event.payload.get("interruptible", True):
                    print("  the shell is deleting these; it cannot be "
                          "interrupted or reported on")
                    continue
                print(f"\r  {done * 100 // total:3d}%  {done:,} of {total:,}  "
                      f"{str(event.payload.get('name', ''))[:40]:<40}",
                      end="", flush=True)
                if args.cancel_after and done >= args.cancel_after and not cancelled:
                    cancelled = True
                    print("\n  cancelling")
                    transfers.cancel(job)
            elif event.kind is Progress.FAILED_ITEM:
                denied = " (refused, not failed)" if event.payload.get("denied") else ""
                print(f"\n  failed{denied}: {event.payload.get('name')}: {event.message}")
            elif event.kind is Progress.DONE:
                elapsed = time.monotonic() - started
                print()
                _report("removed" if kind.removes else "copied",
                        event.payload.get("copied", 0))
                _report("skipped", event.payload.get("skipped", 0))
                _report("failed", event.payload.get("failed", 0))
                _report("bytes", f"{event.payload.get('bytes', 0):,}")
                _report("cancelled", "yes" if event.payload.get("cancelled") else "no")
                _report("elapsed", f"{elapsed:.3f}s")
                if event.message:
                    _report("message", event.message)
                code = 1 if event.payload.get("failed") else 0
                break
        return code
    finally:
        transfers.shutdown()


def cmd_ping(args: argparse.Namespace) -> int:
    pool = WorkerPool()
    try:
        outcome = _run(pool, Op.PING, args.path, timeout=args.timeout)
        _report_outcome(args.path, outcome, rows=False)
        if isinstance(outcome.payload, dict):
            _report("worker pid", outcome.payload.get("pid"))
        return _exit_code(outcome)
    finally:
        pool.shutdown()


def cmd_stall(args: argparse.Namespace) -> int:
    """Wedge a worker on purpose and check that the pool copes.

    This is the acceptance test for the whole architecture, and until there is
    a share to yank it is the only way to run it on Windows. Four things have
    to hold, and the command fails if any of them does not:

      * the wedged request comes back TIMEOUT rather than never coming back;
      * a request queued behind it settles too, rather than waiting on a worker
        that is never going to read it;
      * both settle in about the deadline, not minutes later;
      * the volume works immediately afterwards, on a new process.
    """
    pool = WorkerPool()
    checks: list[tuple[str, bool, str]] = []
    try:
        warmup = _run(pool, Op.PING, args.path, timeout=5)
        if warmup.status is not Status.OK:
            _report_outcome(args.path, warmup, rows=False)
            print("\ncould not reach a worker for this path at all")
            return 1
        before = warmup.payload.get("pid")

        wedged: queue.Queue[Reply] = queue.Queue()
        started = time.monotonic()
        pool.submit(Op.STALL, args.path, timeout=args.timeout, handler=wedged.put)
        time.sleep(0.25)  # let the worker pick it up before queueing behind it

        behind: queue.Queue[Reply] = queue.Queue()
        pool.submit(Op.LIST, args.path, timeout=args.timeout, handler=behind.put)

        wait = args.timeout * 4 + 30
        stalled = _settle(wedged, wait)
        queued = _settle(behind, wait)
        elapsed = time.monotonic() - started

        recovery = _run(pool, Op.PING, args.path, timeout=5)
        after = recovery.payload.get("pid") if recovery.status is Status.OK else None

        _report("worker pid", before)
        _report("stalled", f"{stalled.status.value} after {elapsed:.2f}s")
        _report("queued behind", queued.status.value)
        _report("after restart", f"{recovery.status.value}, worker pid {after}")

        checks = [
            ("the wedged request settled as a timeout",
             stalled.status is Status.TIMEOUT, stalled.status.value),
            ("the request queued behind it settled too",
             queued.status in _SETTLED, queued.status.value),
            ("both settled near the deadline, not minutes later",
             elapsed < args.timeout + 10, f"{elapsed:.2f}s"),
            ("the volume works again straight afterwards",
             recovery.status is Status.OK, recovery.status.value),
            ("on a new process",
             after is not None and after != before, f"{before} -> {after}"),
        ]
        print()
        for label, passed, detail in checks:
            print(f"{'pass' if passed else 'FAIL':<6}{label} ({detail})")
        return 0 if all(passed for _, passed, _ in checks) else 1
    finally:
        pool.shutdown()


def _settle(replies: "queue.Queue[Reply]", wait: float) -> Reply:
    """Wait for the one reply that ends a request, discarding streamed batches."""
    deadline = time.monotonic() + wait
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return Reply(0, Status.ERROR, message="nothing settled; the request hung")
        try:
            reply = replies.get(timeout=remaining)
        except queue.Empty:
            return Reply(0, Status.ERROR, message="nothing settled; the request hung")
        if reply.status is not Status.PARTIAL:
            return reply


def cmd_soak(args: argparse.Namespace) -> int:
    """Repeat a listing on one pool, which is the yank-the-cable test.

    The pool is kept across passes on purpose: what is being watched is not the
    listing, it is what the pool does to the worker when the share goes away
    and what it does when it comes back.
    """
    pool = WorkerPool()
    failures = 0
    try:
        for pass_number in range(1, args.count + 1):
            outcome = _run(pool, Op.LIST, args.path, timeout=args.timeout)
            stamp = time.strftime("%H:%M:%S")
            first = f"{outcome.first_batch:.2f}s" if outcome.first_batch is not None else "-"
            print(f"{stamp}  {pass_number:>4}  {outcome.status.value:<10}"
                  f"{outcome.elapsed:>8.2f}s  first {first:<8}"
                  f"{outcome.rows:>8,} rows  {outcome.message}", flush=True)
            if outcome.status is not Status.OK:
                failures += 1
                if args.retry:
                    pool.retry(args.path)
            if pass_number < args.count:
                time.sleep(args.interval)
        print()
        print(f"{args.count} passes, {failures} not OK")
        for key, state in pool.status().items():
            print(f"worker {key}: {state}")
        return 1 if failures else 0
    finally:
        pool.shutdown()


# --------------------------------------------------------------------------


def _conflict_values():
    from app.io.protocol import Conflict
    return list(Conflict)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.io.harness",
        description="Exercise the io layer without a UI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def with_path(name: str, help_text: str, *, timeout: float = 20.0):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("path")
        cmd.add_argument("--timeout", type=float, default=timeout,
                         help="seconds without progress before the request is "
                              "given up on and the worker restarted")
        return cmd

    resolve = sub.add_parser("resolve", help="show how a path is resolved and keyed")
    resolve.add_argument("path")
    resolve.set_defaults(func=cmd_resolve)

    space = sub.add_parser(
        "space", help="what a volume has left, and whether a transfer would fit")
    space.add_argument("path", help="the destination folder")
    space.add_argument("--need", type=int, default=0, metavar="BYTES",
                       help="bytes a job would write; prints the verdict the "
                            "queue would reach before starting one")
    space.set_defaults(func=cmd_space)

    ejecting = sub.add_parser("eject", help="eject a USB drive the way Explorer does")
    ejecting.add_argument("letter", help="E: or similar")
    ejecting.add_argument("--check", action="store_true",
                          help="only say whether the drive counts as ejectable")
    ejecting.set_defaults(func=cmd_eject)

    drives = sub.add_parser("drives", help="enumerate drive letters without probing them")
    drives.add_argument("--refresh", action="store_true",
                        help="re-read the session table instead of the cache")
    drives.set_defaults(func=cmd_drives)

    listing = with_path("list", "stream a directory listing and time it")
    listing.add_argument("--names", type=int, default=0, metavar="N",
                         help="print the first N names")
    listing.add_argument("--repeat", type=int, default=1, metavar="N")
    listing.add_argument("--cancel-after", type=int, default=0, metavar="ROWS",
                         help="cancel the listing once this many rows have arrived")
    listing.add_argument("--kill-after", type=int, default=0, metavar="ROWS",
                         help="kill the worker once this many rows have arrived; "
                              "the request must settle, not hang")
    listing.set_defaults(func=cmd_list)

    folders = with_path("folders", "the subfolders of one folder, capped",
                        timeout=8.0)
    folders.add_argument("--limit", type=int, default=200, metavar="N",
                         help="stop scanning after this many subfolders")
    folders.add_argument("--names", type=int, default=0, metavar="N",
                         help="print the first N names")
    folders.set_defaults(func=cmd_folders)

    opening = with_path("open", "hand a path to the shell, exactly once", timeout=30.0)
    opening.add_argument("--verb", default="",
                         help="edit, print, properties; empty means the default "
                              "verb, which is not the same as open")
    opening.set_defaults(func=cmd_open)

    network = sub.add_parser(
        "network", help="what this session is attached to, letters or not")
    network.add_argument("--timeout", type=float, default=10.0)
    network.set_defaults(func=cmd_network)

    connecting = with_path("connect", "attach to a share again", timeout=45.0)
    connecting.add_argument("--remember", action="store_true",
                            help="keep the connection across logons")
    connecting.set_defaults(func=cmd_connect)

    running = with_path("run", "expand one external command and start it",
                        timeout=15.0)
    running.add_argument("command",
                         help="the id from the table: terminal, edit, compare")
    running.add_argument("--other", default="",
                         help="what %%T means: the other pane's folder")
    running.add_argument("--name", default="",
                         help="what %%N and %%F mean: the row under the cursor")
    running.add_argument("--mark", nargs="*", default=[],
                         help="what %%S means: the marked names in the folder")
    running.add_argument("--dry-run", action="store_true",
                         help="print the argument vector and start nothing")
    running.set_defaults(func=cmd_run)

    with_path("mkdir", "create one folder").set_defaults(func=cmd_mkdir)

    icons = with_path("icons", "shell icons for every kind in a folder")
    icons.add_argument("--size", type=int, default=16, choices=[16, 32],
                       help="16 for a normal display, 32 for a scaled one")
    icons.add_argument("--icon-timeout", type=float, default=15.0,
                       help="seconds for the whole batch of lookups")
    icons.set_defaults(func=cmd_icons)

    menu = with_path("menu", "the Explorer context menu for a selection")
    menu.add_argument("names", nargs="*",
                      help="names within the folder; none means the folder itself")
    menu.add_argument("--extended", action="store_true",
                      help="the entries Explorer hides behind Shift")
    menu.add_argument("--invoke", type=int, default=0, metavar="ID",
                      help="run the command with this id; this is real, and "
                           "the extension may open a dialog or change files")
    menu.add_argument("--invoke-timeout", type=float, default=600.0,
                      help="seconds to wait for a command that opens a dialog")
    menu.set_defaults(func=cmd_menu)

    overlays = with_path("overlays", "which rows carry a shell badge, and what it costs")
    overlays.add_argument("--rows", type=int, default=40, metavar="N",
                          help="how many rows to ask about, as a screenful would")
    overlays.add_argument("--size", type=int, default=16, choices=[16, 32])
    overlays.add_argument("--overlay-timeout", type=float, default=8.0)
    overlays.set_defaults(func=cmd_overlays)

    own = with_path("fileicons", "the icons files carry themselves, and what "
                                 "reading them costs")
    own.add_argument("--rows", type=int, default=200, metavar="N",
                     help="how many rows of the folder to consider")
    own.add_argument("--size", type=int, default=16, choices=[16, 32])
    own.add_argument("--icon-timeout", type=float, default=8.0)
    own.set_defaults(func=cmd_fileicons)

    # A file, not a folder, so the help says path rather than inheriting the
    # wording every other command uses.
    seeing = with_path("preview", "decode one file and say what came out",
                       timeout=10.0)
    seeing.add_argument("--box", type=int, default=PREVIEW_BOX, metavar="PX",
                        help="longest edge wanted from a picture")
    seeing.add_argument("--text-bytes", type=int, default=PREVIEW_TEXT_BYTES,
                        metavar="N", help="how much of a text file to read")
    seeing.add_argument("--lines", type=int, default=12, metavar="N",
                        help="how many lines of text, or rows of hex, to print")
    seeing.add_argument("--write", default="", metavar="FILE",
                        help="save the picture that came back, to look at it")
    seeing.add_argument("--no-shell", action="store_true",
                        help="skip the Windows thumbnail handler, to see what "
                             "this application can decode on its own")
    seeing.set_defaults(func=cmd_preview)

    grid = with_path("thumbnails", "thumbnails for a folder, and what the grid costs")
    grid.add_argument("--rows", type=int, default=60, metavar="N",
                      help="how many rows to ask about, as a screenful would")
    grid.add_argument("--size", type=int, default=128, metavar="PX")
    grid.add_argument("--thumb-timeout", type=float, default=20.0)
    grid.add_argument("--no-shell", action="store_true",
                      help="skip the Windows thumbnail handler")
    grid.set_defaults(func=cmd_thumbnails)

    # Not `with_path`: the action reads better in front of the path, and the
    # order of positionals is the order they are added.
    elevating = sub.add_parser("elevate", help="run one operation as administrator")
    elevating.add_argument("action", choices=sorted(elevate.ACTIONS))
    elevating.add_argument("path")
    elevating.add_argument("names", nargs="*", help="for delete: names in the folder")
    elevating.add_argument("--timeout", type=float, default=300.0,
                           help="seconds for the operation itself; the wait for "
                                "the consent prompt is added to it")
    elevating.add_argument("--name", default="", help="for rename: the new bare name")
    elevating.add_argument("--permanent", action="store_true",
                           help="for delete: skip the Recycle Bin")
    elevating.set_defaults(func=cmd_elevate)

    renaming = with_path("rename", "rename in place; will not overwrite")
    renaming.add_argument("name", help="the new bare name, not a path")
    renaming.set_defaults(func=cmd_rename)

    deleting = with_path("delete", "delete for real; recycles unless told not to",
                         timeout=300.0)
    deleting.add_argument("names", nargs="+", help="names within the folder")
    deleting.add_argument("--permanent", action="store_true",
                          help="skip the Recycle Bin; there is no undo for this")
    deleting.set_defaults(func=cmd_delete)

    for name, help_text in (
            ("copy", "copy for real, with progress"),
            ("move", "move for real, with progress"),
            ("recycle", "delete to the Recycle Bin, as a queued job"),
            ("erase", "delete permanently, item by item, as a queued job")):
        transfer = sub.add_parser(name, help=help_text)
        transfer.add_argument("sources", nargs="+")
        if name in ("copy", "move"):
            transfer.add_argument("destination")
        transfer.add_argument("--conflict", default="ask",
                              choices=[c.value for c in _conflict_values()],
                              help="what to do when a name is taken")
        transfer.add_argument("--on-conflict", default="skip",
                              choices=[c.value for c in _conflict_values() if c.value != "ask"],
                              help="what this harness answers when asked, "
                                   "applied to the rest of the job")
        transfer.add_argument(
            "--cancel-after", type=int, default=0,
            metavar="BYTES" if name in ("copy", "move") else "ITEMS",
            help="cancel once this much has been done")
        transfer.add_argument("--timeout", type=float, default=120.0,
                              help="seconds to wait for the next event")
        transfer.set_defaults(func=cmd_transfer, kind=name)

    with_path("stat", "one entry").set_defaults(func=cmd_stat)
    with_path("dirsize", "recursive size of a folder", timeout=120.0).set_defaults(func=cmd_dirsize)
    with_path("ping", "round trip to the worker for a volume", timeout=5.0).set_defaults(func=cmd_ping)

    stall = with_path("stall", "wedge a worker on purpose and check the recovery",
                      timeout=3.0)
    stall.set_defaults(func=cmd_stall)

    soak = with_path("soak", "list repeatedly on one pool, for pulling the cable")
    soak.add_argument("--count", type=int, default=20)
    soak.add_argument("--interval", type=float, default=2.0, metavar="SECONDS")
    soak.add_argument("--retry", action="store_true",
                      help="clear the unreachable mark after a failed pass")
    soak.set_defaults(func=cmd_soak)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    problem = paths.win32_problem()
    if problem:
        # Loud, on every command, because the degraded behaviour it describes
        # looks like working software until a share goes away.
        print(f"warning: {problem}\n", file=sys.stderr, flush=True)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
