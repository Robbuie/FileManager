"""Panes, tabs, dialogs, widgets. Presentation and input only.

Nothing in this package may import `os`, `pathlib`, `shutil` or `win32file`, or
call the filesystem by any other route. A blocked SMB call on the UI thread is
the defect this application exists to fix.
"""
