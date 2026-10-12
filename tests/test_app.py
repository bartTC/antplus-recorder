import errno

from usb.core import USBError

from antplus_recorder.app import SensorTile, open_error
from antplus_recorder.sensors import Sensor


def test_no_permission_on_linux_points_to_the_udev_rule(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert "udev" in open_error(USBError("Access denied", 3, errno.EACCES))


def test_access_denied_elsewhere_means_another_app(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    assert "another app" in open_error(USBError("Access denied", 3, errno.EACCES))
    assert "another app" in open_error(USBError("Resource busy", 6, errno.EBUSY))


def test_other_errors_are_passed_on():
    message = open_error(USBError("Input/output error", 1, errno.EIO))
    assert "Input/output error" in message
    assert "another app" not in message


def test_sensors_that_send_come_first_then_heart_rate_power_trainer():
    def tile(device_type, number, last_seen):
        return SensorTile(Sensor(device_type, number, last_seen=last_seen))

    tiles = [
        tile(120, 1, 0.0),  # heart rate that went quiet
        tile(17, 3, 9.0),
        tile(11, 2, 9.0),
        tile(120, 4, 9.0),
    ]
    ordered = sorted(tiles, key=lambda t: t.order(10.0))
    assert [(t.sensor.kind, t.sensor.number) for t in ordered] == [
        ("heart_rate", 4),
        ("power", 2),
        ("trainer", 3),
        ("heart_rate", 1),
    ]

