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
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from app.io import paths
from app.io.pool import WorkerPool
from app.io.protocol import Entry, Op, Reply, Status

_SETTLED = {Status.OK, Status.TIMEOUT, Status.CANCELLED,
            Status.DENIED, Status.GONE, Status.ERROR}


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


def _run(
    pool: WorkerPool,
    op: Op,
    path: str,
    *,
    timeout: float,
    keep_names: int = 0,
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
    return 0


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
    """Run a real copy or move and print what the window would be showing.

    The whole engine, minus the window: the queue, the scan, the conflict rule,
    pause and cancel. A 50,000-file copy over SMB can be watched here, and a
    cancel part way through can be seen to settle, without a UI existing.
    """
    import queue as _queue

    from app.io.ops import Transfers
    from app.io.protocol import Conflict, Event, Progress, Transfer

    events: "_queue.Queue[Event]" = _queue.Queue()
    transfers = Transfers(events.put)
    kind = Transfer.MOVE if args.move else Transfer.COPY
    conflict = Conflict(args.conflict)

    print(f"{kind.value} {len(args.sources)} source(s) -> {args.destination}")
    print(f"conflicts: {conflict.value}")
    started = time.monotonic()
    job = transfers.submit(kind, args.sources, args.destination, conflict=conflict)
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
                print(f"  to move: {event.payload['files']:,} files, "
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
            elif event.kind is Progress.FAILED_ITEM:
                print(f"\n  failed: {event.payload.get('name')}: {event.message}")
            elif event.kind is Progress.DONE:
                elapsed = time.monotonic() - started
                print()
                _report("copied", event.payload.get("copied", 0))
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

    opening = with_path("open", "hand a path to the shell, exactly once", timeout=30.0)
    opening.add_argument("--verb", default="",
                         help="edit, print, properties; empty means the default "
                              "verb, which is not the same as open")
    opening.set_defaults(func=cmd_open)

    with_path("mkdir", "create one folder").set_defaults(func=cmd_mkdir)

    renaming = with_path("rename", "rename in place; will not overwrite")
    renaming.add_argument("name", help="the new bare name, not a path")
    renaming.set_defaults(func=cmd_rename)

    deleting = with_path("delete", "delete for real; recycles unless told not to",
                         timeout=300.0)
    deleting.add_argument("names", nargs="+", help="names within the folder")
    deleting.add_argument("--permanent", action="store_true",
                          help="skip the Recycle Bin; there is no undo for this")
    deleting.set_defaults(func=cmd_delete)

    for name, help_text in (("copy", "copy for real, with progress"),
                            ("move", "move for real, with progress")):
        transfer = sub.add_parser(name, help=help_text)
        transfer.add_argument("sources", nargs="+")
        transfer.add_argument("destination")
        transfer.add_argument("--conflict", default="ask",
                              choices=[c.value for c in _conflict_values()],
                              help="what to do when a name is taken")
        transfer.add_argument("--on-conflict", default="skip",
                              choices=[c.value for c in _conflict_values() if c.value != "ask"],
                              help="what this harness answers when asked, "
                                   "applied to the rest of the job")
        transfer.add_argument("--cancel-after", type=int, default=0, metavar="BYTES",
                              help="cancel once this many bytes have moved")
        transfer.add_argument("--timeout", type=float, default=120.0,
                              help="seconds to wait for the next event")
        transfer.set_defaults(func=cmd_transfer, move=(name == "move"))

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
