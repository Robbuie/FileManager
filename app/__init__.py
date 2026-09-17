"""File Manager — a Windows dual-pane file manager.

The package is split three ways and the boundaries are the design. `ui` renders
and takes input, `core` holds state, `io` owns every real filesystem call and
runs it in a separate process. See CLAUDE.md before adding to any of them.
"""

__version__ = "0.29.12"
