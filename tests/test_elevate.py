"""Checks on elevation: what may be elevated, and what the second process does.

The consent prompt cannot be tested without a person looking at it, and the
worker's side of the launch cannot be tested off Windows. What can be, and is
what would actually go wrong, is the shape of the thing: an elevated process
that will run any operation it is handed is a much larger hole than the one
this feature was meant to fill, and a plan that arrives malformed must be
refused rather than half-run.
"""

from __future__ import annotations

import json

from app.io import elevate
from app.io.protocol import Op, Reply, Status


def test_only_the_single_call_operations_can_be_elevated():
    """Listing is not elevatable and neither is anything that streams: an
    elevated process exists to make one change and stop.
    """
    assert elevate.plan_for(Op.MKDIR, "C:\\Program Files\\New") is not None
    assert elevate.plan_for(Op.RENAME, "C:\\Program Files\\a", {"name": "b"}) is not None
    assert elevate.plan_for(Op.DELETE, "C:\\Program Files", {"names": ["a"]}) is not None
    for op in (Op.LIST, Op.STALL, Op.ICON, Op.OPEN, Op.MENU_INVOKE):
        assert elevate.plan_for(op, "C:\\Program Files") is None


def test_a_plan_says_what_it_will_do_in_words_a_dialog_can_use():
    plan = elevate.plan_for(Op.DELETE, "C:\\Program Files",
                            {"names": ["a", "b"], "permanent": True})
    assert elevate.describe(plan) == "delete permanently 2 item(s) in C:\\Program Files"


def test_an_operation_that_is_not_on_the_list_is_refused(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"action": "list", "path": str(tmp_path)}), encoding="utf-8")
    assert elevate.perform(str(plan)) == 1
    result = json.loads((tmp_path / "plan.json.result").read_text(encoding="utf-8"))
    assert result["status"] == Status.ERROR.value
    assert "cannot be elevated" in result["message"]


def test_a_plan_that_is_not_an_operation_is_refused(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text("[1, 2, 3]", encoding="utf-8")
    assert elevate.perform(str(plan)) == 1


def test_a_missing_plan_is_a_failure_rather_than_a_traceback(tmp_path):
    assert elevate.perform(str(tmp_path / "nothing.json")) == 1


def test_the_elevated_process_runs_the_worker_rather_than_its_own_copy(tmp_path):
    """The same code with a different token. A second implementation of delete
    that only runs when elevated is one that is only tested when elevated.
    """
    target = tmp_path / "made"
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"action": "mkdir", "path": str(target)}),
                    encoding="utf-8")
    assert elevate.perform(str(plan)) == 0
    assert target.is_dir()
    result = json.loads((tmp_path / "plan.json.result").read_text(encoding="utf-8"))
    assert result["status"] == Status.OK.value


def test_the_result_travels_beside_the_plan_and_both_are_cleaned_up(tmp_path):
    written = elevate.write_plan({"action": "mkdir", "path": str(tmp_path / "x")}, 30.0)
    assert json.loads(open(written, encoding="utf-8").read())["timeout"] == 30.0
    elevate.perform(written)
    assert elevate.read_result(written)["status"] == Status.OK.value
    elevate.clean_up(written)
    assert elevate.read_result(written) == {}


def test_the_worker_refuses_a_plan_it_was_not_offered():
    """Checked in the worker as well as in the elevated process. One of those
    two is redundant and neither is the one to leave out.
    """
    from app.io import worker

    replies = []
    request = _request({"plan": {"action": "list", "path": "C:\\Windows"}})
    worker._elevate(request, _Outbox(replies))
    assert replies[0].status is Status.ERROR
    assert "cannot be run as administrator" in replies[0].message

    replies.clear()
    worker._elevate(_request({"plan": "delete everything"}), _Outbox(replies))
    assert replies[0].status is Status.ERROR


class _Outbox:
    def __init__(self, replies):
        self._replies = replies

    def put(self, reply: Reply) -> None:
        self._replies.append(reply)


def _request(args):
    from app.io.protocol import Request

    return Request(id=1, op=Op.ELEVATE, path="C:\\Windows", timeout=5.0, args=args)
