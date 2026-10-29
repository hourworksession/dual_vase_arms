# Klipper test config: two independent extruders

| File | What it is |
| --- | --- |
| `stepX_left_control.cfg` | The config that works today (cartesian, left nozzle on X, bed heater). The panel needs this one. |
| `non_extruder_control_test.cfg` | The test config: same pins, sensors, PID and fans, but two independent extruders driven by macros |
| `extrude_control.cfg` | The EXTRUDE_* macros the test config includes. Needs Klipper v0.13.0-106 or newer: both steppers become G-code axes (right = A, left = B) and every tick sends one G1 that turns both together, so they run continuously at the same time. |

The test config replaces the Pi's `printer.cfg` **for a bench test only**. While it is loaded,
the cell studio panel's extrusion does not work. Put `stepX_left_control.cfg` back afterwards (step 7).

## 1. Check the backup matches the Pi

In Mainsail: Machine > `printer.cfg`. It should match `stepX_left_control.cfg` (pins, serial,
PID). If it differs, download the Pi's version and commit it over `stepX_left_control.cfg` first.

## 2. Load the test config

In Mainsail: upload `extrude_control.cfg`; open `printer.cfg`, replace its contents with
`non_extruder_control_test.cfg`, Save & Restart (or run `FIRMWARE_RESTART`).
Klipper should show **Ready**. If it reports a config error, read the line it names and send it to Claude.

Check both extruders run continuously together (filament loaded, hot ends above 190 C):

    EXTRUDE_ON T=0 RATE=5
    EXTRUDE_ON T=1 RATE=5
    EXTRUDE_STATUS          ; both on=1, ticking=1
    EXTRUDE_OFF

Both motors should hum steadily, not tick. `MANUAL_STEPPER ... MOVE=` no longer works while the
macros own the steppers (Klipper says "Must unregister from gcode axis first"): use `EXTRUDE_MM`.
Mainsail's X position climbs while extruding; X drives nothing and can be ignored.

## Test panel (instead of typing the console commands)

    python3 scripts/extruder_test_panel.py            # Pi address from config/settings.yaml
    python3 scripts/extruder_test_panel.py --sim      # practise without hardware

Per tool: heat, ON/OFF at a rate, flow slider, push a length. ALL OFF (button, Space or Esc)
skips any queued commands. Untick Keepalive to check the 5 s watchdog stops a tool by itself.
ON is blocked below the minimum extrusion temperature (190 C by default). The log shows each
command's round-trip time. On start it says whether the test config is the one loaded.

## 3. Heaters (console)

| Command | Expect |
| --- | --- |
| `EXTRUDE_STATUS` | both tools listed, both off |
| `EXTRUDE_TEMP T=0 TARGET=225 WAIT=1` | right hot end reaches 225 C and the command returns |
| `EXTRUDE_TEMP T=1 TARGET=225 WAIT=1` | left the same |
| `PID_CALIBRATE HEATER=right_hotend TARGET=225` then `SAVE_CONFIG` | optional: the current PID values are carried over; recalibrate only if it overshoots |

Both hot ends should read room temperature before heating, as they do now (PT1000 sensors).

## 4. 100 mm push test (per tool, hot)

Mark the filament 120 mm above the extruder inlet, then:

    EXTRUDE_MM T=0 MM=100 RATE=3

Measure what remains to the mark: 20 mm means `rotation_distance` is right. Otherwise
new value = 8.06 x (actual mm / 100). Repeat with `T=1`. If a motor turns the wrong way,
flip the `!` on that tool's `dir_pin`.

## 5. On/off control

| Command | Expect |
| --- | --- |
| `EXTRUDE_ON T=0 RATE=2` | right extrudes steadily |
| `EXTRUDE_SCALE T=0 PERCENT=50` | flow halves |
| `EXTRUDE_OFF T=0` | stops within about 0.1 s |
| `EXTRUDE_ON T=0 RATE=2 TIMEOUT=3`, then wait | stops by itself after 3 s (watchdog) |
| `EXTRUDE_ON T=0 RATE=2` and `EXTRUDE_ON T=1 RATE=2` together | both run at once |
| `EXTRUDE_OFF` | both stop |

## 6. Record results

Note the measured `rotation_distance` for each tool, the saved PID values, and anything that
failed, in this folder or the lab notes.

## 7. Restore the panel's config

Replace the Pi's `printer.cfg` contents with `stepX_left_control.cfg`, Save & Restart. The panel then works as before.
`extrude_control.cfg` can stay on the Pi; nothing includes it.
