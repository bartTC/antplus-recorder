import subprocess
import sys

import pytest

from antplus_recorder.awake import stay_awake


def assertions() -> str:
    return subprocess.run(
        ["/usr/bin/pmset", "-g", "assertions"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS power assertions")
def test_holds_a_power_assertion_only_while_running():
    assert "antplus-recorder is recording" not in assertions()
    with stay_awake():
        assert "antplus-recorder is recording" in assertions()
    assert "antplus-recorder is recording" not in assertions()


def test_nothing_to_do_elsewhere(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    with stay_awake():
        pass
