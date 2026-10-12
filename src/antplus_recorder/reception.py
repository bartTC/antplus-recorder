"""How much of what each ANT+ channel sent arrived, from a raw log's timestamps.

Every ANT+ profile broadcasts at a fixed period, so the interval between two
received messages of a channel tells how many were missed in between. Counted
per channel, not per device: a Kickr sends power and FE-C, a heart rate strap
heart rate and a footpod profile, each with its own period.

A pause of more than BREAK seconds is a break, not loss: the sensor sleeps
(cranks standing still) or is out of range. Breaks are counted on their own,
and counting starts over after one.

Loss comes per minute, because in scan mode two sensors with almost the same
period collide every time their send times drift past each other. That shows as
loss in regular waves; even loss, or loss on one sensor only, points to the
radio or the sensor instead.

The usual interval tells sending behavior from loss: if it is a whole multiple
of the period (a Kickr's FE-C channel arrives every 750 ms instead of 250 ms),
the sensor sends less often than its profile says, or something hits the same
slots every time. Random loss leaves the usual interval at the period.
"""

import csv
import itertools
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .sensors import KINDS

STICK_CLOCK = 32768  # ticks per second of the stick's receive timestamp
STICK_WRAP = 0x10000 / STICK_CLOCK  # the 16-bit timestamp wraps every 2 s
BREAK = 2.0  # seconds without a message that count as a break, not as loss

# Channel periods of the ANT+ profiles, in stick ticks, as openant opens them
PERIODS = {
    11: 8182,  # bicycle power
    17: 8192,  # fitness equipment (FE-C)
    120: 8070,  # heart rate
    121: 8086,  # speed and cadence
    122: 8102,  # cadence
    123: 8118,  # speed
}


def stick_time(data: Sequence[int]) -> int | None:
    """The stick's receive timestamp from the extended data after the page, if any."""
    if len(data) < 9:
        return None
    flags, offset = data[8], 9
    if flags & 0x80:  # channel ID
        offset += 4
    if flags & 0x40:  # signal: dBm (type 0x20) is 3 bytes, AGC (type 0x10) 4
        if len(data) <= offset or data[offset] not in (0x10, 0x20):
            return None
        offset += 4 if data[offset] == 0x10 else 3
    if flags & 0x20 and len(data) >= offset + 2:
        return data[offset] | data[offset + 1] << 8
    return None


def interval(seconds: float, ticks_before: int | None, ticks: int | None) -> float:
    """Seconds between two messages, as exact as the timestamps allow.

    The stick's timestamp is exact but wraps every 2 s, the computer's clock
    counts on but jitters with USB; the latter only picks how many wraps.
    """
    if ticks_before is None or ticks is None:
        return seconds
    fine = (ticks - ticks_before) % 0x10000 / STICK_CLOCK
    return fine + max(round((seconds - fine) / STICK_WRAP), 0) * STICK_WRAP


@dataclass
class Minute:
    """Messages received and missed in one minute of a ride, for one channel."""

    received: int = 0
    missed: int = 0

    @property
    def loss(self) -> float | None:
        expected = self.received + self.missed
        return self.missed / expected * 100 if expected else None


@dataclass
class Channel:
    """Reception of one device's channel: how often it should send, how often it
    did, and where the gaps were.

    The period is the profile's where known; otherwise it is estimated from the
    usual interval, which assumes loss is the exception.
    """

    device_type: int
    number: int
    period: float  # seconds
    estimated: bool  # period taken from the intervals, the profile isn't known
    usual: float  # median interval in seconds, breaks left out
    minutes: dict[int, Minute] = field(default_factory=lambda: defaultdict(Minute))
    breaks: int = 0
    break_seconds: float = 0.0

    @property
    def kind(self) -> str:
        return KINDS.get(self.device_type, f"type_{self.device_type}")

    @property
    def received(self) -> int:
        return sum(m.received for m in self.minutes.values())

    @property
    def missed(self) -> int:
        return sum(m.missed for m in self.minutes.values())

    @property
    def loss(self) -> float | None:
        return Minute(self.received, self.missed).loss


def analyse(
    channels: dict[tuple[int, int], list[tuple[float, int | None]]],
) -> list[Channel]:
    """Loss per channel and minute from each message's (seconds, stick timestamp).

    For each pair of consecutive messages, the number of periods that fit into
    the gap, minus one, is the number of missed messages. Gaps over BREAK
    seconds are breaks and aren't counted as loss. Channels that never had two
    messages close together have no period to speak of and are left out.
    """
    result = []
    for (device_type, number), messages in sorted(channels.items()):
        intervals = [
            interval(b[0] - a[0], a[1], b[1]) for a, b in itertools.pairwise(messages)
        ]
        short = [i for i in intervals if i <= BREAK]
        if not short:
            continue
        usual = statistics.median(short)
        if device_type in PERIODS:
            period, estimated = PERIODS[device_type] / STICK_CLOCK, False
        else:
            period, estimated = usual, True

        channel = Channel(device_type, number, period, estimated, usual)
        channel.minutes[int(messages[0][0] // 60)].received += 1
        for (before, _), (seconds, _), gap in zip(
            messages, messages[1:], intervals, strict=False
        ):
            channel.minutes[int(seconds // 60)].received += 1
            if gap > BREAK:
                channel.breaks += 1
                channel.break_seconds += gap
                continue
            # Each missed slot counts in the minute it would have been sent in
            for slot in range(1, round(gap / period)):
                channel.minutes[int((before + slot * period) // 60)].missed += 1
        result.append(channel)
    return result


def write_csv(path: Path, channels: Iterable[Channel], started: datetime) -> None:
    """One row per channel and minute, for finding patterns such as collision waves."""
    # "x" refuses to overwrite an existing file
    with path.open("x", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["minute", "kind", "number", "received", "missed", "loss"])
        for channel in channels:
            for index, minute in sorted(channel.minutes.items()):
                loss = minute.loss
                writer.writerow(
                    [
                        (started + timedelta(minutes=index)).isoformat(),
                        channel.kind,
                        channel.number,
                        minute.received,
                        minute.missed,
                        "" if loss is None else f"{loss:.1f}",
                    ]
                )


def sparkline(values: Sequence[float | None], top: float) -> str:
    """One character per minute, scaled to `top`; a dot for no loss."""
    bars = "▁▂▃▄▅▆▇█"
    return "".join(
        " "
        if v is None
        else "·"
        if v == 0
        else bars[min(int(v / top * 7), 7)]
        if top
        else "·"
        for v in values
    )
