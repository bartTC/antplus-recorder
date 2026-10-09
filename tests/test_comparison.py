import pytest

from antplus_recorder.comparison import MIN_REFERENCE_POWER, Lap, difference


def test_difference_in_watts_and_percent():
    assert difference(100, 107) == pytest.approx((-7, -7 / 107 * 100))


def test_no_difference_while_reference_coasts():
    assert difference(10, MIN_REFERENCE_POWER - 1) is None
    assert difference(None, 200) is None


def test_lap_weighs_seconds_by_power():
    lap = Lap()
    for _ in range(60):
        lap.add(95, 100)  # -5% at 100 W
    for _ in range(60):
        lap.add(291, 300)  # -3% at 300 W
    assert lap.seconds == 120
    assert lap.percent == pytest.approx(
        (95 * 60 + 291 * 60 - 100 * 60 - 300 * 60) / (400 * 60) * 100
    )


def test_empty_lap_has_no_percent():
    assert Lap().percent is None
