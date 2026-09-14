"""Say what is actually on the Windows clipboard, and put things on it.

Copy and paste between two programs has three places to fail and they look
identical from either end: the copy wrote nothing, the copy wrote something in
a format the other program does not read, or the data was gone by the time the
paste asked for it. Nothing on screen tells them apart, so this reads the
clipboard directly and prints what is there.

    python tools\\diagnose_clipboard.py              what is on it now
    python tools\\diagnose_clipboard.py --put <path> ... put files on it as a copy
    python tools\\diagnose_clipboard.py --put-cut <path> ... the same, as a cut

The reading form is the one to use first: copy a file in File Manager, leave
the window open, run this, and look for two lines.

  * **CF_HDROP** is the list of files. Explorer pastes this and nothing else.
    No CF_HDROP means the copy never wrote any files, whatever the status line
    said.
  * **Preferred DropEffect** is the word that says copy or cut. Missing, a cut
    pastes as a copy everywhere.

The `--put` forms do the reverse: they write the clipboard the way Explorer
writes it, using Windows directly rather than Qt, so pasting into File Manager
afterwards tests this application's *reading* without Explorer in the way.

One thing worth knowing before reading anything into a result: **a Qt program
hands over clipboard data only when another program asks for it, and it has to
still be running to answer.** Closing File Manager after copying can take the
files off the clipboard with it. Copy, then leave it open, then paste.

This is a development tool. It reads and writes the clipboard and touches
nothing else.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

#: The two that decide whether a paste works, by their Windows names.
HDROP = "CF_HDROP"
DROP_EFFECT = "Preferred DropEffect"

#: The standard formats have numbers rather than names, so they have to be
#: spelled out. Only the ones worth seeing in this context are listed; anything
#: else prints as its number, which is enough to look up.
STANDARD = {
    1: "CF_TEXT", 2: "CF_BITMAP", 3: "CF_METAFILEPICT", 6: "CF_TIFF",
    7: "CF_OEMTEXT", 8: "CF_DIB", 13: "CF_UNICODETEXT", 15: HDROP,
    16: "CF_LOCALE", 17: "CF_DIBV5",
}


def _win32():
    try:
        import win32clipboard
    except ImportError:
        print("pywin32 is not installed in this interpreter.")
        print("Run this with the project venv: .venv\\Scripts\\python.exe")
        raise SystemExit(2) from None
    return win32clipboard


def _name(board, number: int) -> str:
    if number in STANDARD:
        return STANDARD[number]
    try:
        return board.GetClipboardFormatName(number)
    except Exception:  # noqa: BLE001 - an unnamed format is worth printing too
        return f"format {number}"


def show() -> int:
    """Everything on the clipboard, with the two that matter decoded."""
    board = _win32()
    board.OpenClipboard()
    try:
        found: list[tuple[int, str]] = []
        number = board.EnumClipboardFormats(0)
        while number:
            found.append((number, _name(board, number)))
            number = board.EnumClipboardFormats(number)

        if not found:
            print("The clipboard is empty.")
            return 1

        print(f"{len(found)} format(s) on the clipboard:\n")
        names = {name for _, name in found}
        for number, name in found:
            print(f"  {number:>6}  {name}")

        print()
        if HDROP in names:
            files = board.GetClipboardData(15)
            print(f"CF_HDROP: {len(files)} path(s)")
            for item in files:
                print(f"    {item}")
        else:
            print("CF_HDROP: NOT THERE. Explorer has nothing to paste.")

        if DROP_EFFECT in names:
            number = next(n for n, name in found if name == DROP_EFFECT)
            raw = bytes(board.GetClipboardData(number))
            word = struct.unpack("<I", raw[:4])[0] if len(raw) >= 4 else 0
            word_name = {1: "copy", 2: "move (cut)", 3: "copy|move"}.get(
                word, f"unrecognised ({word})")
            print(f"Preferred DropEffect: {raw[:4]!r} -- {word_name}")
        else:
            print("Preferred DropEffect: not there. A cut would paste as a copy.")
        return 0
    finally:
        board.CloseClipboard()


def put(items: list[str], *, cut: bool) -> int:
    """Write the clipboard the way Explorer writes it, without Qt involved.

    A `DROPFILES` header followed by the paths as wide characters, double null
    terminated, which is what `CF_HDROP` is. Built by hand on purpose: pasting
    this into File Manager tests its reading against something this project did
    not also write.
    """
    board = _win32()
    import win32con

    paths_ = [os.path.abspath(item) for item in items]
    # DROPFILES: pFiles (offset to the names), pt.x, pt.y, fNC, fWide.
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    names = "".join(item + "\0" for item in paths_) + "\0"
    payload = header + names.encode("utf-16-le")

    board.OpenClipboard()
    try:
        board.EmptyClipboard()
        board.SetClipboardData(win32con.CF_HDROP, payload)
        effect = board.RegisterClipboardFormat(DROP_EFFECT)
        board.SetClipboardData(effect, struct.pack("<I", 2 if cut else 1))
    finally:
        board.CloseClipboard()

    word = "cut" if cut else "copy"
    print(f"Put {len(paths_)} path(s) on the clipboard as a {word}:")
    for item in paths_:
        print(f"    {item}")
    print("\nNow paste in File Manager. A cut must move; a copy must copy.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--put", nargs="+", metavar="PATH",
                        help="put these on the clipboard as a copy")
    parser.add_argument("--put-cut", nargs="+", metavar="PATH",
                        help="put these on the clipboard as a cut")
    args = parser.parse_args(argv)

    if args.put and args.put_cut:
        print("One or the other, not both.")
        return 2
    if args.put:
        return put(args.put, cut=False)
    if args.put_cut:
        return put(args.put_cut, cut=True)
    return show()


if __name__ == "__main__":
    sys.exit(main())
