"""devices.toml: every sensor that sent a reading, with an optional name set by the user.

The file is shared between the app and the user: the app adds devices and
fills in what it decodes (manufacturer, model, serial, which meter is the
reference), the user edits `name` in a text editor while the app runs. The
registry therefore re-reads the file whenever it changes, and edits it in
place with tomlkit so the user's comments and layout survive.
"""

from pathlib import Path

import tomlkit
from tomlkit.exceptions import ParseError
from tomlkit.items import Table

from .sensors import Sensor

HEADER = (
    "Every ANT+ device that sent a reading to antplus-recorder, by kind and device number.\n"
    "Set `name` to label a device on screen. The other fields are filled in automatically."
)


class Registry:
    """The device list in `devices.toml`, kept in step with the file on disk.

    Sensors are looked up by kind and device number. Reads see the user's
    latest edits; writes never touch a name and are skipped while the file
    doesn't parse, so an edit in progress isn't overwritten.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: int | None = None
        self._valid = True
        self._doc = tomlkit.document()
        for line in HEADER.splitlines():
            self._doc.add(tomlkit.comment(line))
        self.reload()

    def reload(self) -> None:
        """Re-read the file if it changed on disk, e.g. after the user named a device.

        A file that doesn't parse (saved halfway through an edit) keeps the
        last good contents and blocks writes until it parses again. A missing
        file keeps the in-memory document, which is written out on the next
        change.
        """
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self._doc = tomlkit.parse(self.path.read_text(encoding="utf-8"))
            self._valid = True
        except ParseError:
            # Probably saved halfway through an edit: keep the last good names
            # and don't overwrite the user's file until it parses again
            self._valid = False

    def _entry(self, sensor: Sensor) -> Table | dict:
        """The sensor's table in the document, or an empty dict if it isn't listed."""
        return self._doc.get(sensor.kind, {}).get(str(sensor.number), {})

    def name(self, sensor: Sensor) -> str | None:
        """The name the user gave the sensor, None while it is empty."""
        return self._entry(sensor).get("name") or None

    def manufacturer(self, sensor: Sensor) -> str | None:
        """The manufacturer, decoded live or remembered from an earlier session.

        The live value wins; the stored one fills the gap until the sensor's
        manufacturer page comes round again.
        """
        return sensor.manufacturer or self._entry(sensor).get("manufacturer")

    def label(self, sensor: Sensor) -> str | None:
        """What to call the sensor on screen and in recordings: its name, else its manufacturer."""
        return self.name(sensor) or self.manufacturer(sensor)

    def reference(self) -> tuple[str, int] | None:
        """Kind and number of the device other power meters are compared against.

        Reflects the file as of the last `reload`, so setting `reference = true`
        by hand works as well as clicking a tile. Returns None if none is set.
        """
        for kind, devices in self._doc.items():
            if not isinstance(devices, dict):
                continue
            for number, entry in devices.items():
                if isinstance(entry, dict) and entry.get("reference"):
                    return kind, int(number)
        return None

    def set_reference(self, sensor: Sensor | None) -> None:
        """Make `sensor` the reference, or clear it with None.

        At most one device is the reference, so the flag is removed from all
        others first. The sensor is added to the file if it isn't listed yet.
        """
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
        """Add the sensor, or fill in details decoded since, never touching its name.

        New devices get an empty `name` for the user to fill in. The file is
        only written when something changed, as this runs every second for
        every sensor.
        """
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
        """Save the document, remembering its mtime so `reload` ignores our own write."""
        self.path.write_text(tomlkit.dumps(self._doc), encoding="utf-8")
        self._mtime = self.path.stat().st_mtime_ns
