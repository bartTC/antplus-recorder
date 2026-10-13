"""Every ANT+ message of a ride, to recompute it later with the current decoding.

The log keeps the bytes exactly as the stick delivers them, nothing decoded:
what the app makes of them belongs to the analysis, so any old log can be
replayed with a newer decoder. One tab-separated line per entry:

    type    seconds since start    unix time    fields

- `meta`: details about the ride (start, app version, stick, notes given at start)
- `msg`: a received message as hex: 8-byte page, flag byte, channel ID, and
  where the stick supports them a signal block and its own receive timestamp
- `event`: something the stick reported, e.g. a serial queue overflow
- `note`: a note typed in during the ride, e.g. "Stages zeroed"
- `lap`: the lap key was pressed; the field is the number of the lap that starts

The seconds count up from the monotonic clock for exact intervals; the unix
time lines the ride up with files from other devices, e.g. a head unit's FIT.
"""

import gzip
import threading
import time
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .receiver import Receiver
from .reception import stick_time
from .recording import Recorder
from .sensors import Sensor

SUFFIX = ".ant.gz"
HEADER = "# antplus-recorder raw ANT+ log, format 1"


class RawLog:
    """Writes the raw log of a ride as gzipped text, one entry per line.

    Messages arrive on the receiver's thread and notes on the main one, so
    every write takes a lock. The file is flushed at most once a second to keep
    the cost low while bounding the loss in a crash: gzip data up to the last
    flush stays readable, see `read`.
    """

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
        """Record a detail about the ride, such as the app version or the stick in use."""
        self._write("meta", now, f"{key}\t{value}")

    def message(self, data: Sequence[int], now: float) -> None:
        """Record a received message exactly as the stick delivered it."""
        self.messages += 1
        self._write("msg", now, bytes(data).hex(" "))

    def event(self, text: str, now: float) -> None:
        """Record something the stick reported besides data, e.g. a serial queue overflow."""
        self._write("event", now, text)

    def note(self, text: str, now: float, unix_time: float | None = None) -> None:
        """Record a note typed in during the ride, collapsed onto one line.

        `now` and `unix_time` are the moment the key was pressed, which is
        earlier than the entry is written, as typing takes a while.
        """
        self._write("note", now, " ".join(text.split()), unix_time)

    def _write(
        self, kind: str, now: float, fields: str, unix_time: float | None = None
    ) -> None:
        """Append one line, stamped with both clocks (the wall clock now by default).

        Silently does nothing once the log is closed, as the receiver's thread can
        still deliver a message while the app shuts down.
        """
        with self._lock:
            if self._file.closed:
                return
            self._file.write(
                f"{kind}\t{now - self._started:.4f}\t{unix_time or time.time():.3f}\t{fields}\n"
            )
            if now - self._flushed >= 1:
                # A sync flush keeps everything up to here readable after a crash
                self._file.flush()
                self._flushed = now

    def lap(self, number: int, now: float) -> None:
        """Record that lap `number` started, so a replay puts it on the same row."""
        self._write("lap", now, str(number))

    def close(self) -> None:
        """Finish the gzip stream; entries written after this are dropped."""
        with self._lock:
            self._file.close()


@dataclass
class Entry:
    """One line of a raw log: type, seconds into the ride, unix time and the other fields."""

    kind: str
    seconds: float
    unix_time: float
    fields: list[str]


def read(path: Path) -> Iterator[Entry]:
    """Entries of a raw log, as far as it got before a crash.

    A log that ended without a clean close is cut off at an arbitrary point:
    the gzip stream has no end marker, and the last line may be half written.
    Both end the iteration instead of raising, so everything recorded up to
    the last flush can still be replayed. Raises ValueError if the file isn't a
    raw log at all.
    """
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
    # Per channel (device type, number): seconds and stick timestamp of each message
    channels: dict[tuple[int, int], list[tuple[float, int | None]]] = field(
        default_factory=lambda: defaultdict(list)
    )


def replay(
    path: Path, label: Callable[[Sensor], str | None] = lambda _: None
) -> Replay:
    """Rebuild a ride's CSV from its raw log, next to it as `<ride>_replay.csv`.

    The messages go through the receiver and the recorder writes once per
    second of the ride, as during the live ride. Since the log holds the bytes
    undecoded, replaying with a newer version applies its decoder to an old
    ride; that is the point of keeping the raw log.

    `label` names a sensor for the CSV, as the registry does live. Besides the
    CSV, the result carries what the reception analysis needs. Raises
    ValueError if the log has no start time, and FileExistsError if the replay
    CSV already exists.
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
                result.channels[data[11] & 0x7F, number].append(
                    (entry.seconds, stick_time(data))
                )
        elif entry.kind == "meta" and entry.fields[0] == "note":
            result.notes.append((entry.unix_time, entry.fields[1]))
        elif entry.kind == "meta":
            meta[entry.fields[0]] = entry.fields[1]
        elif entry.kind == "note":
            result.notes.append((entry.unix_time, entry.fields[0]))
            result.recorder.note(entry.fields[0], entry.seconds)
        elif entry.kind == "lap":
            result.recorder.new_lap()
        elif entry.kind == "event":
            result.events.append((entry.seconds, entry.fields[0]))
    record(second)
    result.recorder.close()
    result.meta.update(meta)
    return result
