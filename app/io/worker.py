"""One worker process. All real I/O for one volume happens here.

Contract, before any of it is written:

  * The process handles one `Request` at a time from its inbound queue and
    answers every one, including the ones it fails.
  * `LIST` uses `os.scandir` and emits `Reply(status=PARTIAL)` batches as it
    goes. It never accumulates a full listing before sending.
  * The process holds no state that matters. It is expected to be killed
    mid-operation and restarted, so recovery is the pool's problem, not its
    own — see `pool.py`.
  * Nothing here imports from `app.ui` or `app.core`.
"""
