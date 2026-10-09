from antplus_recorder.receiver import Receiver
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
