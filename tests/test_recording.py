import csv
from datetime import datetime

import pytest
from garmin_fit_sdk import Decoder, Stream

from antplus_recorder.recording import Recorder, write_fit_files
from antplus_recorder.sensors import Sensor
from tests.pages import heart_rate_page, power_page


def ride(tmp_path, seconds=10):
    """Record a Stages, Favero pedals and a heart rate strap for `seconds`."""
    stages, pedals, strap = Sensor(11, 57587), Sensor(11, 4242), Sensor(120, 190197)
    recorder = Recorder(tmp_path / "ride.csv", 0.0)
    for second in range(1, seconds + 1):
        stages.update(power_page(second, second * 210, 210), second - 0.5)
        pedals.update(power_page(second, second * 200, 200, right=52), second - 0.5)
        strap.update(heart_rate_page(130), second - 0.5)
        recorder.write(
            [(stages, "Stages"), (pedals, "Favero"), (strap, None)], float(second)
        )
    recorder.close()
    return recorder


def test_csv_has_one_row_per_sensor_and_second(tmp_path):
    recorder = ride(tmp_path)
    rows = list(csv.DictReader(recorder.path.open()))
    assert recorder.rows == len(rows) == 30
    pedals = [row for row in rows if row["number"] == "4242"]
    assert {row["value"] for row in pedals} == {"200"}
    assert {row["right_balance"] for row in pedals} == {"52"}


def test_csv_holds_value_between_sparse_pages(tmp_path):
    sensor = Sensor(11, 1)
    recorder = Recorder(tmp_path / "ride.csv", 0.0)
    # One page every 2 s, then silence after 5 s
    for second in range(1, 9):
        if second in (1, 3, 5):
            sensor.update(power_page(second, second * 240, 240), second - 0.5)
        recorder.write([(sensor, None)], float(second))
    recorder.close()
    values = [row["value"] for row in csv.DictReader(recorder.path.open())]
    assert values == ["240"] * 7  # held while fresh, nothing once it went quiet


def test_recorder_never_overwrites(tmp_path):
    (tmp_path / "ride.csv").write_text("precious")
    with pytest.raises(FileExistsError):
        Recorder(tmp_path / "ride.csv", 0.0)


def test_one_valid_fit_file_per_power_meter(tmp_path):
    recorder = ride(tmp_path)
    written, skipped = write_fit_files(recorder.path)
    assert sorted(path.name for path in written) == [
        "ride_power-4242_favero.fit",
        "ride_power-57587_stages.fit",
    ]
    assert not skipped

    pedals = next(path for path in written if "4242" in path.name)
    assert Decoder(Stream.from_file(str(pedals))).check_integrity()
    messages, errors = Decoder(Stream.from_file(str(pedals))).read()
    assert not errors
    records = messages["record_mesgs"]
    assert len(records) == 10
    assert {record["power"] for record in records} == {200}
    assert {record["heart_rate"] for record in records} == {130}
    assert {record["left_right_balance"] for record in records} == {0x80 | 52}
    session = messages["session_mesgs"][0]
    assert session["avg_power"] == 200
    assert session["total_elapsed_time"] == 10


def test_fit_files_never_overwrite(tmp_path):
    recorder = ride(tmp_path)
    write_fit_files(recorder.path)
    written, skipped = write_fit_files(recorder.path)
    assert not written
    assert len(skipped) == 2


def test_csv_is_utf8_whatever_the_locale(tmp_path):
    sensor = Sensor(11, 1)
    sensor.update(power_page(1, 200, 200), 0.5)
    recorder = Recorder(tmp_path / "ride.csv", 0.0)
    recorder.write([(sensor, "Büro-Kickr")], 1.0)
    recorder.close()
    assert "Büro-Kickr" in recorder.path.read_bytes().decode("utf-8")
    written, _ = write_fit_files(recorder.path)
    assert written[0].name == "ride_power-1_b-ro-kickr.fit"


def test_csv_numbers_the_laps_from_the_row_after_the_key_press(tmp_path):
    sensor = Sensor(11, 1)
    recorder = Recorder(tmp_path / "ride.csv", 0.0)
    for second in range(1, 7):
        sensor.update(power_page(second, second * 200, 200), second - 0.5)
        if second == 4:
            assert recorder.new_lap() == 2
        recorder.write([(sensor, None)], float(second))
    recorder.close()
    laps = [row["lap"] for row in csv.DictReader(recorder.path.open())]
    assert laps == ["1", "1", "1", "2", "2", "2"]


def test_csv_gets_a_row_for_a_note_at_the_time_of_the_key_press(tmp_path):
    sensor = Sensor(11, 1)
    recorder = Recorder(
        tmp_path / "ride.csv", 0.0, datetime(2026, 10, 9, 19, 0, 0).astimezone()
    )
    sensor.update(power_page(1, 200, 200), 0.5)
    recorder.write([(sensor, None)], 1.0)
    recorder.note("Stages   zeroed", 2.2)
    recorder.close()
    rows = list(csv.DictReader(recorder.path.open()))
    assert [row["kind"] for row in rows] == ["power", "note"]
    assert rows[1]["note"] == "Stages zeroed"
    assert rows[1]["time"].startswith("2026-10-09T19:00:02")
    assert rows[1]["value"] == ""
