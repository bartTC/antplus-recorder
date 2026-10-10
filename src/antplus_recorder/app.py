"""Terminal dashboard showing every ANT+ sensor in range."""

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
    DataTable,
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
TILE_KINDS = ("power", "trainer", "heart_rate")  # big tiles, in this order
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

    @property
    def order(self) -> tuple[int, int]:
        return TILE_KINDS.index(self.sensor.kind), self.sensor.number

    def on_click(self) -> None:
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
    TITLE = "antplus-recorder"
    BINDINGS = [
        ("q", "quit", "Quit"),
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
    #others { height: auto; max-height: 8; margin: 1 2; }
    """

    def __init__(
        self,
        receiver: Receiver,
        registry: Registry,
        recorder: Recorder | None,
        raw_log: RawLog | None = None,
    ) -> None:
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
        others = DataTable(id="others", cursor_type="none", zebra_stripes=True)
        # Focus would scroll the screen down to the table on startup; it has
        # no cursor, and the mouse wheel scrolls it without focus
        others.can_focus = False
        yield others
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#others", DataTable)
        for column in ("kind", "number", "manufacturer", "name", "last seen"):
            table.add_column(column, key=column)
        table.display = False

        self.run_worker(self._listen, thread=True, name="ant")
        self.set_interval(0.25, self.refresh_display)
        self.set_interval(1.0, self.tick)

    def _listen(self) -> None:
        """Open the stick and process its data until the app exits (worker thread)."""
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
        self.status = text
        self.refresh_display()

    def _start_listening(self) -> None:
        self.listening_since = time.monotonic()

    def _fail(self, message: str) -> None:
        self.error = message
        self.exit(return_code=1)

    def action_window(self, seconds: int) -> None:
        self.window = seconds

    def watch_window(self, seconds: int) -> None:
        for button in self.query("#windows Button").results(Button):
            button.variant = (
                "primary" if button.id == f"window-{seconds}" else "default"
            )
        self.refresh_display()

    def on_sensor_tile_selected(self, message: SensorTile.Selected) -> None:
        sensor = message.sensor
        if self.registry.reference() == (sensor.kind, sensor.number):
            self.registry.set_reference(None)
        else:
            self.registry.set_reference(sensor)
        self.refresh_display()

    def action_note(self) -> None:
        if self.raw_log is None:
            self.notify("Not recording, there's no raw log for notes", timeout=3)
            return

        def save(text: str | None) -> None:
            if text and self.raw_log:
                self.raw_log.note(text, time.monotonic())
                self.notify(f"Note saved: {text}", timeout=3)

        self.push_screen(NoteScreen(), save)

    def action_reset_laps(self) -> None:
        self.laps.clear()
        self.notify("Δ lap reset", timeout=2)

    def _reference(self) -> Sensor | None:
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
        fit = (self.size.width - TILES_PADDING + TILE_GUTTER) // (
            TILE_MIN_WIDTH + TILE_GUTTER
        )
        columns = max(1, min(len(self.tiles), fit))
        grid = self.query_one("#tiles", Grid)
        if grid.styles.grid_size_columns != columns:
            grid.styles.grid_size_columns = columns

    def refresh_display(self) -> None:
        now = time.monotonic()
        table = self.query_one("#others", DataTable)
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
            if key not in self.seen:
                self.seen.add(key)
                label = self.registry.label(sensor)
                self.found.append(
                    f"{label} {sensor.kind}"
                    if label
                    else f"{sensor.kind} #{sensor.number}"
                )
                self.last_found = now
            if sensor.kind in TILE_KINDS:
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
            else:
                row = f"{key[0]}:{key[1]}"
                cells = {
                    "kind": sensor.kind,
                    "number": str(sensor.number),
                    "manufacturer": self.registry.manufacturer(sensor) or "",
                    "name": self.registry.name(sensor) or "",
                    "last seen": f"{now - sensor.last_seen:.0f}s ago",
                }
                if row not in table.rows:
                    table.add_row(*cells.values(), key=row)
                    table.display = True
                else:
                    for column, value in cells.items():
                        table.update_cell(row, column, value)

        if self.found and now - self.last_found > 1.0:
            found = (
                ", ".join(self.found)
                if len(self.found) <= 2
                else f"{len(self.found)} devices"
            )
            self.notify(f"Found {found}", timeout=3)
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
        now = time.monotonic()
        for tile in self.tiles.values():
            if tile.is_mounted:
                tile.sample(now)
        self.registry.reload()
        sensors = list(self.receiver.sensors.values())
        for sensor in sensors:
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
        tile = self.tiles[key] = SensorTile(sensor)
        grid = self.query_one("#tiles", Grid)
        later = [t for t in grid.query_children(SensorTile) if t.order > tile.order]
        grid.mount(tile, before=min(later, key=lambda t: t.order) if later else None)
        self._fit_columns()
