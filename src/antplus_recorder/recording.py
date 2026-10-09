"""Write rides to disk: a CSV appended every second, and FIT files built from it.

The CSV is the crash-safe record. FIT files are only valid once complete (the
header holds the data size and a CRC ends the file), so they're built from the
CSV when recording ends, or later with --fit-from after a crash.
"""

import csv
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from garmin_fit_sdk import Encoder, Profile
from garmin_fit_sdk.util import convert_datetime_to_timestamp

from .sensors import Sensor

RECORDED_KINDS = ("power", "trainer", "heart_rate")
POWER_KINDS = ("power", "trainer")
CSV_FIELDS = [
    "time",
    "kind",
    "number",
    "name",
    "value",
    "unit",
    "cadence",
    "right_balance",
    "speed",
]

MESG_NUM = Profile["mesg_num"]


class Recorder:
    """Appends one row per sensor and second to a new CSV file."""

    def __init__(self, path: Path, now: float) -> None:
        self.path = path
        self.rows = 0
        # "x" refuses to overwrite an existing file
        self._file = path.open("x", newline="")
        self._writer = csv.DictWriter(self._file, CSV_FIELDS)
        self._writer.writeheader()
        self._file.flush()
        # Rows are stamped from the monotonic clock so they're evenly spaced
        # even if a tick fires a bit early or late
        self._started = now
        self._started_wall = datetime.now().astimezone().replace(microsecond=0)
        self._last = now

    def elapsed(self, now: float) -> float:
        return now - self._started

    def write(self, sensors: list[tuple[Sensor, str | None]], now: float) -> None:
        """Record each sensor's mean value since the previous call."""
        stamp = self._started_wall + timedelta(seconds=round(now - self._started))
        for sensor, name in sensors:
            if sensor.kind not in RECORDED_KINDS:
                continue
            value = sensor.value_since(self._last, now)
            if value is None:
                continue
            self._writer.writerow(
                {
                    "time": stamp.isoformat(),
                    "kind": sensor.kind,
                    "number": sensor.number,
                    "name": name or "",
                    "value": round(value),
                    "unit": sensor.unit,
                    "cadence": sensor.cadence,
                    "right_balance": sensor.right_balance,
                    "speed": None if sensor.speed is None else round(sensor.speed, 2),
                }
            )
            self.rows += 1
        self._last = now
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def write_fit_files(csv_path: Path) -> tuple[list[Path], list[Path]]:
    """Build one FIT activity per power source in the CSV, with heart rate added.

    One file per power meter is what comparison tools like the DC Rainmaker
    Analyzer expect, and every app shows the standard power field. Returns the
    files written and the ones skipped because they already exist.
    """
    heart_rate: dict[datetime, int] = {}
    devices: dict[tuple[str, int], list[dict]] = defaultdict(list)
    with csv_path.open(newline="") as f:
        for row in csv.DictReader(f):
            time = datetime.fromisoformat(row["time"])
            if row["kind"] == "heart_rate":
                heart_rate.setdefault(time, int(row["value"]))
            elif row["kind"] in POWER_KINDS:
                devices[row["kind"], int(row["number"])].append({**row, "time": time})

    written, skipped = [], []
    for (kind, number), rows in devices.items():
        slug = re.sub(r"[^a-z0-9]+", "-", rows[-1]["name"].lower()).strip("-")
        path = csv_path.with_name(f"{csv_path.stem}_{kind}-{number}_{slug or kind}.fit")
        try:
            with path.open("xb") as f:
                f.write(_encode_activity(rows, heart_rate))
            written.append(path)
        except FileExistsError:
            skipped.append(path)
    return written, skipped


def _optional_int(value: str) -> int | None:
    return int(value) if value else None


def _encode_activity(rows: list[dict], heart_rate: dict[datetime, int]) -> bytes:
    start, end = rows[0]["time"], rows[-1]["time"]
    elapsed = (end - start).total_seconds() + 1
    encoder = Encoder()
    encoder.write_mesg(
        {
            "mesg_num": MESG_NUM["FILE_ID"],
            "type": "activity",
            "manufacturer": "development",
            "product": 0,
            "time_created": start,
        }
    )
    encoder.write_mesg(
        {
            "mesg_num": MESG_NUM["EVENT"],
            "timestamp": start,
            "event": "timer",
            "event_type": "start",
        }
    )

    powers, cadences, heart_rates = [], [], []
    distance = 0.0
    for row in rows:
        record = {
            "mesg_num": MESG_NUM["RECORD"],
            "timestamp": row["time"],
            "power": int(row["value"]),
        }
        powers.append(record["power"])
        if (cadence := _optional_int(row["cadence"])) is not None:
            record["cadence"] = cadence
            cadences.append(cadence)
        if (balance := _optional_int(row["right_balance"])) is not None:
            # Same encoding as ANT+: bit 7 marks the percentage as the right leg's
            record["left_right_balance"] = balance | 0x80
        if row["speed"]:
            speed = float(row["speed"]) / 3.6  # km/h -> m/s
            distance += speed  # one row per second
            record["speed"] = speed
            record["distance"] = distance
        if (bpm := heart_rate.get(row["time"])) is not None:
            record["heart_rate"] = bpm
            heart_rates.append(bpm)
        encoder.write_mesg(record)

    encoder.write_mesg(
        {
            "mesg_num": MESG_NUM["EVENT"],
            "timestamp": end,
            "event": "timer",
            "event_type": "stop_all",
        }
    )
    summary = {
        "timestamp": end,
        "start_time": start,
        "total_elapsed_time": elapsed,
        "total_timer_time": elapsed,
        "avg_power": round(sum(powers) / len(powers)),
        "max_power": max(powers),
        "sport": "cycling",
        "sub_sport": "indoor_cycling",
        "event": "lap",
        "event_type": "stop",
    }
    if cadences:
        summary["avg_cadence"] = round(sum(cadences) / len(cadences))
    if heart_rates:
        summary["avg_heart_rate"] = round(sum(heart_rates) / len(heart_rates))
        summary["max_heart_rate"] = max(heart_rates)
    if distance:
        summary["total_distance"] = distance
    encoder.write_mesg({"mesg_num": MESG_NUM["LAP"], "message_index": 0, **summary})
    encoder.write_mesg(
        {
            "mesg_num": MESG_NUM["SESSION"],
            "message_index": 0,
            "first_lap_index": 0,
            "num_laps": 1,
            "trigger": "activity_end",
            **summary,
            "event": "session",
        }
    )
    encoder.write_mesg(
        {
            "mesg_num": MESG_NUM["ACTIVITY"],
            "timestamp": end,
            # FIT local time is the UTC timestamp shifted by the UTC offset
            "local_timestamp": convert_datetime_to_timestamp(end)
            + int((end.utcoffset() or timedelta()).total_seconds()),
            "total_timer_time": elapsed,
            "num_sessions": 1,
            "type": "manual",
            "event": "activity",
            "event_type": "stop",
        }
    )
    return encoder.close()
