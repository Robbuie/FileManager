"""Every real filesystem call in the application, in separate processes.

One worker process per volume, keyed on resolved server name. A hung share is
a killed process, not a frozen window.
"""
