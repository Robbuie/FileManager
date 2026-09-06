"""Checks on path resolution.

Worth having because the failure they catch is silent: a path that resolves two
ways gets two workers for one server, which then hang separately and restart
separately. Nothing about that looks wrong until a share goes away.

They run anywhere, including off Windows, because the session table is
injectable — `resolve` and its neighbours take a mapping.
"""

import pytest

from app.io import paths

MAPPING = {"S:": r"\\dc01\projects", "T:": r"\\dc01\archive", "Z:": r"\\nas\media"}


@pytest.mark.parametrize("given, expected", [
    (r"S:\Jobs\2026", r"S:\Jobs\2026"),
    ("s:/jobs/2026", r"S:\jobs\2026"),
    (r"S:\Jobs\2026\\", r"S:\Jobs\2026"),
    (r"S:\\Jobs\\\2026", r"S:\Jobs\2026"),
    ("S:", "S:\\"),
    ("S:\\", "S:\\"),
    (r"\\dc01\projects\Jobs", r"\\dc01\projects\Jobs"),
    (r"\\\\dc01\projects", r"\\dc01\projects"),
    (r"\\dc01\projects\\", r"\\dc01\projects"),
])
def test_one_spelling_per_path(given, expected):
    assert paths.normalize(given) == expected


def test_a_drive_root_keeps_its_separator():
    # "C:" means the current directory on C:, which is not the root.
    assert paths.normalize("C:") == "C:\\"
    assert paths.normalize("C:\\") == "C:\\"


def test_a_posix_path_is_left_alone():
    assert paths.normalize("/tmp/listing") == "/tmp/listing"


def test_a_letter_resolves_to_what_it_is_mapped_to():
    assert paths.resolve(r"S:\Jobs\2026", mapping=MAPPING) == r"\\dc01\projects\Jobs\2026"
    assert paths.resolve("S:", mapping=MAPPING) == r"\\dc01\projects"


def test_an_unmapped_letter_resolves_to_itself():
    assert paths.resolve(r"C:\Windows", mapping=MAPPING) == r"C:\Windows"


def test_the_letter_and_the_unc_share_one_worker():
    key = paths.volume_key(r"S:\Jobs", mapping=MAPPING)
    assert key == paths.volume_key(r"\\dc01\projects\Jobs", mapping=MAPPING)
    assert key == paths.volume_key(r"\\DC01\projects\Jobs", mapping=MAPPING)


def test_two_shares_on_one_server_share_a_worker():
    # The connection to the server is what hangs, not the share.
    assert paths.volume_key(r"S:\a", mapping=MAPPING) == paths.volume_key(r"T:\b", mapping=MAPPING)


def test_two_servers_do_not_share_a_worker():
    assert paths.volume_key(r"S:\a", mapping=MAPPING) != paths.volume_key(r"Z:\b", mapping=MAPPING)


def test_local_disks_share_one_worker():
    assert paths.volume_key(r"C:\Windows", mapping=MAPPING) == paths.LOCAL_VOLUME_KEY
    assert paths.volume_key(r"D:\data", mapping=MAPPING) == paths.LOCAL_VOLUME_KEY


def test_display_is_a_preference_and_round_trips():
    unc = r"\\dc01\projects\Jobs\2026"
    assert paths.display(unc, prefer_letter=True, mapping=MAPPING) == r"S:\Jobs\2026"
    assert paths.display(r"S:\Jobs\2026", prefer_letter=False, mapping=MAPPING) == unc


def test_display_falls_back_to_the_unc_rather_than_inventing_a_letter():
    other = r"\\dc01\nowhere\Jobs"
    assert paths.display(other, prefer_letter=True, mapping=MAPPING) == other


def test_same_volume_sees_through_the_spelling():
    assert paths.same_volume(r"S:\a", r"\\dc01\archive\b", mapping=MAPPING)
    assert not paths.same_volume(r"S:\a", r"Z:\b", mapping=MAPPING)


def test_a_missing_pywin32_is_reported_rather_than_absorbed():
    # The degraded path is the dangerous one: without resolution every share is
    # keyed "local" and shares a worker with the local disks.
    problem = paths.win32_problem()
    if paths.WIN32_AVAILABLE:
        assert problem is None
    else:
        assert problem and "letters" in problem


@pytest.mark.parametrize("given, expected", [
    (r"C:\Windows\System32", r"C:\Windows"),
    (r"C:\Windows", "C:\\"),
    ("C:\\", None),
    (r"\\dc01\projects\Jobs\2026", r"\\dc01\projects\Jobs"),
    (r"\\dc01\projects\Jobs", r"\\dc01\projects"),
    (r"\\dc01\projects", None),
])
def test_walking_up_stops_at_a_root(given, expected):
    assert paths.parent(given) == expected


def test_join_and_leaf_are_string_work_only():
    assert paths.join("C:\\", "Windows") == r"C:\Windows"
    assert paths.join(r"\\dc01\projects", "Jobs") == r"\\dc01\projects\Jobs"
    assert paths.leaf(r"C:\Windows\System32") == "System32"
    assert paths.leaf("C:\\") == "C:\\"
    assert paths.leaf(r"\\dc01\projects") == r"\\dc01\projects"
