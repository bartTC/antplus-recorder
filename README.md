# antplus-recorder

Shows and records every ANT+ sensor in range: power meters, smart trainers and heart rate straps, side by side in the terminal. Built to compare power meters against each other, for example a smart trainer against crank or pedal power meters. One power meter is the reference, and every other one shows how far it is off, in watts and percent. Every ride is written to a CSV file and to one FIT file per power meter.

There is no pairing. The ANT+ USB stick listens to the whole ANT+ frequency, and every sensor that sends shows up on its own.

![antplus-recorder with a Wahoo Kickr as reference, a Stages crank 0.9% below it, the Kickr's trainer channel and a heart rate strap](https://raw.githubusercontent.com/bartTC/antplus-recorder/main/docs/images/dashboard.png)

*The Kickr in ERG mode at 100 W is the reference. Its power and trainer channels agree, the Stages crank reads 1.1% lower over the lap.*

## Install

Python ≥ 3.11 and an ANT+ USB stick (ANT USB2 or ANT USB-m, the usual Garmin/Dynastream ones).

```sh
uvx antplus-recorder            # run without installing
uv tool install antplus-recorder
pipx install antplus-recorder
```

libusb comes along with the `libusb-package` dependency, so a Mac needs nothing from Homebrew. If libusb is installed on the system, that one is used.

- **macOS:** Plug the stick in and allow the accessory to connect when macOS asks.
- **Linux:** The stick needs read/write access for your user. Untested, but this udev rule in `/etc/udev/rules.d/42-ant-usb-sticks.rules` should do it, followed by `sudo udevadm control --reload-rules` and replugging the stick:

  ```
  SUBSYSTEM=="usb", ATTRS{idVendor}=="0fcf", ATTRS{idProduct}=="1008", MODE="0666"
  SUBSYSTEM=="usb", ATTRS{idVendor}=="0fcf", ATTRS{idProduct}=="1009", MODE="0666"
  ```

## Usage

```sh
antplus-recorder                       # show and record everything in range
antplus-recorder --no-record           # only show
antplus-recorder -o ~/Rides            # record somewhere else
antplus-recorder -d 57587 -d 19696     # only these device numbers
antplus-recorder --fit-from 2026-10-09_18-57-53.csv
```

Recording starts right away, so it can't be forgotten. Files go to the folder you start the command in, named after the start time, and are never overwritten.

| Option | Default | Effect |
|---|---|---|
| `-o`, `--output-dir DIR` | current folder | Where recordings go. |
| `--no-record` | off | Only show, write no files. |
| `-d`, `--device NUMBER` | all | Only show and record this ANT+ device number. Repeat for more. Useful when other people's sensors are in range. |
| `--devices FILE` | `devices.toml` | The device list, see [Naming devices](#naming-devices). |
| `--fit-from CSV` | | Build the FIT files for a recorded CSV, e.g. after a crash, then exit. |

### Keys

| Key | Effect |
|---|---|
| `1`–`5` or the buttons | Big power numbers, L/R balance and the difference show Live, 3 s, 10 s, 30 s or 60 s averages. |
| click a power tile | Make it the reference for comparing, click again to clear. |
| `c` | Start a new lap for the comparison. |
| `q` | Quit. Writes the FIT files. |

### What a tile shows

- Power and cadence. Power is the average over the selected window, cadence is live.
- 3 s, 10 s and 30 s averages, always.
- The difference to the reference meter, see below.
- L/R balance for dual-sided meters, power-weighted over the selected window, battery and how often a new power value arrives (Hz).
- Smart trainers (FE-C): state (ready, in use, paused), resistance level, incline if sent, and warnings such as `spindown needed` or `ERG: speed too low`.
- A trend line of the last two minutes, starting at 0 W.

Heart rate straps get a tile with bpm. Every other ANT+ device in range (watches, footpods, radars …) is listed in a table below.

## Comparing power meters

Click the power meter you trust, for example the pedals or the crank. It gets an orange border and `REFERENCE`. Every other power tile then shows:

```
Δ vs Stages Cycling
30s  -7 W  -6.5%
lap  -6.8%  (4:12)
```

- The middle line compares the averages over the selected window. In Live mode it uses 30 s, because two meters never measure at the same instant and single readings make the difference jump.
- **lap** compares the work of both meters since the last `c`. Seconds in which the reference is below 20 W (coasting) or one of the meters sends nothing don't count, and the time in brackets is only the counted seconds. Because it sums energy, a minute at 300 W weighs three times as much as a minute at 100 W. Press `c` at the start of every power level.
- Changing the reference starts a new lap. The reference is saved in `devices.toml`.

Use the lap, not the 30 s value, for corrections: over a few seconds, the reference's own noise shows up as a difference.

**Example:** With the crank as reference, a smart trainer shows `-6.5%`, so it reads 6.5% low. To really ride 200 W in ERG mode, set it to 200 × (1 − 0.065) ≈ 187 W.

For a clean number, warm up for 10 minutes, then do the trainer's spindown and the zero offset of the other meters, and ride a few minutes at each of several levels (100, 200, 300 W). That also shows whether the difference is a constant percentage or a fixed number of watts: 7 W are 7% at 100 W but only 2–3% at 300 W.

## Files

### CSV

`2026-10-09_18-57-53.csv`, one row per sensor and second, appended and flushed every second, so a crash loses at most the last second.

| Column | Content |
|---|---|
| `time` | Local time with UTC offset, ISO 8601. |
| `kind` | `power`, `trainer` or `heart_rate`. |
| `number` | ANT+ device number. |
| `name` | Name from `devices.toml`, else the manufacturer. |
| `value` | Watts or bpm, the mean of that second. |
| `unit` | `W` or `bpm`. |
| `cadence` | rpm. |
| `right_balance` | Right leg's share in %, dual-sided meters only. |
| `speed` | km/h, smart trainers only. |

A device that joins mid-ride (pedals woken up later) simply starts appearing in the rows. With pandas: `df.pivot_table(index="time", columns=["kind", "number"], values="value")`.

### FIT

When you quit, there is one FIT file per power source, e.g. `2026-10-09_18-57-53_power-57587_stages-cycling.fit`, with power, cadence, L/R balance and the heart rate of the first strap, plus speed and distance for a smart trainer. Each file is a complete cycling activity (indoor cycling) with lap and session summary, so it can be uploaded anywhere and compared with tools like the [DC Rainmaker Analyzer](https://analyzer.dcrainmaker.com), which expect one file per power meter. A FIT file is only valid when complete, so it is built from the CSV at the end. After a crash, `--fit-from` builds it.

### Naming devices

`devices.toml` in the current folder lists every device ever seen, filled in automatically:

```toml
[power.57587]
name = ""
manufacturer = "Stages Cycling"
model = 3
serial = 40618248
reference = true
```

Set `name` and the tile shows it right away, no restart needed. Names also go into the CSV and the FIT file names. A Kickr shows up twice, as `power` and as `trainer`: smart trainers send both profiles, so plain power meter displays can read them too.

## How it works

- **Scan mode:** The stick runs in ANT continuous scan mode. It listens 100% of the time on 2457 MHz and receives every sensor, with no pairing and no limit of 8 channels. The same sensor can still be paired with a bike computer or Zwift at the same time, because ANT+ sensors broadcast. The flip side: scan mode only receives, so the app can't control a trainer. Use the trainer's own app for ERG.
- **Power between pages:** Torque-based meters send the page with the power value only every few messages. The app computes the average from the accumulated power and the event counter, so the power of every pedal stroke counts, not just the latest one.
- **Coasting:** A meter keeps repeating its last value while the cranks stand still. If the event counter hasn't moved for 3 s, the app shows 0 W, like head units do.
- **Recording:** If a second gets no new power value, the last one is held while the sensor is still sending, again like head units with 1 s recording.

## Troubleshooting

- **No ANT+ USB stick found:** Check that the system sees it (macOS: allow the accessory; Linux: udev rule above).
- **Stick is used by another app:** Only one program can use the stick. Close Zwift, Garmin Express or another `antplus-recorder`.
- **Stick didn't answer, resetting it:** After a crash or kill the stick can hang once. The app resets it on its own, which takes about 3 s.
- **No sensors found:** Sensors sleep until they move. Pedal a few turns, put on the heart rate strap.
- **Two scrollbars at the right edge in iTerm2:** iTerm2 extends the last column into the window margin. Settings → Advanced → "Extend background color into margins in alternate screen mode?" → No ([textual#6230](https://github.com/Textualize/textual/issues/6230)).

## Limitations

- Receives only, no trainer control (see scan mode above).
- Tested on macOS with an ANT USB2 stick, a Wahoo Kickr, a Stages crank and a Garmin heart rate strap. Linux is untested.
- A Kickr sends the power page on its trainer channel only rarely, sometimes more than 10 s apart, so its `trainer` tile reacts slowly. Its `power` tile updates about once per second and is the better one to compare.
- Many sensors on one frequency collide more often. Untested beyond a handful of real sensors; the display itself was tested with 40 simulated ones.

## Development

```sh
just run                  # run the dashboard from the repo
just test                 # pytest
just lint                 # ruff with all rules (config in pyproject.toml)
just build                # sdist and wheel into dist/
```

Releases go out through GitHub Actions: `git tag v0.2.0 && git push --tags` runs ruff and the tests and publishes to PyPI with Trusted Publishing. The tag has to match the version in `pyproject.toml`.

## License

MIT
