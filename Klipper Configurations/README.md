# Klipper test config: two independent extruders

`printer.cfg` + `extrude_control.cfg` replace the Pi's current Klipper config **for a bench
test only**. While they are loaded, the cell studio panel's extrusion does not work.
Restore the old config afterwards (step 8).

## 1. Back up the current config (do not skip)

In Mainsail/Fluidd: Machine > config files > `printer.cfg` > Download. Also save a copy here
as `printer_current_backup.cfg` and commit it, so the repo has the config that works today.

## 2. Check the three items at the top of `printer.cfg`

- `[mcu] serial`: copy the line from the backup.
- `dir_pin`: if the backup has a different `!` on gpio10 or gpio13, use the backup's.
- `sensor_type`: if the backup uses a different thermistor type and reads room temperature
  correctly, use the backup's.

## 3. Load it

Upload both files (replace `printer.cfg`, add `extrude_control.cfg`), then run `FIRMWARE_RESTART`.
Klipper should show **Ready**. If it reports a config error, read the line it names and send it to Claude.

## 4. Heaters (console)

| Command | Expect |
| --- | --- |
| `EXTRUDE_STATUS` | both tools listed, both off |
| `EXTRUDE_TEMP T=0 TARGET=225 WAIT=1` | right hot end reaches 225 C and the command returns |
| `EXTRUDE_TEMP T=1 TARGET=225 WAIT=1` | left the same |
| `PID_CALIBRATE HEATER=right_hotend TARGET=225` then `SAVE_CONFIG` | new PID values saved (repeat for left_hotend) |

Both hot ends should read room temperature before heating. A reading of 0 or about -14 C
means the wrong pin or a disconnected thermistor: stop and check.

## 5. 100 mm push test (per tool, hot)

Mark the filament 120 mm above the extruder inlet, then:

    EXTRUDE_MM T=0 MM=100 RATE=3

Measure what remains to the mark: 20 mm means `rotation_distance` is right. Otherwise
new value = 7.82 x (actual mm / 100). Repeat with `T=1`. If a motor turns the wrong way,
flip the `!` on that tool's `dir_pin`.

## 6. On/off control

| Command | Expect |
| --- | --- |
| `EXTRUDE_ON T=0 RATE=2` | right extrudes steadily |
| `EXTRUDE_SCALE T=0 PERCENT=50` | flow halves |
| `EXTRUDE_OFF T=0` | stops within about 0.1 s |
| `EXTRUDE_ON T=0 RATE=2 TIMEOUT=3`, then wait | stops by itself after 3 s (watchdog) |
| `EXTRUDE_ON T=0 RATE=2` and `EXTRUDE_ON T=1 RATE=2` together | both run at once |
| `EXTRUDE_OFF` | both stop |

## 7. Record results

Note the measured `rotation_distance` for each tool, the saved PID values, and anything that
failed, in this folder or the lab notes.

## 8. Restore the panel's config

Upload `printer_current_backup.cfg` as `printer.cfg`, then run `FIRMWARE_RESTART`. The panel then works as before.
