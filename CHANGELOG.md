# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-10-12

### Added

- `space` starts a new lap. The CSV has a `lap` column with the lap number, so the first row with a new number is the one after the key press. The raw log records the press too, so a replay gives the same laps. It also restarts the difference to the reference, like `c`, which still does that alone.
- On macOS the Mac no longer goes to sleep while the app runs, which stopped the recording. No need to run `caffeinate` alongside any more.

### Changed

- Tiles of sensors that are sending come first, in the order heart rate, power, trainer. A passing heart rate strap or trainer no longer pushes your own power meter down, and one that goes quiet sinks to the end.
- The table of other devices is gone. Passers-by (foot pods, bike lights …) only cluttered the screen; they are still counted in the title bar and kept in the raw log. The "Found" notice now only announces devices that get a tile.
- `devices.toml` only lists devices that sent a reading, so passers-by no longer pile up in it.

### Fixed

- A Kickr always said `spindown needed`, even after a spindown: it sends that flag on every message. The warning is no longer shown.
- Parts of a notification could stay on screen after it expired, until the next scroll. The screen is now repainted when a notification goes away.

## [0.4.0] - 2026-10-10

### Added

- A stick that is unplugged mid-ride ends the session with a message instead of stale tiles and a spinning core; the CSV, the raw log and the FIT files are written as on `q`.
- Clear messages for a stick that can't do continuous scan mode (ANT USB1 and other nRF24AP1 sticks), for missing libusb on platforms without a bundled copy (e.g. 32-bit Raspberry Pi OS), for missing permissions on Linux (pointing to the udev rule) and for a stick that refuses a command. The last two used to end in a traceback.

### Fixed

- The dashboard could crash with "deque mutated during iteration" when a sensor's page arrived while its average was being computed.
- Quitting after the stick was unplugged, and on Linux possibly every quit, raised a USB error while giving the stick back to the system.
- CSV, reception CSV and `devices.toml` are always UTF-8, so a device name with non-ASCII characters no longer depends on the system locale (Windows: cp1252).

## [0.3.0] - 2026-10-09

### Added

- `--replay` reports reception per channel: the profile's period, the usual interval, received messages, loss in percent, breaks and loss per minute, and writes `<ride>_replay_reception.csv` with loss per channel and minute. Loss is counted from the intervals between messages, exact to the stick's timestamp; pauses over 2 s count as breaks, not loss.

### Fixed

- The signal block in the raw log was described as a signal strength. On ANT USB2 and USB-m sticks (nRF24AP2) it is an AGC reading (measurement type `0x10`, 4 bytes), not RSSI in dBm, and says nothing about reception.

## [0.2.0] - 2026-10-09

### Added

- Raw log: every ride also writes `<ride>.ant.gz` with every ANT+ message exactly as the stick delivered it, with a monotonic and a unix timestamp per line, the app version, the command line and the stick's serial number, firmware and USB ID. Flushed every second, readable after a crash.
- The stick is asked for signal strength and its own receive timestamp (1/32768 s) with each message. Sticks without them fall back to fewer extras.
- Stick events other than data, such as a serial queue overflow, go into the raw log.
- Notes: `--note TEXT` at the start and the `n` key during the ride write a note into the raw log, e.g. bike, spindown or zero offset.
- `--replay LOG` recomputes CSV and FIT files from a raw log with the current version and prints the notes and, per device, the number of messages, the usual interval and the longest gap.

### Changed

- Messages that repeat the previous one byte for byte are no longer dropped, because the receive timestamp makes each one unique. Averages can differ slightly from 0.1.0.

## [0.1.0] - 2026-10-09

First release.

### Added

- Receives every ANT+ sensor in range without pairing, using the stick's continuous scan mode.
- Terminal dashboard: a tile per power meter, smart trainer and heart rate strap with power, cadence, 3/10/30 s averages, L/R balance, battery, update rate and a trend line; other devices in a table.
- Power averages over Live, 3, 10, 30 or 60 s with buttons or the keys 1–5. L/R balance follows the selected window, power-weighted.
- Comparing power meters: click a tile to make it the reference; the others show the difference in watts and percent and a lap over the work since the last `c`.
- Smart trainers (FE-C): state, resistance, incline, calibration warnings such as `spindown needed`, and ERG feedback.
- Recording: a CSV row per sensor and second, and one FIT file per power source when quitting. Files are named after the start time and never overwritten. `--fit-from` builds the FIT files from a CSV after a crash.
- `devices.toml` lists every device seen with manufacturer, model and serial; names set there show up right away. The reference meter is saved there too.
- `--device` limits display and recording to given device numbers.
- Power from the accumulated power and event counter, so every pedal stroke counts; 0 W after 3 s without a new event; the last value held for seconds without a new page.
- libusb comes bundled through `libusb-package`, so no Homebrew is needed on a Mac.
- A stick that hangs after a crash is detected within half a second and reset.

[Unreleased]: https://github.com/bartTC/antplus-recorder/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/bartTC/antplus-recorder/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/bartTC/antplus-recorder/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/bartTC/antplus-recorder/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/bartTC/antplus-recorder/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/bartTC/antplus-recorder/releases/tag/v0.1.0
