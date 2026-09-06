"""The copy/move queue, in a process of its own.

Separate from the listing workers so a stalled transfer cannot take the window
down, and so a transfer survives the window closing.

The hard part is not the copying. It is the queue around it, and this is where
most hobby file managers fall over, so it gets designed before the copy loop
gets written:

  * pause and resume;
  * per-file and total progress, on a byte count established up front;
  * conflict rules — skip, overwrite, newer only, auto-rename — decided per
    item, with an answer that can be applied to the rest of the queue;
  * locked-file retry with a bounded backoff, not an indefinite one;
  * timestamps and attributes preserved on the destination;
  * a confirmed destination before a single byte moves. The application never
    picks a target on its own and never reports where something went after the
    fact.
"""
