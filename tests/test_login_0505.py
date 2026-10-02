"""0.50.5: a share that refuses this session's account asks for another one.

Before, the login prompt only followed a failed Reconnect, so browsing to a
server outside the domain showed Windows' reason and nothing else.
"""

from __future__ import annotations

import pytest

from app.core.pane import _login_share, _windows_words
from app.io import paths
from app.io.protocol import Reply, Status


def _reply(status: Status, number: int, where: str = r"\\?\UNC\srv\share") -> Reply:
    words = {5: "Access is denied", 1326: "The user name or password is incorrect",
             3: "The system cannot find the path specified"}[number]
    kind = "PermissionError" if number == 5 else "OSError"
    return Reply(1, status, message=f"{kind}: [WinError {number}] {words}: "
                                    f"'{where}' (winerror {number})")


def test_a_logon_failure_anywhere_on_a_share_asks_for_its_root():
    reply = _reply(Status.ERROR, 1326)
    assert _login_share(r"\\srv\share\jobs\1234", reply) == r"\\srv\share"
    assert _windows_words(reply) == "The user name or password is incorrect."


def test_access_denied_asks_only_at_the_share_root():
    reply = _reply(Status.DENIED, 5)
    assert _login_share(r"\\srv\share", reply) == r"\\srv\share"
    assert _login_share(r"\\srv\share\locked", reply) is None


def test_other_failures_and_local_folders_never_ask():
    assert _login_share(r"\\srv\share", _reply(Status.GONE, 3)) is None
    assert _login_share(r"C:\Windows", _reply(Status.ERROR, 1326)) is None


@pytest.mark.parametrize("typed, expected", [
    (r"\rj", r"srv\rj"),
    (r".\rj", r"srv\rj"),
    (r"PLANT\rj", r"PLANT\rj"),
    ("rj@plant.local", "rj@plant.local"),
    ("rj", "rj"),
])
def test_the_servers_own_account_is_spelled_with_the_server(typed, expected):
    assert paths.account_for(r"\\srv\share", typed) == expected


def test_a_second_account_on_one_server_says_what_to_do():
    why = paths._conflict_reason(r"\\srv\share")  # noqa: SLF001
    assert "srv" in why and "IP address" in why
