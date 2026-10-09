"""Every ANT+ message of a ride, to recompute it later with the current decoding.

The log keeps the bytes exactly as the stick delivers them, nothing decoded:
what the app makes of them belongs to the analysis, so any old log can be
replayed with a newer decoder. One tab-separated line per entry:

    type    seconds since start    unix time    fields

- `meta`: details about the ride (start, app version, stick, notes given at start)
- `msg`: a received message as hex: 8-byte page, flag byte, channel ID, and
  where the stick supports them RSSI and its own receive timestamp
- `event`: something the stick reported, e.g. a serial queue overflow
- `note`: a note typed in during the ride, e.g. "Stages zeroed"

The seconds count up from the monotonic clock for exact intervals; the unix
time lines the ride up with files from other devices, e.g. a head unit's FIT.
"""

import gzip
import itertools
import statistics
import threading
import time
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .receiver import Receiver
from .recording import Recorder
from .sensors import Sensor

SUFFIX = ".ant.gz"
HEADER = "# antplus-recorder raw ANT+ log, format 1"


class RawLog:
    def __init__(self, path: Path, now: float, started: datetime) -> None:
        self.path = path
        self.messages = 0
        # "x" refuses to overwrite an existing file
        self._file = gzip.open(path, "xt", encoding="utf-8")  # noqa: SIM115
        self._file.write(f"{HEADER}\n# type\tseconds since start\tunix time\tfields\n")
        self._started = now
        self._flushed = now
        # Messages arrive on the receiver's thread, notes and closing on the main one
        self._lock = threading.Lock()
        self.meta("started", started.isoformat(), now)

    def meta(self, key: str, value: str, now: float) -> None:
        self._write("meta", now, f"{key}\t{value}")

    def message(self, data: Sequence[int], now: float) -> None:
        self.messages += 1
        self._write("msg", now, bytes(data).hex(" "))

    def event(self, text: str, now: float) -> None:
        self._write("event", now, text)

    def note(self, text: str, now: float) -> None:
        self._write("note", now, " ".join(text.split()))

    def _write(self, kind: str, now: float, fields: str) -> None:
        with self._lock:
            if self._file.closed:
                return
            self._file.write(
                f"{kind}\t{now - self._started:.4f}\t{time.time():.3f}\t{fields}\n"
            )
            if now - self._flushed >= 1:
                # A sync flush keeps everything up to here readable after a crash
                self._file.flush()
                self._flushed = now

    def close(self) -> None:
        with self._lock:
            self._file.close()


@dataclass
class Entry:
    kind: str
    seconds: float
    unix_time: float
    fields: list[str]


def read(path: Path) -> Iterator[Entry]:
    """Entries of a raw log, as far as it got before a crash."""
    with gzip.open(path, "rt", encoding="utf-8") as file:
        if file.readline().strip() != HEADER:
            msg = f"{path} is not an antplus-recorder raw log"
            raise ValueError(msg)
        try:
            for line in file:
                if line.startswith("#"):
                    continue
                kind, seconds, unix_time, *fields = line.rstrip("\n").split("\t")
                try:
                    yield Entry(kind, float(seconds), float(unix_time), fields)
                except ValueError:
                    return  # the line a crash cut off
        except (EOFError, zlib.error):
            return  # no end-of-stream marker after a crash


@dataclass
class Replay:
    """What a replay found, besides the CSV."""

    recorder: Recorder
    meta: dict[str, str] = field(default_factory=dict)
    notes: list[tuple[float, str]] = field(default_factory=list)
    events: list[tuple[float, str]] = field(default_factory=list)
    # Per device (kind, number): times of its messages
    times: dict[tuple[str, int], list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )


def replay(
    path: Path, label: Callable[[Sensor], str | None] = lambda _: None
) -> Replay:
    """Rebuild a ride's CSV from its raw log, next to it as `<ride>_replay.csv`.

    The messages go through the receiver and the recorder writes once per
    second of the ride, as during the live ride.
    """
    entries = read(path)
    meta: dict[str, str] = {}
    first = next(entries, None)
    if first is None or first.kind != "meta" or first.fields[0] != "started":
        msg = f"{path} has no start time"
        raise ValueError(msg)
    started = datetime.fromisoformat(first.fields[1])

    receiver = Receiver()
    stem = path.name.removesuffix(SUFFIX)
    result = Replay(Recorder(path.with_name(f"{stem}_replay.csv"), 0.0, started))
    result.meta["started"] = first.fields[1]

    def record(second: float) -> None:
        sensors = list(receiver.sensors.values())
        result.recorder.write([(s, label(s)) for s in sensors], second)

    second = 1.0
    for entry in entries:
        while entry.seconds > second:
            record(second)
            second += 1
        if entry.kind == "msg":
            data = list(bytes.fromhex(entry.fields[0]))
            receiver.handle(data, entry.seconds)
            if len(data) >= 13:
                number = data[9] | data[10] << 8 | (data[12] & 0xF0) << 12
                sensor = receiver.sensors.get((data[11] & 0x7F, number))
                if sensor:
                    result.times[sensor.kind, number].append(entry.seconds)
        elif entry.kind == "meta" and entry.fields[0] == "note":
            result.notes.append((entry.unix_time, entry.fields[1]))
        elif entry.kind == "meta":
            meta[entry.fields[0]] = entry.fields[1]
        elif entry.kind == "note":
            result.notes.append((entry.unix_time, entry.fields[0]))
        elif entry.kind == "event":
            result.events.append((entry.seconds, entry.fields[0]))
    record(second)
    result.recorder.close()
    result.meta.update(meta)
    return result


def longest_gap(times: list[float]) -> float:
    return max((b - a for a, b in itertools.pairwise(times)), default=0.0)


def median_interval(times: list[float]) -> float:
    return (
        statistics.median(b - a for a, b in itertools.pairwise(times))
        if len(times) > 1
        else 0.0
    )
