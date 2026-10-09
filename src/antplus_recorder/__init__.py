"""Record and display every ANT+ sensor in range."""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from textual.logging import TextualHandler

from .app import RecorderApp
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
    args = parser.parse_args()

    if args.fit_from:
        write_fits(args.fit_from)
        return

    # openant logs USB hiccups as warnings, which would garble the terminal UI
    logging.basicConfig(level=logging.WARNING, handlers=[TextualHandler()])

    receiver = Receiver(set(args.device) if args.device else None)
    recorder = None
    if not args.no_record:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        path = args.output_dir / f"{datetime.now().astimezone():%Y-%m-%d_%H-%M-%S}.csv"
        recorder = Recorder(path, time.monotonic())

    app = RecorderApp(receiver, Registry(args.devices), recorder)
    try:
        app.run()
    finally:
        receiver.stop()
        if recorder:
            recorder.close()

    if recorder:
        if recorder.rows:
            print(f"Recorded {recorder.path}")
            write_fits(recorder.path)
        else:
            recorder.path.unlink()
            if not app.error:
                print("Nothing recorded, no files written.")
    if app.error:
        sys.exit(app.error)


def write_fits(csv_path: Path) -> None:
    written, skipped = write_fit_files(csv_path)
    for path in written:
        print(f"Wrote {path}")
    for path in skipped:
        print(f"Skipped {path}, it already exists")
    if not written and not skipped:
        print(f"No power data in {csv_path}, no FIT files written.")
