"""Spawns, kills and restarts worker processes, one per volume.

Contract, before any of it is written:

  * Workers are keyed on **resolved server name**, so `S:\\` and
    `\\\\server\\share\\` share one worker instead of two that hang
    independently. Local disks share a single worker.
  * A worker that stops answering is killed and restarted, and every request
    outstanding against it is failed with `Status.GONE` at that moment.
    Requests are re-issued by the caller, never resumed — a reply that will
    never come is how a tab waits forever.
  * Drive enumeration is lazy and local: `WNetGetConnection` reads local
    session state and does not touch the network. Nothing probes drives at
    startup. Presence in the session table is not reachability.
"""
