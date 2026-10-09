import pytest

from antplus_recorder.sensors import COAST_TIMEOUT, Sensor
from tests.pages import heart_rate_page, power_page, trainer_page


def test_power_page():
    sensor = Sensor(11, 1)
    sensor.update(power_page(1, 250, 250, cadence=92, right=52), 0.0)
    assert sensor.value == 250
    assert sensor.cadence == 92
    assert sensor.right_balance == 52


def test_single_sided_meter_has_no_balance():
    sensor = Sensor(11, 1)
    sensor.update(power_page(1, 250, 250), 0.0)
    assert sensor.right_balance is None


def test_power_averages_every_event_between_pages():
    # Torque meters send the power page only every few messages: the value
    # covers all events since the previous one, not just the latest
    sensor = Sensor(11, 1)
    accumulated = 65400  # rolls over
    sensor.update(power_page(254, accumulated, 200), 0.0)
    for power in (300, 250, 210):
        accumulated += power
    sensor.update(power_page(254 + 3, accumulated, 210), 2.0)
    assert sensor.value == round((300 + 250 + 210) / 3)


def test_repeated_event_holds_then_coasts_to_zero():
    sensor = Sensor(11, 1)
    sensor.update(power_page(1, 400, 400, cadence=53, right=43), 0.0)
    sensor.update(power_page(1, 400, 400, cadence=53, right=43), COAST_TIMEOUT - 0.5)
    assert sensor.value == 400
    sensor.update(power_page(1, 400, 400, cadence=53, right=43), COAST_TIMEOUT + 0.5)
    assert sensor.value == 0
    assert sensor.cadence == 0
    assert sensor.right_balance is None


def test_right_balance_average_is_power_weighted():
    sensor = Sensor(11, 1)
    accumulated = 0
    for second in range(20):
        power, right = (200, 50) if second < 10 else (300, 56)
        accumulated += power
        sensor.update(
            power_page(second + 1, accumulated, power, right=right), float(second)
        )
    assert sensor.right_balance_average(30, 19.0) == pytest.approx(
        (50 * 2000 + 56 * 3000) / 5000
    )
    assert sensor.right_balance_average(5, 19.0) == 56


def test_heart_rate_pages():
    sensor = Sensor(120, 1)
    sensor.update(heart_rate_page(132), 0.0)
    assert sensor.value == 132
    sensor.update(
        bytes([0x82, 1, 0x34, 0x12, 0, 0, 2, 133]), 0.25
    )  # page 2, toggle bit set
    assert sensor.manufacturer == "Garmin"
    assert sensor.serial == 0x1234
    assert sensor.value == 133
    sensor.update(bytes([0x07, 80, 0, 0, 0, 0, 3, 134]), 0.5)
    assert sensor.battery == "80%"


def test_common_pages():
    sensor = Sensor(11, 1)
    sensor.update(bytes([0x50, 0xFF, 0xFF, 7, 69, 0, 3, 0]), 0.0)
    sensor.update(bytes([0x51, 0xFF, 0xFF, 15, 0x08, 0xC3, 0x6B, 0x02]), 0.0)
    sensor.update(bytes([0x52, 0xFF, 0xFF, 0, 0, 0, 0, 0x20]), 0.0)
    assert sensor.manufacturer == "Stages Cycling"
    assert sensor.model == 3
    assert sensor.serial == 0x026BC308
    assert sensor.battery == "good"


def test_trainer_pages():
    sensor = Sensor(17, 1)
    sensor.update(bytes([0x10, 0x19, 0, 0, 0x28, 0x23, 0xFF, 0x34]), 0.0)  # 9 m/s
    sensor.update(bytes([0x11, 0xFF, 0xFF, 0xCF, 0xFF, 0x7F, 70, 0x30]), 0.0)
    sensor.update(trainer_page(1, 200, 200, cadence=88, status=0x2, target=1), 0.0)
    assert sensor.speed == pytest.approx(32.4)
    assert sensor.resistance == 35
    assert sensor.incline is None
    assert sensor.fe_state == "in use"
    assert sensor.value == 200
    assert sensor.cadence == 88
    assert sensor.trainer_status == ("spindown needed",)
    assert sensor.target_power_status == "ERG: speed too low"


def test_value_since_holds_last_value_while_fresh():
    sensor = Sensor(11, 1)
    sensor.update(power_page(1, 240, 240), 0.5)
    assert sensor.value_since(1.0, 2.0) == 240  # no new page in that second
    assert sensor.value_since(10.0, 11.0) is None  # gone quiet
