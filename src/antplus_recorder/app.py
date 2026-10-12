"""Terminal dashboard showing the ANT+ sensors that are sending.

Power meters, smart trainers and heart rate straps each get a tile with the
current value, rolling averages and a trend line. Other devices are only
counted in the title bar; the raw log has them.

The receiver decodes on the stick's thread and the app polls its sensors from
the UI thread: four times a second for the screen, once a second for the CSV
row, the trend lines and the laps against the reference meter.
"""

import errno
import sys
import time
from collections import deque
from typing import ClassVar

from openant.base.driver import DriverNotFound
from openant.easy.exception import AntException
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Digits,
    Footer,
    Header,
    Input,
    Label,
    Sparkline,
)
from usb.core import NoBackendError, USBError

from .comparison import MIN_REFERENCE_POWER, Lap, difference
from .rawlog import RawLog
from .receiver import Receiver, StickUnsupported
from .recording import Recorder
from .registry import Registry
from .sensors import Sensor

NOTHING_FOUND_AFTER = 15.0  # seconds before the empty screen turns into a warning
TILE_KINDS = ("heart_rate", "power", "trainer")  # big tiles, in this order
# Fits "1234 W  100 rpm" and the averages line; as many columns as fit at this width
TILE_MIN_WIDTH = 36
TILE_GUTTER = 2
TILES_PADDING = 4 + 2  # grid padding left and right, and a vertical scrollbar
HISTORY = 120  # seconds of history in each tile's trend line
POWER_WINDOWS = (
    0,
    3,
    10,
    30,
    60,
)  # seconds averaged for the big power number, 0 = live
AVERAGED_KINDS = ("power", "trainer")
# Seconds compared in Live mode: two meters never measure at the same instant,
# so single readings make the difference jump around
LIVE_DELTA_WINDOW = 30


class NoteScreen(ModalScreen[str | None]):
    """Ask for a note to put into the raw log at this moment of the ride."""

    BINDINGS: ClassVar = [("escape", "dismiss", "Cancel")]
    DEFAULT_CSS = """
    NoteScreen { align: center middle; }
    NoteScreen Input { width: 70; border: round $accent; }
    """

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Note, e.g. Stages zeroed  (Enter saves, Esc cancels)")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Close with the typed note, or None if the field was empty."""
        self.dismiss(event.value.strip() or None)


def open_error(e: USBError) -> str:
    """Why the stick couldn't be opened, as far as the USB error code tells."""
    if e.errno == errno.EACCES and sys.platform.startswith("linux"):
        # libusb lists the stick without permission to open it; macOS reports
        # the same code when another app holds it
        return (
            "No permission to use the ANT+ USB stick. Add the udev rule from the "
            "README, reload the rules and plug the stick in again."
        )
    if e.errno in (errno.EACCES, errno.EBUSY):
        return f"Can't open the ANT+ USB stick, is another app using it? ({e})"
    return f"Can't open the ANT+ USB stick ({e})."


def signed(percent: float) -> str:
    """One decimal with a sign, without the "-0.0" a tiny negative rounds to."""
    return f"{round(percent, 1) + 0.0:+.1f}"


class SensorTile(Vertical):
    """One sensor, big: current value, rolling averages and a trend line."""

    def __init__(self, sensor: Sensor) -> None:
        super().__init__(classes=sensor.kind)
        self.sensor = sensor
        self.history: deque[float] = deque([0.0] * HISTORY, maxlen=HISTORY)

    class Selected(Message):
        """A power tile was clicked, to make it the reference or clear it."""

        def __init__(self, sensor: Sensor) -> None:
            super().__init__()
            self.sensor = sensor

    def order(self, now: float) -> tuple[bool, int, int]:
        """Sort key that puts sensors sending now first, then by kind and number.

        Without this a passing heart rate strap or a stray trainer would take
        a place in front of your own power meter. A sensor that goes quiet
        sinks to the end, where it is already dimmed.
        """
        return (
            not self.sensor.is_fresh(now),
            TILE_KINDS.index(self.sensor.kind),
            self.sensor.number,
        )

    def on_click(self) -> None:
        """Offer this tile as the reference meter, if it is one that can be compared."""
        if self.sensor.kind in AVERAGED_KINDS:
            self.post_message(self.Selected(self.sensor))

    def compose(self) -> ComposeResult:
        with Horizontal(classes="head"):
            yield Label(classes="title")
            yield Label(classes="ident")
        with Horizontal(classes="reading"):
            yield Digits("--", classes="value")
            yield Label(f"\n{self.sensor.unit}", classes="unit")
            if self.sensor.kind in AVERAGED_KINDS:
                yield Digits("--", classes="cadence")
                yield Label("\nrpm", classes="unit")
        yield Label(classes="averages")
        yield Label(classes="delta")
        yield Label(classes="details")
        yield Label(classes="status")
        yield Sparkline(self.history)

    def show(self, label: str | None, now: float, window: int) -> None:
        """Update the tile from its sensor's state at monotonic time `now`.

        `window` is the seconds averaged for the big power number, 0 for the
        live value. A sensor that has gone quiet dims and shows `--` instead of
        its last value, with how long it has been silent in the details.
        """
        s = self.sensor
        fresh = s.is_fresh(now)
        self.set_class(not fresh, "stale")
        self.query_one(".title", Label).update(label or s.kind)
        self.query_one(".ident", Label).update(f"{s.kind} #{s.number}")

        if s.kind in AVERAGED_KINDS and window:
            value = s.average(window, now)
            # Window above the unit, so it doesn't widen the line
            self.query_one(".unit", Label).update(f"{window}s\n{s.unit}")
        else:
            value = s.value
            self.query_one(".unit", Label).update(f"\n{s.unit}")
        self.query_one(".value", Digits).update(
            f"{value:.0f}" if fresh and value is not None else "--"
        )
        if s.kind in AVERAGED_KINDS:
            cadence = s.cadence if fresh else None
            self.query_one(".cadence", Digits).update(
                "--" if cadence is None else str(cadence)
            )

        averages = []
        for seconds in (3, 10, 30):
            avg = s.average(seconds, now)
            averages.append(
                f"{seconds}s {avg:.0f}" if avg is not None else f"{seconds}s --"
            )
        self.query_one(".averages", Label).update("  ".join(averages))

        details = []
        right = s.right_balance_average(window, now) if window else s.right_balance
        if fresh and right is not None:
            details.append(f"L{100 - round(right)}/R{round(right)}")
        if s.speed is not None:
            details.append(f"{s.speed:.1f} km/h")
        if s.battery:
            details.append(f"bat {s.battery}")
        details.append(f"{s.rate(now):.1f} Hz")
        if not fresh:
            details.append(f"no data for {now - s.last_seen:.0f}s")
        self.query_one(".details", Label).update("  ".join(details))

        status = []
        if s.fe_state:
            status.append(s.fe_state.upper())
        if s.resistance is not None:
            status.append(f"resistance {s.resistance:.0f}%")
        if s.incline is not None:
            status.append(f"incline {s.incline:.1f}%")
        lines = ["  ".join(status)] if status else []
        # One line per warning, so none of them breaks across lines
        lines += [
            f"[$warning]{text}[/]"
            for text in (*s.trainer_status, s.target_power_status)
            if text
        ]
        self.query_one(".status", Label).update("\n".join(lines))
        self.query_one(".status", Label).display = bool(lines)

    def show_comparison(
        self,
        reference: Sensor | None,
        reference_label: str,
        lap: Lap | None,
        now: float,
        window: int,
        comparable: int,
    ) -> None:
        """Difference to the reference meter, or how to pick one."""
        delta = self.query_one(".delta", Label)
        is_reference = reference is self.sensor
        self.set_class(is_reference, "reference")
        if self.sensor.kind not in AVERAGED_KINDS or (
            reference is None and comparable < 2
        ):
            delta.display = False
            return
        delta.display = True

        if reference is None:
            delta.update("[$text-muted]click to compare the others against this[/]")
        elif is_reference:
            delta.update("REFERENCE  [$text-muted]click to clear[/]")
        else:
            seconds = window or LIVE_DELTA_WINDOW
            diff = difference(
                self.sensor.average(seconds, now), reference.average(seconds, now)
            )
            now_text = f"{round(diff[0]):+d} W  {signed(diff[1])}%" if diff else "--"
            if lap and lap.seconds:
                lap_text = f"{signed(lap.percent)}%  ({lap.seconds // 60}:{lap.seconds % 60:02})"
            else:
                lap_text = "--"
            delta.update(
                f"Δ vs {reference_label}\n{seconds}s  {now_text}\nlap  {lap_text}"
            )

    def sample(self, now: float) -> None:
        """Add one point per second to the trend line."""
        fresh = self.sensor.is_fresh(now)
        self.history.append((self.sensor.value or 0) if fresh else 0)
        data = list(self.history)
        if self.sensor.kind in AVERAGED_KINDS:
            # The sparkline scales from the smallest value to the largest, so at
            # a steady 99-100 W it flips between empty and full; a leading 0
            # pins the scale to zero
            data.insert(0, 0.0)
        self.query_one(Sparkline).data = data


class RecorderApp(App):
    """The dashboard: a grid of tiles over the receiver's sensors.

    Owns the receiver's worker thread and ties together the registry (names
    and the reference meter), the CSV recorder and the raw log. Failures to
    use the stick end the app with `error` set, which `main` prints after the
    terminal is restored.
    """

    TITLE = "antplus-recorder"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("space", "lap", "Lap"),
        ("c", "reset_laps", "Reset Δ lap"),
        ("n", "note", "Note"),
    ] + [
        Binding(str(key), f"window({seconds})", show=False)
        for key, seconds in enumerate(POWER_WINDOWS, start=1)
    ]
    CSS = """
    /* Slim and quiet, so it isn't mistaken for the terminal's own scrollbar */
    Screen {
        scrollbar-size-vertical: 1;
        scrollbar-color: $panel-lighten-2;
        scrollbar-color-hover: $panel-lighten-3;
        scrollbar-background: $background;
    }
    #hint { padding: 1 2; color: $text-muted; }
    #hint.warning { color: $warning; }
    #windows { height: auto; padding: 1 2 0 2; }
    #windows Label { padding: 0 1 0 0; color: $text-muted; }
    #windows Button { margin-right: 1; min-width: 6; }
    #rec {
        width: 1fr;
        text-align: right;
        text-wrap: nowrap;
        text-overflow: ellipsis;
        color: $text-muted;
    }
    #rec.on { color: $error; text-style: bold; }
    #tiles {
        grid-size: 3;
        grid-rows: auto;
        grid-gutter: 1 2;
        height: auto;
        padding: 1 2 0 2;
        min-width: 40;  /* one tile plus padding; narrower windows scroll sideways */
    }
    SensorTile { height: auto; border: round $panel-lighten-2; padding: 0 1; }
    SensorTile.stale { opacity: 45%; }
    SensorTile.reference { border: round $accent; }
    SensorTile .delta { width: 1fr; }
    SensorTile .head { height: 1; }
    SensorTile .title { width: 1fr; text-style: bold; text-wrap: nowrap; text-overflow: ellipsis; }
    SensorTile .ident { padding-left: 1; }
    SensorTile .ident, SensorTile .unit, SensorTile .averages, SensorTile .details,
    SensorTile .status {
        color: $text-muted;
    }
    SensorTile .reading { height: auto; }
    SensorTile Digits { width: auto; }
    SensorTile .cadence { margin-left: 2; color: $text; }
    SensorTile .unit { padding: 1 0 0 1; }
    SensorTile .details, SensorTile .status { width: 1fr; }
    SensorTile Sparkline { height: 3; }
    SensorTile.power .value { color: $warning; }
    SensorTile.trainer .value { color: $accent; }
    SensorTile.heart_rate .value { color: $error; }
    """

    def __init__(
        self,
        receiver: Receiver,
        registry: Registry,
        recorder: Recorder | None,
        raw_log: RawLog | None = None,
    ) -> None:
        """`recorder` and `raw_log` are None when not recording."""
        super().__init__()
        self.receiver = receiver
        self.registry = registry
        self.recorder = recorder
        self.raw_log = raw_log
        self.tiles: dict[tuple[int, int], SensorTile] = {}
        self.seen: set[tuple[int, int]] = set()
        # Per tile key: work against the reference since the last reset
        self.laps: dict[tuple[int, int], Lap] = {}
        self.compared_to: tuple[str, int] | None = None
        self.last_tick = time.monotonic()
        # Devices found since the last toast, announced together once discovery settles
        self.found: list[str] = []
        self.last_found = 0.0
        self.status = "Starting..."
        self.listening_since: float | None = None
        # Set when the stick can't be used; main() prints it after the app exits
        self.error: str | None = None

    # Seconds averaged for the big power numbers, 0 = live
    window = reactive(0)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="windows"):
            yield Label("Power")
            for key, seconds in enumerate(POWER_WINDOWS, start=1):
                yield Button(
                    f"{seconds}s" if seconds else "Live",
                    id=f"window-{seconds}",
                    action=f"app.window({seconds})",
                    tooltip=f"Key {key}",
                    compact=True,
                )
            yield Label(id="rec")
        yield Label(id="hint")
        yield Grid(id="tiles")
        yield Footer()

    def on_mount(self) -> None:
        """Start the listening thread and the timers for the display and the recording."""
        self.run_worker(self._listen, thread=True, name="ant")
        self.set_interval(0.25, self.refresh_display)
        self.set_interval(1.0, self.tick)

    def _listen(self) -> None:
        """Open the stick and process its data until the app exits (worker thread).

        Each way opening can fail ends the app with a message saying what to
        do about it; the cases are told apart by the exception type, as openant
        and pyusb report them differently.
        """
        self.receiver.on_lost = lambda: self.call_from_thread(
            self._fail, "The ANT+ USB stick was disconnected. Recording stopped."
        )
        try:
            if not self.receiver.open(
                lambda text: self.call_from_thread(self._set_status, text)
            ):
                return
        except DriverNotFound:
            error = (
                "No ANT+ USB stick found. Plug one in (on a Mac, allow the accessory "
                "to connect) and try again."
            )
        except NoBackendError:
            error = (
                "No libusb found, so the ANT+ USB stick can't be reached. Install "
                "libusb-1.0 (e.g. apt install libusb-1.0-0) and try again."
            )
        except USBError as e:
            error = open_error(e)
        except StickUnsupported as e:
            error = str(e)
        except AntException:
            error = "The ANT+ USB stick isn't responding. Unplug it, plug it back in and try again."
        except Exception as e:  # noqa: BLE001 - openant raises a bare Exception when the stick refuses a command
            error = f"The ANT+ USB stick refused to start scan mode ({e})."
        else:
            self.call_from_thread(self._start_listening)
            self.receiver.run()
            return
        self.call_from_thread(self._fail, error)

    def _set_status(self, text: str) -> None:
        """Show the receiver's progress; called on the UI thread through `call_from_thread`."""
        self.status = text
        self.refresh_display()

    def _start_listening(self) -> None:
        self.listening_since = time.monotonic()

    def _fail(self, message: str) -> None:
        """End the app with exit code 1; `main` prints `message` after the terminal is restored."""
        self.error = message
        self.exit(return_code=1)

    def action_window(self, seconds: int) -> None:
        """Average the big power numbers over `seconds` (0 for live), from the buttons and keys."""
        self.window = seconds

    def watch_window(self, seconds: int) -> None:
        for button in self.query("#windows Button").results(Button):
            button.variant = (
                "primary" if button.id == f"window-{seconds}" else "default"
            )
        self.refresh_display()

    def on_sensor_tile_selected(self, message: SensorTile.Selected) -> None:
        """Make the clicked power tile the reference meter, or clear it if it already is."""
        sensor = message.sensor
        if self.registry.reference() == (sensor.kind, sensor.number):
            self.registry.set_reference(None)
        else:
            self.registry.set_reference(sensor)
        self.refresh_display()

    def toast(self, message: str, timeout: float) -> None:
        """Notify, then repaint once it's gone: the terminal can keep the parts of
        the toast that overlapped widgets updating underneath it."""
        self.notify(message, timeout=timeout)
        self.set_timer(timeout + 0.5, lambda: self.refresh(repaint=True, layout=True))

    def action_note(self) -> None:
        """Ask for a note and write it into the raw log at this moment of the ride.

        There is nothing to write to without recording, which is said instead.
        """
        if self.raw_log is None:
            self.toast("Not recording, there's no raw log for notes", 3)
            return

        def save(text: str | None) -> None:
            if text and self.raw_log:
                self.raw_log.note(text, time.monotonic())
                self.toast(f"Note saved: {text}", 3)

        self.push_screen(NoteScreen(), save)

    def action_lap(self) -> None:
        """Start a new lap: the next CSV rows carry the next lap number.

        The lap difference against the reference starts over too, since a lap
        is usually a new power level. Without recording there is no lap number
        to count, so only the difference is reset.
        """
        self.laps.clear()
        if self.recorder is None:
            self.toast("Reset Δ lap", 2)
            return
        number = self.recorder.new_lap()
        if self.raw_log:
            self.raw_log.lap(number, time.monotonic())
        self.toast(f"Lap {number}", 2)

    def action_reset_laps(self) -> None:
        """Start the lap difference against the reference over, e.g. after a zero offset."""
        self.laps.clear()
        self.toast("Reset Δ lap", 2)

    def _reference(self) -> Sensor | None:
        """The reference meter, or None if none is set or it hasn't been heard yet."""
        reference = self.registry.reference()
        if reference is None:
            return None
        for sensor in list(self.receiver.sensors.values()):
            if (sensor.kind, sensor.number) == reference:
                return sensor
        return None

    def on_unmount(self) -> None:
        # The worker thread only returns once the receiver stops, and asyncio
        # waits for it before the app can exit
        self.receiver.stop()

    def on_resize(self) -> None:
        self._fit_columns()

    def _fit_columns(self) -> None:
        """Set the tile grid's column count to what fits the window, at most one per tile.

        Each tile needs TILE_MIN_WIDTH, so the count is recomputed when the window
        is resized or a tile is added.
        """
        fit = (self.size.width - TILES_PADDING + TILE_GUTTER) // (
            TILE_MIN_WIDTH + TILE_GUTTER
        )
        columns = max(1, min(len(self.tiles), fit))
        grid = self.query_one("#tiles", Grid)
        if grid.styles.grid_size_columns != columns:
            grid.styles.grid_size_columns = columns

    def refresh_display(self) -> None:
        """Bring the screen up to date with the receiver; runs four times a second.

        Creates tiles for sensors heard for the first time and announces them in a
        notification once discovery settles, updates the existing tiles, keeps the
        active ones in front and sets the hint and title. Devices without a tile
        are skipped.
        """
        now = time.monotonic()
        sensors = list(self.receiver.sensors.items())

        # A new reference, picked here or in devices.toml, starts the laps over
        if self.registry.reference() != self.compared_to:
            self.compared_to = self.registry.reference()
            self.laps.clear()
        reference = self._reference()
        reference_label = (
            (self.registry.label(reference) or reference.kind) if reference else ""
        )
        comparable = sum(
            1 for t in self.tiles.values() if t.sensor.kind in AVERAGED_KINDS
        )

        for key, sensor in sensors:
            # Everything else, e.g. a runner's foot pod passing by, is only recorded
            if sensor.kind not in TILE_KINDS:
                continue
            if key not in self.seen:
                self.seen.add(key)
                label = self.registry.label(sensor)
                self.found.append(
                    f"{label} {sensor.kind}"
                    if label
                    else f"{sensor.kind} #{sensor.number}"
                )
                self.last_found = now
            if key not in self.tiles:
                # Shown from the next refresh on, once its widgets exist
                self._add_tile(key, sensor)
            else:
                tile = self.tiles[key]
                tile.show(self.registry.label(sensor), now, self.window)
                tile.show_comparison(
                    reference,
                    reference_label,
                    self.laps.get(key),
                    now,
                    self.window,
                    comparable,
                )

        self._sort_tiles(now)

        if self.found and now - self.last_found > 1.0:
            found = (
                ", ".join(self.found)
                if len(self.found) <= 2
                else f"{len(self.found)} devices"
            )
            self.toast(f"Found {found}", 3)
            self.found = []

        hint = self.query_one("#hint", Label)
        hint.display = not self.tiles
        if self.listening_since is None:
            hint.update(self.status)
        elif not sensors and now - self.listening_since > NOTHING_FOUND_AFTER:
            hint.add_class("warning")
            hint.update(
                "No ANT+ sensors found. Sensors sleep until they move: "
                "pedal a few turns, put on the heart rate strap."
            )
        else:
            hint.update(f"{self.status}, waiting for sensors...")
        self.sub_title = f"{self.status} · {len(sensors)} devices"

    def tick(self) -> None:
        """Once-a-second work: trend lines, device list, laps and the CSV row.

        Sensors enter `devices.toml` only once they have sent a reading. Without a
        recorder this only shows "not recording" and skips the row.
        """
        now = time.monotonic()
        for tile in self.tiles.values():
            if tile.is_mounted:
                tile.sample(now)
        self.registry.reload()
        sensors = list(self.receiver.sensors.values())
        for sensor in sensors:
            # Passers-by that never sent a reading don't belong in devices.toml
            if sensor.has_data:
                self.registry.record(sensor)
        self._add_to_laps(now)
        self.last_tick = now

        rec = self.query_one("#rec", Label)
        if self.recorder is None:
            rec.update("not recording")
            return
        self.recorder.write([(s, self.registry.label(s)) for s in sensors], now)
        elapsed = int(self.recorder.elapsed(now))
        rec.add_class("on")
        rec.update(
            f"● REC {elapsed // 3600:02}:{elapsed % 3600 // 60:02}:{elapsed % 60:02}"
            f"  {self.recorder.path.name}"
        )

    def _add_to_laps(self, now: float) -> None:
        """Count the last second towards each meter's lap against the reference."""
        reference = self._reference()
        if reference is None:
            return
        reference_power = reference.value_since(self.last_tick, now)
        if reference_power is None or reference_power < MIN_REFERENCE_POWER:
            return
        for key, tile in self.tiles.items():
            if tile.sensor is reference or tile.sensor.kind not in AVERAGED_KINDS:
                continue
            power = tile.sensor.value_since(self.last_tick, now)
            if power is not None:
                self.laps.setdefault(key, Lap()).add(power, reference_power)

    def _add_tile(self, key: tuple[int, int], sensor: Sensor) -> None:
        """Mount a tile for a newly heard sensor; the next refresh moves it into place."""
        tile = self.tiles[key] = SensorTile(sensor)
        # Appended; the next refresh puts it in its place
        self.query_one("#tiles", Grid).mount(tile)
        self._fit_columns()

    def _sort_tiles(self, now: float) -> None:
        """Reorder the tiles by `SensorTile.order`, moving only those out of place.

        Called every refresh, so it moves only the tiles that are out of place and
        leaves an already sorted grid alone.
        """
        grid = self.query_one("#tiles", Grid)
        shown = list(grid.query_children(SensorTile))
        wanted = sorted(shown, key=lambda t: t.order(now))
        for index, tile in enumerate(wanted):
            if shown[index] is not tile:
                grid.move_child(tile, before=shown[index])
                shown.remove(tile)
                shown.insert(index, tile)
