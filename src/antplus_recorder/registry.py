"""devices.toml: every sensor seen so far, with an optional name set by the user."""

from pathlib import Path

import tomlkit
from tomlkit.exceptions import ParseError
from tomlkit.items import Table

from .sensors import Sensor

HEADER = (
    "Every ANT+ device antplus-recorder has seen, by kind and device number.\n"
    "Set `name` to label a device on screen. The other fields are filled in automatically."
)


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: int | None = None
        self._valid = True
        self._doc = tomlkit.document()
        for line in HEADER.splitlines():
            self._doc.add(tomlkit.comment(line))
        self.reload()

    def reload(self) -> None:
        """Re-read the file if it changed on disk, e.g. after the user named a device."""
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self._doc = tomlkit.parse(self.path.read_text())
            self._valid = True
        except ParseError:
            # Probably saved halfway through an edit: keep the last good names
            # and don't overwrite the user's file until it parses again
            self._valid = False

    def _entry(self, sensor: Sensor) -> Table | dict:
        return self._doc.get(sensor.kind, {}).get(str(sensor.number), {})

    def name(self, sensor: Sensor) -> str | None:
        return self._entry(sensor).get("name") or None

    def manufacturer(self, sensor: Sensor) -> str | None:
        """Decoded live, or remembered from an earlier session until the
        manufacturer page comes round again."""
        return sensor.manufacturer or self._entry(sensor).get("manufacturer")

    def label(self, sensor: Sensor) -> str | None:
        return self.name(sensor) or self.manufacturer(sensor)

    def reference(self) -> tuple[str, int] | None:
        """Kind and number of the device other power meters are compared against."""
        for kind, devices in self._doc.items():
            if not isinstance(devices, dict):
                continue
            for number, entry in devices.items():
                if isinstance(entry, dict) and entry.get("reference"):
                    return kind, int(number)
        return None

    def set_reference(self, sensor: Sensor | None) -> None:
        """Make `sensor` the reference, or clear it with None."""
        self.reload()
        if not self._valid:
            return
        for devices in self._doc.values():
            if isinstance(devices, dict):
                for entry in devices.values():
                    if isinstance(entry, dict):
                        entry.pop("reference", None)
        if sensor is not None:
            self.record(sensor)
            self._entry(sensor)["reference"] = True
        self._write()

    def record(self, sensor: Sensor) -> None:
        """Add the sensor, or fill in details decoded since, never touching its name."""
        self.reload()
        if not self._valid:
            return

        kind = self._doc.get(sensor.kind)
        if kind is None:
            kind = self._doc[sensor.kind] = tomlkit.table(is_super_table=True)
        key = str(sensor.number)
        entry = kind.get(key)
        changed = entry is None
        if entry is None:
            entry = kind[key] = tomlkit.table()
            entry["name"] = ""

        details = {
            "manufacturer": sensor.manufacturer,
            "model": sensor.model,
            "serial": sensor.serial,
        }
        for field, value in details.items():
            if value is not None and entry.get(field) != value:
                entry[field] = value
                changed = True

        if changed:
            self._write()

    def _write(self) -> None:
        self.path.write_text(tomlkit.dumps(self._doc))
        self._mtime = self.path.stat().st_mtime_ns
