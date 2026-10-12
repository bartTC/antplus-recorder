import csv
import gzip
from datetime import datetime

import pytest

from antplus_recorder import rawlog
from antplus_recorder.rawlog import RawLog
from antplus_recorder.receiver import Receiver
from antplus_recorder.recording import Recorder
from tests.pages import extended, heart_rate_page, power_page

STARTED = datetime.fromisoformat("2026-10-09T18:00:00+02:00")


def messages(seconds=10):
    """(time, data) of a Stages sending a power page every 2nd message and a strap."""
    for tick in range(seconds * 4):
        now = tick / 4 + 0.1
        second = int(now)
        if tick % 2 == 0:
            page = power_page(second + 1, (second + 1) * 230, 230, right=51)
            yield now, extended(page, 57587, 11, stick_time=tick * 8192)
        else:
            yield (
                now,
                extended(heart_rate_page(140), 190197 & 0xFFFF, 120, 0x21, tick * 8192),
            )


def record_live(tmp_path, seconds=10):
    """What the app writes during a ride: CSV once per second and the raw log."""
    log = RawLog(tmp_path / f"ride{rawlog.SUFFIX}", 0.0, STARTED)
    receiver = Receiver(log=log)
    recorder = Recorder(tmp_path / "ride.csv", 0.0, STARTED)
    second = 1.0
    for now, data in messages(seconds):
        while now > second:
            recorder.write([(s, None) for s in receiver.sensors.values()], second)
            second += 1
        receiver.handle(data, now)
    recorder.write([(s, None) for s in receiver.sensors.values()], second)
    recorder.close()
    log.close()
    return log, recorder


def rows(path):
    return [
        (row["time"], row["kind"], row["number"], row["value"], row["right_balance"])
        for row in csv.DictReader(path.open())
    ]


def test_log_lines_carry_both_clocks_and_raw_bytes(tmp_path):
    log = RawLog(tmp_path / f"ride{rawlog.SUFFIX}", 100.0, STARTED)
    log.meta("stick", "serial 1", 100.0)
    log.message([0x10, 1, 2, 3, 4, 5, 6, 7, 0xE0, 1, 2, 3, 4], 101.25)
    log.note("Stages  zeroed\n", 102.0)
    log.close()
    entries = list(rawlog.read(log.path))
    assert [(e.kind, e.seconds, e.fields) for e in entries] == [
        ("meta", 0.0, ["started", STARTED.isoformat()]),
        ("meta", 0.0, ["stick", "serial 1"]),
        ("msg", 1.25, ["10 01 02 03 04 05 06 07 e0 01 02 03 04"]),
        ("note", 2.0, ["Stages zeroed"]),
    ]
    assert all(e.unix_time > 1_700_000_000 for e in entries)


def test_replay_gives_the_same_csv_as_the_live_ride(tmp_path):
    log, recorder = record_live(tmp_path)
    result = rawlog.replay(log.path)
    assert result.recorder.path.name == "ride_replay.csv"
    assert rows(result.recorder.path) == rows(recorder.path)
    assert {key: len(messages) for key, messages in result.channels.items()} == {
        (11, 57587): 20,
        (120, 190197): 20,
    }
    # The stick timestamp is kept for the reception analysis
    assert result.channels[11, 57587][1] == (0.6, 2 * 8192)


def test_replay_reads_a_log_cut_off_by_a_crash(tmp_path):
    log, _ = record_live(tmp_path)
    data = log.path.read_bytes()
    log.path.write_bytes(data[: len(data) * 2 // 3])
    entries = list(rawlog.read(log.path))
    assert entries[0].fields[0] == "started"
    assert 0 < len(entries) < 81


def test_replay_never_overwrites(tmp_path):
    log, _ = record_live(tmp_path)
    rawlog.replay(log.path)
    with pytest.raises(FileExistsError):
        rawlog.replay(log.path)


def test_other_files_are_refused(tmp_path):
    path = tmp_path / f"other{rawlog.SUFFIX}"
    with gzip.open(path, "wt") as file:
        file.write("time,value\n")
    with pytest.raises(ValueError, match="not an antplus-recorder raw log"):
        list(rawlog.read(path))


def test_replay_puts_laps_on_the_same_rows_as_the_live_ride(tmp_path):
    log = RawLog(tmp_path / f"ride{rawlog.SUFFIX}", 0.0, STARTED)
    receiver = Receiver(log=log)
    recorder = Recorder(tmp_path / "ride.csv", 0.0, STARTED)
    second = 1.0
    for now, data in messages():
        while now > second:
            recorder.write([(s, None) for s in receiver.sensors.values()], second)
            second += 1
            if second == 5.0:  # key pressed between two ticks
                log.lap(recorder.new_lap(), second - 0.5)
        receiver.handle(data, now)
    recorder.write([(s, None) for s in receiver.sensors.values()], second)
    recorder.close()
    log.close()

    result = rawlog.replay(log.path)
    live = [row["lap"] for row in csv.DictReader(recorder.path.open())]
    assert live[0] == "1"
    assert "2" in live
    assert [row["lap"] for row in csv.DictReader(result.recorder.path.open())] == live
