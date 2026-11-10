"""Cell web panel: a browser interface for the dual-arm cell, built on speed-and-time programs.

    python3 ui/web/server.py --sim            # simulators, no hardware
    python3 ui/web/server.py                  # the cell (config/settings.yaml)
    python3 ui/web/server.py --servo          # arms streamed at 100 Hz in servo mode (smoother; firmware 1.6+)
    python3 ui/web/server.py --port 8080

then open http://<cell pc>:8000 on any browser in the lab (a tablet works).

What it exposes (all JSON, see the routes below):
    state                                   poses, bed angle, temperatures, what each device is doing
    arm jog / goto / park                   the one place coordinates are needed
    bed: speed for seconds                  (0 s = until stopped)
    extruder: rate for seconds, heat, off   maps straight onto EXTRUDE_ON RATE= TIMEOUT=
    program: compile a shape into tracks, preview (bed-frame path + arm paths), run, status, stop
    STOP                                    everything off, bed stopped, program aborted

Every action that moves something is a cell.program.Program played by one Player at a time, so a
manual purge and a full print are the same kind of thing and the STOP button covers both.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "ui")]

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from cell import program as prg, shapes  # noqa: E402
from cell.runs.cylinder import DEFAULTS, NOZZLE, PARK, SAFE_Z, SIDE, Cell  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app = FastAPI(title="cell panel")
LOG: list[str] = []


def log(msg):
    LOG.append(f"{time.strftime('%H:%M:%S')}  {msg}")
    del LOG[:-200]


class Hub:
    """The one connection to the cell and the one running Player."""

    def __init__(self, sim, arm_mode="position"):
        self.sim = sim
        self.arm_mode = arm_mode
        self.arms = {0: dict(DEFAULTS[0], T=0, side=SIDE[0]), 1: dict(DEFAULTS[1], T=1, side=SIDE[1])}
        self.cell = Cell({"arms": self.arms}, sim)
        self.player = None
        self.lock = threading.Lock()
        self.last_program = None
        try:
            for t in (0, 1):
                self.cell.gcode(f"EXTRUDE_SCALE T={t} PERCENT=100")
        except Exception as ex:
            log(f"could not reset EXTRUDE_SCALE: {ex}")
        log(f"connected ({'simulators' if sim else 'cell'})")

    def z_floor(self):
        return {f"arm{t}": a["Z0"] for t, a in self.arms.items()}

    def play(self, p: prg.Program):
        with self.lock:
            if self.player and self.player.running():
                raise HTTPException(409, "something is already running; stop it first")
            self.player = prg.Player(self.cell, p, z_floor=self.z_floor(), on_log=log, arm_mode=self.arm_mode,
                                     approach_z=SAFE_Z)
            self.last_program = p
            self.player.start()
            log(f"started: {p.name} ({p.total():.1f} s)")
        return self.player.status()

    def stop(self):
        with self.lock:
            if self.player:
                self.player.stop()
        for fn in (lambda: self.cell.gcode("EXTRUDE_OFF"), self.cell.tt.stop_rotation):
            try:
                fn()
            except Exception as ex:
                log(f"stop: {ex}")
        log("STOP: extruders off, bed stopped")

    def state(self):
        st = {"sim": self.sim, "arm_mode": self.arm_mode, "arms": {}, "bed": {"deg": None}, "program": self.player.status() if self.player else None,
              "log": LOG[-30:]}
        try:
            st["bed"]["deg"] = round(self.cell.tt.get_angle(), 2)
        except Exception as ex:
            st["bed"]["error"] = str(ex)
        for t, a in self.arms.items():
            d = {"side": a["side"], "nozzle": NOZZLE[t], "z0": a["Z0"], "angle": a["ANGLE"], "cx": a["CX"], "cy": a["CY"]}
            try:
                p = self.cell.pose(t)
                d["pose"] = [round(v, 2) for v in p[:3]]
            except Exception as ex:
                d["error"] = str(ex)
            try:
                d["temp"] = round(self.cell.hot_end_temp(t), 1) if not self.sim else None
            except Exception:
                d["temp"] = None
            st["arms"][t] = d
        return st


hub: Hub | None = None


# ----------------------------------------------------------------------
class Jog(BaseModel):
    dx: float = 0
    dy: float = 0
    dz: float = 0
    speed: float = 20


class Goto(BaseModel):
    x: float
    y: float
    z: float
    speed: float = 20


class BedCmd(BaseModel):
    deg_per_s: float
    seconds: float = 0           # 0: run until stopped


class ExtCmd(BaseModel):
    rate: float                  # filament mm/s
    seconds: float


class Heat(BaseModel):
    temp: float


class Shape(BaseModel):
    kind: str = "rounded_square"         # cylinder | rounded_square | rounded_polygon
    arms: list[int] = [0]
    R: float = 60                        # cylinder
    side: float = 80                     # polygons
    rf: float = 10
    n: int = 4
    V: float = 8                         # bead speed, mm/s
    W: float = 1.0
    L_H: float = 0.6
    flow_k: float = 1.3
    loops: float = 3
    flat_first: float = 1
    flat_last: float = 1
    pitch: float | None = None
    first: float | None = None
    prime_s: float = 5
    step_deg: float = 2


class ProgramIn(BaseModel):
    program: dict


def compile_shape(s: Shape) -> prg.Program:
    arms = {t: hub.arms[t] for t in s.arms}
    kw = dict(V=s.V, W=s.W, L_H=s.L_H, flow_k=s.flow_k, loops=s.loops, flat_first=s.flat_first,
              flat_last=s.flat_last, pitch=s.pitch, first=s.first, prime_s=s.prime_s, step_deg=s.step_deg)
    if s.kind == "cylinder":
        return shapes.cylinder(arms, s.R, **kw)
    if s.kind == "rounded_square":
        return shapes.rounded_square_program(arms, s.side, s.rf, **kw)
    if s.kind == "rounded_polygon":
        return shapes.rounded_polygon_program(arms, s.n, s.side, s.rf, **kw)
    raise HTTPException(400, f"unknown shape {s.kind!r}")


def preview(p: prg.Program):
    rows = prg.simulate(p, tick=0.1)
    out = {"program": p.to_dict(), "total": round(p.total(), 2), "summary": p.summary(), "arms": {}}
    for t in (0, 1):
        if f"arm{t}" in p.tracks:
            path = shapes.bed_frame_path(rows, hub.arms[t], t)
            out["arms"][t] = {"bed_frame": [[round(v, 2) for v in q] for q in path[::2]],
                              "arm_frame": [[round(v, 2) for v in r[f"arm{t}"]] for r in rows[::2] if r[f"arm{t}"]]}
    out["bed"] = [[r["t"], round(r["bed_dps"], 2)] for r in rows[::2]]
    out["ext"] = {t: [[r["t"], round(r[f"ext{t}"], 2)] for r in rows[::2]] for t in (0, 1) if f"ext{t}" in p.tracks}
    return out


# ----------------------------------------------------------------------
@app.get("/api/state")
def api_state():
    return hub.state()


@app.post("/api/stop")
def api_stop():
    hub.stop()
    return {"ok": True}


@app.post("/api/arm/{tool}/jog")
def api_jog(tool: int, j: Jog):
    a = hub.arms[tool]
    x, y, z = hub.cell.pose(tool)[:3]
    target = (x + j.dx, y + j.dy, z + j.dz)
    if target[2] < a["Z0"] - 1e-6:
        raise HTTPException(400, f"Z {target[2]:.2f} is below {a['side']}'s Z0 {a['Z0']:.2f}")
    dur = max(0.2, math.dist((x, y, z), target) / max(j.speed, 1))
    log(f"{a['side']} jog {j.dx:+g} {j.dy:+g} {j.dz:+g}")
    return hub.play(shapes.arm_goto(tool, target, dur))


@app.post("/api/arm/{tool}/goto")
def api_goto(tool: int, g: Goto):
    a = hub.arms[tool]
    if g.z < a["Z0"] - 1e-6:
        raise HTTPException(400, f"Z {g.z:.2f} is below {a['side']}'s Z0 {a['Z0']:.2f}")
    cur = hub.cell.pose(tool)[:3]
    dur = max(0.2, math.dist(cur, (g.x, g.y, g.z)) / max(g.speed, 1))
    log(f"{a['side']} goto {g.x:.1f} {g.y:.1f} {g.z:.1f}")
    return hub.play(shapes.arm_goto(tool, (g.x, g.y, g.z), dur))


@app.post("/api/arm/{tool}/park")
def api_park(tool: int):
    x, y, z = hub.cell.pose(tool)[:3]
    p = prg.Program(f"park {SIDE[tool]}")
    tr = p.track(f"arm{tool}")
    up = max(z, SAFE_Z)
    tr.add((x, y, up), max(0.5, abs(up - z) / 40))
    tr.add(tuple(PARK), max(1.0, math.dist((x, y, up), PARK) / 60))
    log(f"{SIDE[tool]} park")
    return hub.play(p)


@app.post("/api/bed")
def api_bed(b: BedCmd):
    if b.seconds <= 0:                                  # until stopped
        hub.cell.tt.rotate_velocity(b.deg_per_s)
        log(f"bed {b.deg_per_s:g} deg/s until stopped")
        return {"ok": True}
    log(f"bed {b.deg_per_s:g} deg/s for {b.seconds:g} s")
    return hub.play(shapes.bed_jog(b.deg_per_s, b.seconds))


@app.post("/api/bed/stop")
def api_bed_stop():
    hub.cell.tt.stop_rotation()
    return {"ok": True}


@app.post("/api/extruder/{tool}")
def api_ext(tool: int, e: ExtCmd):
    log(f"{SIDE[tool]} extrude {e.rate:g} mm/s for {e.seconds:g} s")
    return hub.play(shapes.purge(tool, e.rate, e.seconds))


@app.post("/api/extruder/{tool}/off")
def api_ext_off(tool: int):
    hub.cell.gcode(f"EXTRUDE_OFF T={tool}")
    return {"ok": True}


@app.post("/api/extruder/{tool}/heat")
def api_heat(tool: int, h: Heat):
    hub.cell.ext.set_temperature(tool, h.temp)
    log(f"{SIDE[tool]} heat to {h.temp:g} C")
    return {"ok": True}


@app.post("/api/program/compile")
def api_compile(s: Shape):
    try:
        return preview(compile_shape(s))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.post("/api/program/run")
def api_run(body: ProgramIn):
    p = prg.Program.from_dict(body.program)
    return hub.play(p)


@app.post("/api/program/run_shape")
def api_run_shape(s: Shape):
    try:
        return hub.play(compile_shape(s))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.get("/api/program/status")
def api_status():
    return hub.player.status() if hub.player else {"running": False, "t": 0, "total": 0, "applied": {}}


@app.post("/api/program/stop")
def api_prog_stop():
    hub.stop()
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main(argv=None):
    global hub
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sim", action="store_true", help="cell studio's simulators instead of the hardware")
    ap.add_argument("--servo", action="store_true", help="stream arm poses at 100 Hz in xArm servo mode (firmware 1.6+) instead of queued moves")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args(argv)
    hub = Hub(a.sim, "servo" if a.servo else "position")
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
