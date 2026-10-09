import csv
from datetime import datetime

import pytest

from antplus_recorder import reception
from antplus_recorder.reception import STICK_CLOCK, analyse, interval, stick_time
from tests.pages import extended, heart_rate_page

HR_PERIOD = 8070 / STICK_CLOCK


def heart_rate_channel(slots, skip=lambda _: False):
    """(seconds, stick timestamp) for every heart rate slot that isn't skipped."""
    return [
        (slot * HR_PERIOD + 0.003 * (slot % 3), slot * 8070 % 0x10000)
        for slot in range(slots)
        if not skip(slot)
    ]


def test_stick_time_after_agc_and_dbm_blocks():
    page = heart_rate_page(120)
    agc = extended(page, 1, 120, stick_time=0x1A5A)  # flags 0xE0, 4-byte AGC block
    assert stick_time(agc) == 0x1A5A
    dbm = [*page, 0xE0, 1, 0, 120, 5, 0x20, 0xC4, 0x00, 0x34, 0x12]
    assert stick_time(dbm) == 0x1234
    timestamp_only = [*page, 0xA0, 1, 0, 120, 5, 0x34, 0x12]
    assert stick_time(timestamp_only) == 0x1234
    assert stick_time(extended(page, 1, 120)) is None


def test_interval_takes_wraps_from_the_computer_clock():
    # 4.6 s apart: the stick's 2 s counter shows 0.6 s, the computer ~4.6 s
    ticks = round(0.6 * STICK_CLOCK)
    assert interval(4.61, 0, ticks) == pytest.approx(4.6, abs=1e-4)
    assert interval(0.2501, 100, 100 + 8192) == pytest.approx(0.25)
    assert interval(0.3, None, 5) == 0.3


def test_missed_slots_count_as_loss():
    (channel,) = analyse({(120, 1): heart_rate_channel(400, lambda s: s % 5 == 4)})
    assert channel.received == 320
    assert channel.missed == 79  # the last slot's miss is never followed by a message
    assert channel.loss == pytest.approx(79 / 399 * 100)
    assert channel.usual == pytest.approx(HR_PERIOD)
    assert not channel.estimated


def test_a_sleeping_sensor_is_a_break_not_loss():
    awake = heart_rate_channel(100)
    later = [(s + 30, t) for s, t in heart_rate_channel(100)]
    (channel,) = analyse({(120, 1): awake + later})
    assert channel.missed == 0
    assert channel.breaks == 1
    assert channel.break_seconds == pytest.approx(30 - 99 * HR_PERIOD, abs=0.01)


def test_loss_per_minute_shows_where_it_happened():
    # Loss only in the second minute
    slots = int(180 / HR_PERIOD)
    (channel,) = analyse(
        {
            (120, 1): heart_rate_channel(
                slots, lambda s: 60 <= s * HR_PERIOD < 120 and s % 2
            )
        }
    )
    assert channel.minutes[0].loss == 0
    assert channel.minutes[1].loss == pytest.approx(50, abs=1)
    assert channel.minutes[2].loss == 0


def test_sending_every_third_slot_shows_in_the_usual_interval():
    (channel,) = analyse(
        {(17, 1): [(i * 0.75, i * 3 * 8192 % 0x10000) for i in range(40)]}
    )
    assert channel.usual == pytest.approx(0.75)
    # 40 messages, 2 missed slots in each of the 39 intervals between them
    assert channel.loss == pytest.approx(78 / 118 * 100)


def test_unknown_profiles_get_an_estimated_period():
    (channel,) = analyse({(35, 1): [(i * 0.125, None) for i in range(50)]})
    assert channel.estimated
    assert channel.period == pytest.approx(0.125)
    assert channel.missed == 0


def test_csv_has_a_row_per_channel_and_minute(tmp_path):
    channels = analyse({(120, 190197): heart_rate_channel(int(90 / HR_PERIOD))})
    path = tmp_path / "ride_replay_reception.csv"
    reception.write_csv(
        path, channels, datetime.fromisoformat("2026-10-09T18:00:00+02:00")
    )
    rows = list(csv.DictReader(path.open()))
    assert [(r["minute"], r["kind"], r["loss"]) for r in rows] == [
        ("2026-10-09T18:00:00+02:00", "heart_rate", "0.0"),
        ("2026-10-09T18:01:00+02:00", "heart_rate", "0.0"),
    ]


def test_sparkline_marks_no_loss_and_no_data_apart():
    assert reception.sparkline([0, None, 10, 5], top=10) == "· █▄"
