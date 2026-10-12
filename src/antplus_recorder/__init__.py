"""Record and display every ANT+ sensor in range."""

import argparse
import logging
import sys
import time
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from textual.logging import TextualHandler

from . import rawlog, reception
from .app import RecorderApp
from .awake import stay_awake
from .rawlog import RawLog
from .receiver import Receiver
from .recording import Recorder, write_fit_files
from .registry import Registry


def main() -> None:
    parser = argparse.ArgumentParser(prog="antplus-recorder", description=__doc__)
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path(),
        help="where recordings go, named by start time (default: current directory)",
    )
    parser.add_argument(
        "--no-record", action="store_true", help="only display, don't write any files"
    )
    parser.add_argument(
        "-d",
        "--device",
        type=int,
        action="append",
        metavar="NUMBER",
        help="only show and record this ANT+ device number; repeat for more (default: all)",
    )
    parser.add_argument(
        "--devices",
        type=Path,
        default=Path("devices.toml"),
        help="file listing seen devices and their names (default: %(default)s)",
    )
    parser.add_argument(
        "--fit-from",
        type=Path,
        metavar="CSV",
        help="build the FIT files for a recorded CSV, e.g. after a crash, then exit",
    )
    parser.add_argument(
        "--note",
        action="append",
        default=[],
        metavar="TEXT",
        help="note for the raw log, e.g. bike, spindown, zero offset; repeat for more",
    )
    parser.add_argument(
        "--replay",
        type=Path,
        metavar="LOG",
        help="recompute CSV and FIT files from a ride's raw log (.ant.gz) and report reception, then exit",
    )
    args = parser.parse_args()

    if args.fit_from:
        write_fits(args.fit_from)
        return
    if args.replay:
        replay(args.replay, Registry(args.devices))
        return

    # openant logs USB hiccups as warnings, which would garble the terminal UI
    logging.basicConfig(level=logging.WARNING, handlers=[TextualHandler()])

    receiver = Receiver(set(args.device) if args.device else None)
    recorder = raw_log = None
    if not args.no_record:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        now, started = (
            time.monotonic(),
            datetime.now().astimezone().replace(microsecond=0),
        )
        name = f"{started:%Y-%m-%d_%H-%M-%S}"
        recorder = Recorder(args.output_dir / f"{name}.csv", now, started)
        raw_log = RawLog(args.output_dir / f"{name}{rawlog.SUFFIX}", now, started)
        raw_log.meta("app", f"antplus-recorder {version('antplus-recorder')}", now)
        raw_log.meta("command", " ".join(sys.argv[1:]), now)
        for note in args.note:
            raw_log.meta("note", note, now)
        receiver.log = raw_log

    app = RecorderApp(receiver, Registry(args.devices), recorder, raw_log)
    try:
        with stay_awake():
            app.run()
    finally:
        receiver.stop()
        if recorder and raw_log:
            recorder.close()
            raw_log.close()

    if recorder and raw_log:
        if recorder.rows:
            print(f"Recorded {recorder.path} and {raw_log.path}")
            write_fits(recorder.path)
        else:
            recorder.path.unlink()
            raw_log.path.unlink()
            if not app.error:
                print("Nothing recorded, no files written.")
    if app.error:
        sys.exit(app.error)


def replay(log: Path, registry: Registry) -> None:
    try:
        result = rawlog.replay(log, registry.label)
    except FileExistsError as e:
        sys.exit(f"{e.filename} already exists, remove it to replay again.")
    except ValueError as e:
        sys.exit(str(e))

    for key in ("started", "app", "stick", "command"):
        if key in result.meta:
            print(f"{key + ':':<9}{result.meta[key]}")
    for unix_time, text in result.notes:
        print(
            f"note:    {datetime.fromtimestamp(unix_time).astimezone():%H:%M:%S}  {text}"
        )
    channels = reception.analyse(result.channels)
    print_reception(channels)
    for seconds, text in result.events:
        print(f"stick event at {seconds:.1f}s: {text}")
    print()
    print(f"Wrote {result.recorder.path}")
    stem = result.recorder.path.name.removesuffix(".csv")
    reception_path = result.recorder.path.with_name(f"{stem}_reception.csv")
    started = datetime.fromisoformat(result.meta["started"])
    reception.write_csv(reception_path, channels, started)
    print(f"Wrote {reception_path}")
    write_fits(result.recorder.path)


def print_reception(channels: list[reception.Channel]) -> None:
    """Loss per channel, and per minute as a line of bars to spot collision waves."""
    top = max((m.loss or 0 for c in channels for m in c.minutes.values()), default=0)
    last = max((max(c.minutes) for c in channels if c.minutes), default=0)
    print(
        f"\n{'channel':<22}{'period':>9}{'usual':>8}{'messages':>10}{'loss':>7}{'breaks':>10}"
        f"   loss per minute (█ = {top:.1f}%)"
    )
    for c in channels:
        loss = "" if c.loss is None else f"{c.loss:.1f}%"
        period = f"{'~' if c.estimated else ''}{c.period * 1000:.0f}ms"
        usual = f"{c.usual * 1000:.0f}ms"
        breaks = f"{c.breaks} ({c.break_seconds:.0f}s)" if c.breaks else "0"
        minutes = [
            c.minutes[i].loss if i in c.minutes else None for i in range(last + 1)
        ]
        print(
            f"{c.kind + ' #' + str(c.number):<22}{period:>9}{usual:>8}{c.received:>10}{loss:>7}"
            f"{breaks:>10}   {reception.sparkline(minutes, top)}"
        )


def write_fits(csv_path: Path) -> None:
    written, skipped = write_fit_files(csv_path)
    for path in written:
        print(f"Wrote {path}")
    for path in skipped:
        print(f"Skipped {path}, it already exists")
    if not written and not skipped:
        print(f"No power data in {csv_path}, no FIT files written.")
