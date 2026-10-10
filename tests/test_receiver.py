import errno

import pytest
from openant.base.driver import AdvancedOptionsTwo
from usb.core import USBError

from antplus_recorder.receiver import Receiver, StickUnsupported
from tests.pages import extended, heart_rate_page, power_page


def test_scan_data_creates_one_sensor_per_device():
    receiver = Receiver()  # nothing touches the stick before open()
    receiver._on_data(extended(power_page(1, 200, 200), 57587, 11))
    receiver._on_data(
        extended(heart_rate_page(120), 190197 & 0xFFFF, 120, transmission_type=0x21)
    )
    receiver._on_data(extended(power_page(2, 400, 200), 57587, 11))
    assert sorted(receiver.sensors) == [(11, 57587), (120, 190197)]
    # Upper nibble of the transmission type extends the device number to 20 bits
    assert receiver.sensors[120, 190197].number == 190197


def test_pairing_bit_is_ignored():
    receiver = Receiver()
    receiver._on_data(extended(power_page(1, 200, 200), 1, 11 | 0x80))
    assert list(receiver.sensors) == [(11, 1)]


def test_only_listed_devices_are_tracked():
    receiver = Receiver(only={57587})
    receiver._on_data(extended(power_page(1, 200, 200), 57587, 11))
    receiver._on_data(extended(power_page(1, 200, 200), 19696, 11))
    assert list(receiver.sensors) == [(11, 57587)]


def test_messages_without_channel_id_are_skipped():
    receiver = Receiver()
    receiver._on_data(list(power_page(1, 200, 200)))
    assert not receiver.sensors


def test_rssi_and_stick_timestamp_dont_disturb_decoding():
    receiver = Receiver()
    receiver.handle(
        extended(power_page(1, 200, 200), 57587, 11, stick_time=0x1A5A), 0.0
    )
    assert receiver.sensors[11, 57587].value == 200


def test_log_gets_every_tracked_message_unchanged():
    logged = []

    class Log:
        def message(self, data, now):
            logged.append((list(data), now))

    receiver = Receiver(only={57587}, log=Log())
    data = extended(power_page(1, 200, 200), 57587, 11, stick_time=7)
    receiver.handle(data, 1.5)
    receiver.handle(extended(power_page(1, 200, 200), 19696, 11), 1.6)
    assert logged == [(data, 1.5)]


class FakeDriver:
    def __init__(self, error=None):
        self.error = error
        self.reads = 0

    def read(self):
        self.reads += 1
        if self.error:
            raise self.error
        return []


class FakeAnt:
    def __init__(self, driver):
        self._driver = driver
        self.written = []

    def write_message(self, message):
        self.written.append(message)


class FakeNode:
    def __init__(self, driver=None, stop_error=None, options=frozenset()):
        self.ant = FakeAnt(driver or FakeDriver())
        self.stop_error = stop_error
        self.stopped = 0
        self.advanced_options_two = set(options)

    def stop(self):
        self.stopped += 1
        if self.stop_error:
            raise self.stop_error


def no_such_device():
    return USBError("No such device", 19, errno.ENODEV)


def test_stop_survives_a_stick_that_is_already_gone():
    receiver = Receiver()
    receiver.node = FakeNode(stop_error=no_such_device())
    receiver.stop()  # on Linux closing also raises with the stick still there
    assert receiver.node.stopped == 1


def test_losing_the_stick_is_reported_once():
    receiver = Receiver()
    receiver.node = FakeNode(FakeDriver(no_such_device()))
    lost = []
    receiver.on_lost = lambda: lost.append(True)
    receiver._watch_for_loss()
    for _ in range(3):  # openant retries the read at once
        with pytest.raises(USBError):
            receiver.node.ant._driver.read()
    assert lost == [True]


def test_other_usb_errors_are_not_a_lost_stick():
    receiver = Receiver()
    receiver.node = FakeNode(FakeDriver(USBError("Pipe error", 9, errno.EPIPE)))
    receiver.on_lost = lambda: pytest.fail("reported as lost")
    receiver._watch_for_loss()
    with pytest.raises(USBError):
        receiver.node.ant._driver.read()


def test_stick_without_scan_mode_is_refused():
    receiver = Receiver()
    receiver.node = FakeNode(options={AdvancedOptionsTwo.ExtMessageEnabled})
    with pytest.raises(StickUnsupported):
        receiver._check_scan_mode()


def test_stick_with_scan_mode_or_unknown_capabilities_passes():
    receiver = Receiver()
    receiver.node = FakeNode(options={AdvancedOptionsTwo.ScanModeEnabled})
    receiver._check_scan_mode()
    receiver.node = FakeNode()  # capabilities never arrived
    receiver._check_scan_mode()
