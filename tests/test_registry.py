import os

from antplus_recorder.registry import Registry
from antplus_recorder.sensors import Sensor


def stages():
    sensor = Sensor(11, 57587)
    sensor.update(bytes([0x50, 0xFF, 0xFF, 7, 69, 0, 3, 0]), 0.0)
    return sensor


def touch_later(path):
    """Edits within the same clock tick would look unchanged."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def test_new_device_is_written_with_empty_name(tmp_path):
    registry = Registry(tmp_path / "devices.toml")
    registry.record(stages())
    text = (tmp_path / "devices.toml").read_text()
    assert "[power.57587]" in text
    assert 'name = ""' in text
    assert 'manufacturer = "Stages Cycling"' in text


def test_name_set_by_hand_shows_up_and_survives(tmp_path):
    path = tmp_path / "devices.toml"
    registry = Registry(path)
    registry.record(stages())
    path.write_text(path.read_text().replace('name = ""', 'name = "Crank"'))
    touch_later(path)
    registry.reload()
    assert registry.label(stages()) == "Crank"

    sensor = stages()
    sensor.serial = 1234
    registry.record(sensor)
    assert 'name = "Crank"' in path.read_text()
    assert "serial = 1234" in path.read_text()


def test_broken_file_is_left_alone(tmp_path):
    path = tmp_path / "devices.toml"
    registry = Registry(path)
    registry.record(stages())
    path.write_text("[power.57587\nname = ")  # saved halfway through an edit
    touch_later(path)
    other = Sensor(11, 1)
    registry.record(other)
    assert path.read_text() == "[power.57587\nname = "


def test_manufacturer_is_remembered_until_decoded(tmp_path):
    registry = Registry(tmp_path / "devices.toml")
    registry.record(stages())
    assert registry.manufacturer(Sensor(11, 57587)) == "Stages Cycling"


def test_reference_is_set_and_cleared(tmp_path):
    registry = Registry(tmp_path / "devices.toml")
    kickr = Sensor(11, 19696)
    registry.set_reference(stages())
    assert registry.reference() == ("power", 57587)
    registry.set_reference(kickr)
    assert registry.reference() == ("power", 19696)
    assert (tmp_path / "devices.toml").read_text().count("reference") == 1
    registry.set_reference(None)
    assert registry.reference() is None
