"""Listen to every ANT+ sensor in range, without pairing."""

import contextlib
import time
from collections.abc import Callable, Sequence
from typing import Protocol

import libusb_package
import usb.backend.libusb1
from openant.base.message import Message
from openant.devices import ANTPLUS_NETWORK_KEY
from openant.easy.channel import Channel
from openant.easy.exception import AntException
from openant.easy.node import Node
from usb.core import USBError

from .sensors import Sensor

ANTPLUS_FREQUENCY = 57  # 2457 MHz
OPEN_ATTEMPTS = 3
RESPONSE_TIMEOUT = 0.5  # seconds; a working stick answers within about 30 ms


# Lib config flags for the data after each page: channel ID, RSSI, receive
# timestamp. Tried in this order; sticks without RSSI fall back to fewer.
EXTENDED_DATA = {0xE0: "channel ID, RSSI, timestamp", 0xA0: "channel ID, timestamp"}
DATA_EVENTS = {
    Message.Code.EVENT_RX_BROADCAST,
    Message.Code.EVENT_RX_ACKNOWLEDGED,
    Message.Code.EVENT_RX_BURST_PACKET,
    Message.Code.EVENT_TX,
}


class StickNotResponding(AntException):
    pass


class Log(Protocol):
    """Where the receiver reports what it gets, e.g. the raw log."""

    def message(self, data: Sequence[int], now: float) -> None: ...
    def meta(self, key: str, value: str, now: float) -> None: ...
    def event(self, text: str, now: float) -> None: ...


def load_libusb() -> None:
    """Use the system's libusb, or the copy bundled with libusb-package without one.

    pyusb keeps the first library it loads and openant asks for its default
    backend, so loading the bundled copy here is enough for a fresh Mac
    without Homebrew's libusb.
    """
    if usb.backend.libusb1.get_backend() is None:
        libusb_package.get_libusb1_backend()


class Receiver:
    """Runs the ANT+ stick in continuous scan mode.

    In this mode the radio receives all the time and picks up every sensor on
    the ANT+ frequency, so there is no per-sensor channel to pair or open, and
    no limit of 8 channels per stick.
    """

    def __init__(self, only: set[int] | None = None, log: Log | None = None) -> None:
        """Only sensors with a device number in `only` are tracked, if given.

        `log` gets every tracked message unchanged, the stick's details and its
        events.
        """
        self.only = only
        self.log = log
        # Lib config flags in use, see EXTENDED_DATA
        self.extended_data = 0x80
        self.sensors: dict[tuple[int, int], Sensor] = {}
        self.node: Node | None = None
        self._stopping = False

    def open(self, status: Callable[[str], None] = lambda _: None) -> bool:
        """Open the stick and start scanning, reporting progress to `status`.

        Returns False if stop() was called meanwhile. Raises openant's
        DriverNotFound without a stick, USBError if another process has it, and
        StickNotResponding if it never answers.
        """
        load_libusb()
        for attempt in range(OPEN_ATTEMPTS):
            status(
                "Opening ANT+ stick..."
                if attempt == 0
                else "Stick didn't answer, resetting it..."
            )
            # Resets the stick, which takes a second
            self.node = Node()
            if self._stopping:
                # stop() ran while Node() was starting up
                self.node.stop()
                return False
            if self._responds():
                break
            # A stick left scanning by a session that didn't reset it (killed,
            # crashed) ignores the first open, but works on the next one
            self.node.stop()
        else:
            raise StickNotResponding

        status("Starting scan mode...")
        try:
            self._open_scan_channel()
        except Exception:
            # The node runs a non-daemon thread that would keep the process alive
            self.node.stop()
            raise
        if self.log:
            self.log.meta("stick", self.stick_info, time.monotonic())
        status(f"Listening on {2400 + ANTPLUS_FREQUENCY} MHz")
        return True

    @property
    def stick_info(self) -> str:
        node = self.node
        product = getattr(node.ant._driver, "ID_PRODUCT", None)  # noqa: SLF001
        usb = f"USB 0fcf:{product:04x}, " if product else ""
        extended = EXTENDED_DATA.get(self.extended_data, "channel ID")
        version = (node.ant_version or "").rstrip("\x00 ")
        return f"serial {node.serial}, {version}, {usb}extended data {self.extended_data:#04x} ({extended})"

    def _responds(self) -> bool:
        # Node() requests the stick's serial number while starting up
        deadline = time.monotonic() + RESPONSE_TIMEOUT
        while self.node.serial is None:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.02)
        return True

    def _open_scan_channel(self) -> None:
        self.node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
        channel = self.node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
        channel.on_broadcast_data = self._on_data
        channel.on_acknowledge_data = channel.on_burst_data = lambda _: None
        # Wildcard channel ID matches every device type and number
        channel.set_id(0, 0, 0)
        channel.enable_extended_messages(1)
        for flags in EXTENDED_DATA:
            self.node.ant.write_message(Message(Message.ID.LIB_CONFIG, [0x00, flags]))
            try:
                self.node.wait_for_response(Message.ID.LIB_CONFIG)
            except Exception:  # noqa: BLE001, S112 - openant raises a bare Exception for a refusal
                continue
            self.extended_data = flags
            break
        channel.set_rf_freq(ANTPLUS_FREQUENCY)
        channel.open_rx_scan_mode()

        # Stick events such as a serial queue overflow, which explain gaps the
        # sensors didn't cause
        forward = self.node.ant.channel_event_function

        def on_event(channel: int, event: int, data: Sequence[int]) -> None:
            if self.log and event not in DATA_EVENTS:
                name = Message.Code.lookup(event) or f"event {event:#04x}"
                self.log.event(
                    f"channel {channel} {name} {bytes(data).hex(' ')}", time.monotonic()
                )
            forward(channel, event, data)

        self.node.ant.channel_event_function = on_event

    def run(self) -> None:
        """Process incoming data until stop() is called."""
        if self.node and not self._stopping:
            self.node.start()

    def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        if self.node is None:
            return
        # Reset the stick so it stops streaming scan data. A stick left
        # scanning ignores the next session's first open.
        with contextlib.suppress(USBError):
            self.node.ant.write_message(Message(Message.ID.RESET_SYSTEM, [0x00]))
        self.node.stop()

    def _on_data(self, data: Sequence[int]) -> None:
        self.handle(data, time.monotonic())

    def handle(self, data: Sequence[int], now: float) -> None:
        """Decode one message from the stick, live or replayed from a raw log."""
        # Extended data after the 8-byte page: flag byte, then the channel ID
        # of the sender (device number, device type, transmission type)
        if len(data) < 13:
            return
        number = data[9] | data[10] << 8 | (data[12] & 0xF0) << 12
        if self.only and number not in self.only:
            return
        if self.log:
            self.log.message(data, now)
        device_type = data[11] & 0x7F  # bit 7 is the pairing bit
        key = (device_type, number)
        sensor = self.sensors.get(key)
        if sensor is None:
            sensor = self.sensors[key] = Sensor(device_type, number)
        sensor.update(bytes(data[:8]), now)
