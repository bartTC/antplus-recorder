"""Build ANT+ data pages the way sensors broadcast them."""


def power_page(
    event: int,
    accumulated: int,
    power: int,
    cadence: int = 90,
    right: int | None = None,
) -> bytes:
    """Standard power-only page (0x10). `right` is the right leg's percentage."""
    balance = 0xFF if right is None else 0x80 | right
    accumulated %= 0x10000
    return bytes(
        [
            0x10,
            event % 256,
            balance,
            cadence,
            accumulated & 0xFF,
            accumulated >> 8,
            power & 0xFF,
            power >> 8,
        ]
    )


def trainer_page(
    event: int,
    accumulated: int,
    power: int,
    cadence: int = 90,
    status: int = 0,
    target: int = 0,
) -> bytes:
    """FE-C specific trainer data page (0x19), state 'in use'."""
    accumulated %= 0x10000
    return bytes(
        [
            0x19,
            event % 256,
            cadence,
            accumulated & 0xFF,
            accumulated >> 8,
            power & 0xFF,
            status << 4 | power >> 8,
            0x30 | target,
        ]
    )


def heart_rate_page(bpm: int, page: int = 0) -> bytes:
    return bytes([page, 0xFF, 0xFF, 0xFF, 0, 0, 1, bpm])


def extended(
    page: bytes,
    number: int,
    device_type: int,
    transmission_type: int = 0x05,
    stick_time: int | None = None,
) -> list[int]:
    """Broadcast data as the stick reports it in scan mode: page, flag byte, channel ID.

    With `stick_time` (1/32768 s) the way an ANT USB2 stick adds RSSI and its
    receive timestamp when asked for them.
    """
    channel_id = [number & 0xFF, number >> 8 & 0xFF, device_type, transmission_type]
    if stick_time is None:
        return [*page, 0x80, *channel_id]
    rssi = [0x10, 0x01, 0x6D, 0x00]
    return [*page, 0xE0, *channel_id, *rssi, stick_time & 0xFF, stick_time >> 8 & 0xFF]
