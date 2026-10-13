import asyncio
import errno
import os
from pathlib import Path

from usb.core import USBError

from antplus_recorder import changelog
from antplus_recorder.app import ChangelogScreen, RecorderApp, SensorTile, open_error
from antplus_recorder.receiver import Receiver
from antplus_recorder.registry import Registry
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


def test_changelog_is_readable_and_current():
    text = changelog.read()
    assert text.startswith("# Changelog")
    assert "## [Unreleased]" in text


def test_menu_offers_the_changelog_and_it_opens():
    class NoStick(RecorderApp):
        def _listen(self) -> None:
            pass

    async def run():
        app = NoStick(Receiver(), Registry(Path(os.devnull)), None)
        async with app.run_test() as pilot:
            titles = [c.title for c in app.get_system_commands(app.screen)]
            assert "Changelog" in titles
            app.action_changelog()
            await pilot.pause()
            assert isinstance(app.screen, ChangelogScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ChangelogScreen)

    asyncio.run(run())
