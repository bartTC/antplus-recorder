"""Live state of ANT+ sensors, decoded from their broadcast data pages.

A sensor broadcasts 8-byte data pages at a fixed rate. Each `Sensor` keeps what
the pages said so far (current value, cadence, battery, a history of readings
for averages), and `Sensor.update` decodes one page into it. Only power meters,
smart trainers and heart rate straps are decoded; every other device type is
tracked by identity and last-seen time alone.

The stick's thread calls `update` while the dashboard reads, so readers must
snapshot the sample deques before iterating.
"""

from collections import deque
from dataclasses import dataclass, field

from garmin_fit_sdk import Profile

MANUFACTURERS = Profile["types"]["manufacturer"]

# ANT+ device type -> kind, which groups sensors on screen and in devices.toml
KINDS = {
    11: "power",
    17: "trainer",
    120: "heart_rate",
    121: "speed_cadence",
    122: "cadence",
    123: "speed",
    124: "stride",
}

UNITS = {"power": "W", "trainer": "W", "heart_rate": "bpm"}

BATTERY_STATUS = {1: "new", 2: "good", 3: "ok", 4: "low", 5: "critical", 6: "charging"}

STALE_AFTER = 3.0  # seconds without any page before a sensor counts as gone
# Seconds without a new power event before showing 0 W, as head units do
COAST_TIMEOUT = 3.0
# Older than this, the previous event is no baseline to average over
EVENT_GAP = 10.0

# FE-C (fitness equipment control), as broadcast by smart trainers
FE_STATES = {1: "off", 2: "ready", 3: "in use", 4: "paused"}
FE_PAGES_WITH_STATE = (0x10, 0x11, 0x19, 0x1A)
# Page 0x19, byte 6 bits 4-6. Bit 1 (spindown needed) is left out: a Kickr
# sends it on every message, even right after a spindown
TRAINER_STATUS = {
    0x1: "zero offset needed",
    0x4: "user config needed",
}
# Only reported while an app such as Zwift holds the trainer at a target power
TARGET_POWER_STATUS = {1: "ERG: speed too low", 2: "ERG: speed too high"}


@dataclass
class Sensor:
    """One ANT+ device heard in scan mode, identified by device type and number.

    Fields are filled in as the matching data pages arrive, so most of them stay
    None until the sensor has sent them: manufacturer, model and serial arrive
    on pages that come round only every minute or two, and what a sensor sends
    depends on its profile. `value` is watts for power meters and trainers and
    bpm for heart rate straps; `samples` keeps every reading with its arrival
    time for averaging.
    """

    device_type: int
    number: int
    manufacturer_id: int | None = None
    model: int | None = None
    serial: int | None = None
    value: int | None = None  # watts for power and trainer, bpm for heart rate
    cadence: int | None = None
    right_balance: int | None = None  # percent of total power from the right leg
    speed: float | None = None  # km/h
    battery: str | None = None
    fe_state: str | None = None
    resistance: float | None = None  # percent of maximum
    incline: float | None = None  # percent
    trainer_status: tuple[str, ...] = ()
    target_power_status: str | None = None
    last_seen: float = 0.0
    # (event count, accumulated power, time) of the last new power event
    _event: tuple[int, int, float] | None = field(default=None, repr=False)
    # (monotonic time, value) for every page that carried the main value,
    # enough for a 60 s average at the usual 4 messages per second
    samples: deque[tuple[float, int]] = field(
        default_factory=lambda: deque(maxlen=1000)
    )
    # (monotonic time, right leg percent, power) whenever a balance came with the power
    balance_samples: deque[tuple[float, int, int]] = field(
        default_factory=lambda: deque(maxlen=1000)
    )

    @property
    def kind(self) -> str:
        """The name of the device type, e.g. `power`, or `type_<n>` if it isn't one we know."""
        return KINDS.get(self.device_type, f"type_{self.device_type}")

    @property
    def unit(self) -> str:
        """Unit of `value`, empty for kinds that don't report one."""
        return UNITS.get(self.kind, "")

    @property
    def manufacturer(self) -> str | None:
        """The manufacturer's name as decoded from the FIT profile, or None if unknown.

        Sensors send this on a page of its own, so it is missing until that page
        has come round. An id outside the profile reads as `Manufacturer <id>`.
        """
        if self.manufacturer_id is None:
            return None
        name = MANUFACTURERS.get(self.manufacturer_id)
        if name is None:
            return f"Manufacturer {self.manufacturer_id}"
        return " ".join(word[:1].upper() + word[1:] for word in name.split("_"))

    @property
    def has_data(self) -> bool:
        """Whether it ever sent a reading, not just a page that announced it.

        Passers-by are heard before they say anything useful, and a device type
        that isn't decoded never has a reading, so this is what separates
        sensors worth remembering from noise.
        """
        return bool(self.samples)

    def is_fresh(self, now: float) -> bool:
        """Whether a page arrived within STALE_AFTER seconds of `now`.

        Sensors sleep when nothing moves, so a stale sensor is normal (the user
        stopped pedalling) and not an error; the dashboard dims it instead.
        """
        return now - self.last_seen < STALE_AFTER

    # The stick's thread appends to the sample deques while the dashboard reads
    # them, and iterating a deque that another thread appends to raises. list()
    # copies it in one step under the GIL, so every reader snapshots first.

    def mean(self, since: float, until: float) -> float | None:
        """Mean of the readings that arrived after `since`, up to and including `until`.

        Returns None if there are none.
        """
        values = [v for t, v in list(self.samples) if since < t <= until]
        return sum(values) / len(values) if values else None

    def value_since(self, since: float, now: float) -> float | None:
        """Mean value since `since`, or the last one held while the sensor still sends.

        Most power meters send a new value only every second or two, so a
        one-second slice often has none; head units hold the last value too.
        """
        value = self.mean(since, now)
        if value is None and self.is_fresh(now):
            value = self.value
        return value

    def average(self, seconds: float, now: float) -> float | None:
        return self.mean(now - seconds, now)

    def right_balance_average(self, seconds: float, now: float) -> float | None:
        """Right leg's share over the last `seconds`, weighted by power like head units do."""
        recent = [
            (right, power)
            for t, right, power in list(self.balance_samples)
            if now - t <= seconds
        ]
        total = sum(power for _, power in recent)
        if not total:
            return None
        return sum(right * power for right, power in recent) / total

    def rate(self, now: float, seconds: float = 4.0) -> float:
        """Readings per second over the last `seconds`, a rough signal quality indicator.

        A power meter normally sends 4 a second; a lower rate while the sensor is
        in range points to dropped messages. Held values (see `_hold_or_coast`)
        count as readings too, so this measures messages, not new measurements.
        """
        return sum(1 for t, _ in list(self.samples) if now - t <= seconds) / seconds

    def update(self, page: bytes, now: float) -> None:
        """Decode one 8-byte ANT+ data page received at monotonic time `now`.

        Every page counts as a sign of life, whatever it carries. The common
        pages (manufacturer, serial, battery) have the same layout in every
        profile except heart rate, which carries its own in a rotating page
        number; everything else is handled per profile. Pages of other device
        types or with unknown numbers are ignored.
        """
        self.last_seen = now
        number = page[0]

        # Common pages, sent by every modern profile except heart rate
        if number == 0x50:
            self.manufacturer_id = page[4] | page[5] << 8
            self.model = page[6] | page[7] << 8
        elif number == 0x51:
            serial = int.from_bytes(page[4:8], "little")
            if serial != 0xFFFFFFFF:
                self.serial = serial
        elif number == 0x52:
            self.battery = BATTERY_STATUS.get(page[7] >> 4 & 0x07)
        elif self.device_type == 120:
            self._heart_rate(page, now)
        elif self.device_type == 11 and number == 0x10:
            self._power(page, now)
        elif self.device_type == 17:
            self._trainer(page, now)

    def _set_value(self, value: int, now: float) -> None:
        """Make `value` the current reading and add it to the history.

        The balance sample is kept alongside only if one is known, so the power
        weighted left/right average sees the same readings as the power average.
        """
        self.value = value
        self.samples.append((now, value))
        if self.right_balance is not None:
            self.balance_samples.append((now, self.right_balance, value))

    def _heart_rate(self, page: bytes, now: float) -> None:
        """Decode a heart rate page.

        Every page carries the current bpm in its last byte; the first four bytes
        depend on the page number and rotate through manufacturer, model, battery
        and so on. A 0 means the strap has no reading yet and is skipped.
        """
        # Bit 7 toggles every 4 messages; bytes 4-7 are the same on every page
        number = page[0] & 0x7F
        if number == 2:
            self.manufacturer_id = page[1]
            self.serial = page[2] | page[3] << 8  # upper 16 bits of the serial only
        elif number == 3:
            self.model = page[3]
        elif number == 7 and page[1] <= 100:
            self.battery = f"{page[1]}%"
        if page[7]:
            self._set_value(page[7], now)

    def _power(self, page: bytes, now: float) -> None:
        """Decode a standard power page (0x10), with optional pedal balance and cadence.

        A page whose event counter hasn't moved repeats the previous measurement,
        so it only extends the current value (or starts coasting).
        """
        power = self._event_power(
            page[1], page[4] | page[5] << 8, page[6] | page[7] << 8, now
        )
        if power is None:
            self._hold_or_coast(now)
            return
        self.cadence = None if page[3] == 0xFF else page[3]
        # Bit 7 set means the percentage is the right leg's share; single-sided
        # meters send 0xFF
        balance = page[2]
        self.right_balance = (
            balance & 0x7F if balance != 0xFF and balance & 0x80 else None
        )
        self._set_value(power, now)

    def _event_power(
        self, event: int, accumulated: int, instantaneous: int, now: float
    ) -> int | None:
        """Mean power over every event since the previous power page.

        Torque-based meters send their power page only every few messages, so
        the instantaneous value alone would skip the events in between. Returns
        None if the event count hasn't moved, i.e. no new measurement.
        """
        last = self._event
        if last is not None and event == last[0]:
            return None
        self._event = (event, accumulated, now)
        if last is None or now - last[2] > EVENT_GAP:
            return instantaneous
        events = (event - last[0]) % 256
        return round((accumulated - last[1]) % 65536 / events)

    def _hold_or_coast(self, now: float) -> None:
        """Handle a page without a new measurement: keep the value, or show 0 W.

        While the cranks stand still, meters keep repeating their last message,
        power included. Head units show 0 W once the event counter has been
        still for COAST_TIMEOUT, and so do we; until then the last value is
        held so a brief gap doesn't dip the average.
        """
        if self._event is not None and now - self._event[2] >= COAST_TIMEOUT:
            self.cadence = 0
            self.right_balance = None
            self._set_value(0, now)
        elif self.value is not None:
            self._set_value(self.value, now)

    def _trainer(self, page: bytes, now: float) -> None:
        """Decode a fitness equipment (FE-C) page from a smart trainer.

        Trainers spread their state over several pages: speed (0x10), resistance
        and incline (0x11) and power with calibration and target status (0x19);
        each fills in only its own fields. The "spindown needed" status bit is
        deliberately not reported, see TRAINER_STATUS.
        """
        number = page[0]
        if number in FE_PAGES_WITH_STATE:
            self.fe_state = FE_STATES.get(page[7] >> 4 & 0x07)
        if number == 0x10:
            # 0.001 m/s -> km/h
            self.speed = (page[4] | page[5] << 8) * 0.0036
        elif number == 0x11:
            incline = int.from_bytes(page[4:6], "little", signed=True)
            self.incline = None if incline == 0x7FFF else incline / 100
            self.resistance = None if page[6] == 0xFF else page[6] / 2
        elif number == 0x19:
            instantaneous = page[5] | (page[6] & 0x0F) << 8
            if instantaneous != 0xFFF:
                power = self._event_power(
                    page[1], page[3] | page[4] << 8, instantaneous, now
                )
                if power is None:
                    self._hold_or_coast(now)
                else:
                    self.cadence = None if page[2] == 0xFF else page[2]
                    self._set_value(power, now)
            status = page[6] >> 4
            self.trainer_status = tuple(
                text for bit, text in TRAINER_STATUS.items() if status & bit
            )
            self.target_power_status = TARGET_POWER_STATUS.get(page[7] & 0x03)
