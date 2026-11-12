r"""0.61: a bare `\\server` is a place a pane can stand, and logs in.

A favourite saved as `\\server` -- a server outside the domain, reached with
the server's own account -- used to fail with 1326 and never offer the login
prompt, because the prompt was only offered for a share. These pin the string
decisions that route it; the share enumeration itself is Windows' and is
checked against the real server.
"""

from __future__ import annotations

from app.core.network import clean
from app.core.pane import _login_share
from app.io import paths, worker
from app.io.protocol import Op, Reply, Request, Status


def _refused(where: str = r"\\nas01") -> Reply:
    return Reply(1, Status.ERROR, message=(
        "OSError: [WinError 1326] The user name or password is incorrect: "
        f"'{where}' (winerror 1326)"))


def test_a_logon_failure_on_a_bare_server_asks_for_the_server():
    assert _login_share(r"\\nas01", _refused()) == r"\\nas01"


def test_access_denied_on_a_bare_server_asks_too():
    denied = Reply(1, Status.DENIED, message="PermissionError: [WinError 5] "
                   "Access is denied: '\\\\nas01' (winerror 5)")
    assert _login_share(r"\\nas01", denied) == r"\\nas01"


def test_a_bare_server_can_be_reconnected():
    assert clean(r"\\nas01") == r"\\nas01"
    assert clean("\\\\nas01\\") == r"\\nas01"
    assert clean(r"\\nas01\public") == r"\\nas01\public"
    assert clean(r"C:\Windows") == ""


def test_the_servers_own_account_is_qualified_against_ipc():
    r"""`connect` turns `\\nas01` into `\\nas01\IPC$` before the account is
    worked out, so `\rj` still becomes `nas01\rj`."""
    assert paths.account_for(r"\\nas01\IPC$", r"\rj") == r"nas01\rj"


def test_listing_a_bare_server_is_an_envelope_not_a_crash():
    """Off Windows there is no NetShareEnum; the reply must still be a reply."""
    sent: list[Reply] = []

    class Box:
        put = staticmethod(sent.append)

    worker._list(Request(7, Op.LIST, r"\\nas01", timeout=5.0), Box(), None, set())
    assert len(sent) == 1
    assert sent[0].id == 7
    assert sent[0].status is not Status.OK
