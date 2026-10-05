# Web panels

Browser instruments for the dual arm cell. Each file is a single self contained HTML page: no build step, no dependencies beyond Google Fonts.

## Run

From the repo root:

```
python -m http.server 8000
```

then open http://localhost:8000/web/ and pick a panel. Opening a file directly (double click, `file://`) also works for everything except the Cell Panel's live WebSocket link, which needs the page served over http.

## Panels

| File | Purpose |
|---|---|
| `stage_monitor.html` | Panel by panel view of the four toolpath stages for one part: unmolested helix points, rotary resolution with stop and reverse physics, single extruder with FullControl test and refine, and the dual cooperative output line. Exports datasets A to D as CSV (in a plain browser the Save buttons use normal downloads). Accepts an STL via the file input and measures radius, wall and height from it. |
| `overlay_viewer.html` | Loads the A, B, C and D CSVs together as overlaid point clouds with direction arrows between consecutive points. Orbit with drag, zoom with wheel, window sliders to isolate a stretch of rows. |
| `cell_panel.html` | Klipper style operator panel: 2D schematic of both arms, extruders and the rotary stage with live value tags (joint angles, TCP xyz, nozzle temperature and extruder velocity, plate angle and speed), jog pad, rotary controls, temperatures, gcode style console, E-stop. Runs a simulator by default. |
| `job_runner.html` | Executes a C or D dataset as a job: HOME, PRIME, PRINT, RETRACT, DONE, with live speed and flow overrides that do not interrupt the job, pause and jog with re-approach on resume, and an E-stop with held job recovery. |

## Dataset formats

Headers are how the pages auto detect each file:

```
A  i,x,y,z
B  t,plate_angle_deg,rotary_speed_rev_s,state
C  t,x,y,z,extruder_0_mm_s,rotary_speed_rev_s
D  t,x0,y0,z0,x1,y1,z1,rotary_speed_rev_s,extruder_0_mm_s,extruder_1_mm_s
```

## Live telemetry (cell_panel.html)

The Live link pane accepts a WebSocket URL. A bridge on the cell host should send JSON messages of this shape at any rate:

```json
{"arm0": {"tcp": [x, y, z], "e": 0.0, "temp": 24.0, "target": 210.0},
 "arm1": {"tcp": [x, y, z], "e": 0.0, "temp": 24.0, "target": 210.0},
 "rotary": {"ang": 0.0, "om": 0.0}}
```

Units: mm, mm/s, degrees C, degrees, rev/s. Until a bridge exists the panel runs its simulator; the console grammar it accepts is the grammar a bridge should implement.
