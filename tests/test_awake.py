import os
import subprocess
import sys

import pytest

from antplus_recorder.awake import stay_awake


def holding() -> bool:
    """Whether this process holds the assertion; a running dashboard has its own."""
    listing = subprocess.run(
        ["/usr/bin/pmset", "-g", "assertions"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return any(
        f"pid {os.getpid()}(" in line and "antplus-recorder is recording" in line
        for line in listing.splitlines()
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS power assertions")
def test_holds_a_power_assertion_only_while_running():
    assert not holding()
    with stay_awake():
        assert holding()
    assert not holding()


def test_nothing_to_do_elsewhere(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    with stay_awake():
        pass
