"""Extruder control from the terminal, like the extrude buttons on a Klipper panel.

For the test config ("Klipper Configurations/non_extruder_control_test.cfg").
Tools: "right" (or 0) and "left" (or 1). Run from the repo root.

Interactive (type the methods):

    python3 -i extrude.py            # add --sim to practise without hardware
    >>> heat("right", 225)           # set temperature (add wait=True to block until there)
    >>> temps()                      # both temperatures and targets
    >>> purge("right", 50, 3)        # push 50 mm at 3 mm/s and stop (waits until done)
    >>> retract("right", 2)          # pull back 2 mm
    >>> on("left", 2)                # run continuously at 2 mm/s until off()
    >>> on("left", 2, seconds=10)    # run for 10 s, then stop
    >>> flow("left", 50)             # throttle the running tool to 50 %
    >>> off()                        # stop both (off("left") for one)
    >>> status()

One-off commands:

    python3 extrude.py heat right 225 --wait
    python3 extrude.py purge right 50 3
    python3 extrude.py on left 2 --seconds 10
    python3 extrude.py off
    python3 extrude.py status

Safety: purge/on refuse below 190 C (manual_stepper has no cold-extrusion check);
a running tool stops by itself 5 s after its last keepalive (firmware watchdog),
on() keeps it alive for at most 120 s unless you give a longer `seconds`, and
leaving the terminal turns everything off.
"""
from __future__ import annotations

import argparse
import atexit
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

MIN_TEMP = 190.0          # C, refuse to move filament below this
WATCHDOG_S = 5.0          # firmware stops a tool this long after the last keepalive
MAX_ON_S = 120.0          # on() without `seconds` stops after this, in case it is forgotten
HEATERS = {0: "right_hotend", 1: "left_hotend"}
STEPPERS = {0: "right", 1: "left"}

_link = None
_keep = {}                # tool -> threading.Event that stops its keepalive thread


# ----------------------------------------------------------------------
class _Moonraker:
    def __init__(self, host, port):
        import requests
        self.base, self.http = f"http://{host}:{port}", requests.Session()

    def gcode(self, script, timeout=10):
        r = self.http.post(f"{self.base}/printer/gcode/script", json={"script": script}, timeout=timeout)
        if r.status_code != 200:
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                msg = r.text[:200]
            raise RuntimeError(f"Klipper refused '{script}': {msg}")
        return r.json()

    def query(self, *objs):
        q = "&".join(o.replace(" ", "%20") for o in objs)
        r = self.http.get(f"{self.base}/printer/objects/query?{q}", timeout=5)
        r.raise_for_status()
        return r.json()["result"]["status"]


class _Sim:
    """Stand-in Klipper for practice: heaters warm up, ON runs until OFF or the watchdog."""

    def __init__(self):
        self.temp, self.target, self.t = [22.0, 22.0], [0.0, 0.0], time.monotonic()
        self.on, self.rate, self.scale, self.deadline, self.pushed = [0, 0], [0.0, 0.0], [100.0, 100.0], [0.0, 0.0], [0.0, 0.0]

    def _step(self):
        now = time.monotonic()
        dt, self.t = now - self.t, now
        for i in (0, 1):
            self.temp[i] += (max(self.target[i], 22.0) - self.temp[i]) * min(1.0, dt * 0.8)
            if self.on[i]:
                self.pushed[i] += self.rate[i] * self.scale[i] / 100 * dt
                if now > self.deadline[i]:
                    self.on[i] = 0

    def gcode(self, script, timeout=10):
        self._step()
        w = script.split()
        p = dict(x.split("=", 1) for x in w[1:] if "=" in x)
        t = int(p.get("T", -1))
        if w[0] == "EXTRUDE_TEMP":
            self.target[t] = float(p["TARGET"])
        elif w[0] == "EXTRUDE_ON":
            self.on[t], self.rate[t], self.deadline[t] = 1, float(p["RATE"]), time.monotonic() + float(p["TIMEOUT"])
        elif w[0] == "EXTRUDE_OFF":
            for i in ((0, 1) if t < 0 else (t,)):
                self.on[i] = 0
        elif w[0] == "EXTRUDE_SCALE":
            self.scale[t] = float(p["PERCENT"])
        elif w[0] == "EXTRUDE_MM":
            mm, rate = float(p["MM"]), float(p["RATE"])
            self.pushed[t] += mm
            time.sleep(min(abs(mm) / rate, 3) * 0.1)        # a tenth of real time, capped
        return {"result": "ok"}

    def query(self, *objs):
        self._step()
        return {"heater_generic right_hotend": {"temperature": self.temp[0], "target": self.target[0]},
                "heater_generic left_hotend": {"temperature": self.temp[1], "target": self.target[1]},
                "gcode_macro _EXT": {"on": list(self.on), "rate": list(self.rate), "scale": list(self.scale)},
                "webhooks": {"state": "ready", "state_message": "simulated"}}


# ----------------------------------------------------------------------
def connect(host: str | None = None, port: int | None = None, sim: bool = False):
    """Connect to Moonraker (address from config/settings.yaml unless given), or a simulator."""
    global _link
    if sim:
        _link = _Sim()
        print("Connected to the SIMULATOR (no hardware).")
        return
    from config_loader import load_config
    mr = (load_config() or {}).get("moonraker", {})
    host, port = host or mr.get("host", "127.0.0.1"), port or int(mr.get("port", 7125))
    _link = _Moonraker(host, port)
    st = _link.query("webhooks", "heater_generic right_hotend", "gcode_macro _EXT")
    wh = st.get("webhooks", {})
    print(f"Connected to {host}:{port}. Klipper: {wh.get('state')} {wh.get('state_message', '')[:60]}")
    if "gcode_macro _EXT" not in st or "heater_generic right_hotend" not in st:
        print("WARNING: the test config is not loaded (no EXTRUDE macros / right_hotend). "
              "Load 'Klipper Configurations/non_extruder_control_test.cfg' as printer.cfg.")


def _need():
    if _link is None:
        connect()
    return _link


def _tool(tool) -> int:
    t = {"right": 0, "r": 0, "0": 0, "left": 1, "l": 1, "1": 1}.get(str(tool).strip().lower())
    if t is None:
        raise ValueError(f"tool must be 'right'/0 or 'left'/1 (got {tool!r})")
    return t


def _temps() -> list[tuple[float, float]]:
    st = _need().query("heater_generic right_hotend", "heater_generic left_hotend")
    return [(st[f"heater_generic {HEATERS[t]}"]["temperature"], st[f"heater_generic {HEATERS[t]}"]["target"])
            for t in (0, 1)]


def _check_hot(t: int):
    temp = _temps()[t][0]
    if temp < MIN_TEMP:
        raise RuntimeError(f"{STEPPERS[t]} is at {temp:.0f} C, below {MIN_TEMP:.0f} C. "
                           f"heat('{STEPPERS[t]}', 225, wait=True) first.")


def heat(tool, temp: float, wait: bool = False):
    """Set a hot end's target (0 = off). wait=True blocks until within 5 C."""
    t = _tool(tool)
    _need().gcode(f"EXTRUDE_TEMP T={t} TARGET={temp:.0f}")
    print(f"{STEPPERS[t]} target {temp:.0f} C")
    if wait and temp > 0:
        while True:
            now = _temps()[t][0]
            print(f"\r  {STEPPERS[t]} {now:5.1f} / {temp:.0f} C", end="", flush=True)
            if abs(now - temp) <= 5:
                print("  reached")
                return
            time.sleep(1)


def temps():
    """Print both hot ends: temperature / target."""
    for t, (now, tgt) in enumerate(_temps()):
        print(f"  {STEPPERS[t]:5}  {now:5.1f} / {tgt:.0f} C")


def purge(tool, mm: float = 50, speed: float = 3):
    """Push `mm` of filament at `speed` mm/s and stop. Waits until it has finished."""
    t = _tool(tool)
    _check_hot(t)
    off(t, quiet=True)
    t0 = time.monotonic()
    _need().gcode(f"EXTRUDE_MM T={t} MM={mm:.3f} RATE={speed:.3f}")
    if isinstance(_link, _Moonraker):        # hand the stepper's time to the toolhead, then wait for it
        _link.gcode(f"MANUAL_STEPPER STEPPER={STEPPERS[t]} SYNC=1\nM400", timeout=abs(mm) / speed + 15)
    print(f"{STEPPERS[t]}: {'pushed' if mm >= 0 else 'pulled'} {abs(mm):g} mm at {speed:g} mm/s "
          f"({time.monotonic() - t0:.1f} s)")


def retract(tool, mm: float = 2, speed: float = 20):
    """Pull filament back `mm` at `speed` mm/s."""
    purge(tool, -abs(mm), speed)


def on(tool, speed: float = 2, seconds: float | None = None):
    """Extrude continuously at `speed` mm/s until off(), or for `seconds` if given."""
    t = _tool(tool)
    _check_hot(t)
    link = _need()
    link.gcode(f"EXTRUDE_ON T={t} RATE={speed:.3f} TIMEOUT={WATCHDOG_S:.1f}")
    stop = threading.Event()
    if t in _keep:
        _keep[t].set()
    _keep[t] = stop
    limit = seconds if seconds is not None else MAX_ON_S

    def keepalive():
        end = time.monotonic() + limit
        while not stop.wait(WATCHDOG_S / 2.5):
            if time.monotonic() >= end:
                break
            try:
                link.gcode(f"EXTRUDE_ON T={t} RATE={speed:.3f} TIMEOUT={WATCHDOG_S:.1f}")
            except Exception as e:
                print(f"\nkeepalive failed ({e}); the watchdog will stop {STEPPERS[t]} within {WATCHDOG_S:g} s")
                return
        if not stop.is_set():
            link.gcode(f"EXTRUDE_OFF T={t}")
            print(f"\n{STEPPERS[t]}: stopped after {limit:g} s")
    threading.Thread(target=keepalive, daemon=True).start()
    print(f"{STEPPERS[t]}: ON at {speed:g} mm/s " + (f"for {seconds:g} s" if seconds else
                                                     f"until off() (auto-off after {MAX_ON_S:g} s)"))
    if seconds is not None and not sys.flags.interactive:
        time.sleep(seconds + 0.5)                     # one-off command: wait for it to finish


def off(tool=None, quiet: bool = False):
    """Stop one tool, or both."""
    ts = (0, 1) if tool is None else (_tool(tool),)
    for t in ts:
        if t in _keep:
            _keep.pop(t).set()
    if _link is not None:
        _link.gcode("EXTRUDE_OFF" if tool is None else f"EXTRUDE_OFF T={ts[0]}")
    if not quiet:
        print("both OFF" if tool is None else f"{STEPPERS[ts[0]]} OFF")


def flow(tool, percent: float):
    """Throttle a running tool, 0 to 300 %."""
    t = _tool(tool)
    _need().gcode(f"EXTRUDE_SCALE T={t} PERCENT={percent:.0f}")
    print(f"{STEPPERS[t]} flow {percent:.0f} %")


def status():
    st = _need().query("webhooks", "heater_generic right_hotend", "heater_generic left_hotend", "gcode_macro _EXT")
    ext = st.get("gcode_macro _EXT", {})
    print(f"Klipper: {st.get('webhooks', {}).get('state')}")
    for t in (0, 1):
        h = st.get(f"heater_generic {HEATERS[t]}", {})
        on_ = ext.get("on", [0, 0])[t]
        print(f"  {STEPPERS[t]:5}  {h.get('temperature', 0):5.1f} / {h.get('target', 0):.0f} C   "
              f"{'EXTRUDING' if on_ else 'off'}  rate {ext.get('rate', [0, 0])[t]:.2f} mm/s  "
              f"flow {ext.get('scale', [100, 100])[t]:.0f} %")


def _shutdown():
    try:
        if _link is not None and _keep:
            off(quiet=True)
    except Exception:
        pass


atexit.register(_shutdown)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Extruder control from the terminal (test config).")
    ap.add_argument("--sim", action="store_true", help="practise without hardware")
    ap.add_argument("--host")
    sub = ap.add_subparsers(dest="cmd")
    h = sub.add_parser("heat")
    h.add_argument("tool")
    h.add_argument("temp", type=float)
    h.add_argument("--wait", action="store_true")
    p = sub.add_parser("purge")
    p.add_argument("tool")
    p.add_argument("mm", type=float, nargs="?", default=50)
    p.add_argument("speed", type=float, nargs="?", default=3)
    r = sub.add_parser("retract")
    r.add_argument("tool")
    r.add_argument("mm", type=float, nargs="?", default=2)
    o = sub.add_parser("on")
    o.add_argument("tool")
    o.add_argument("speed", type=float, nargs="?", default=2)
    o.add_argument("--seconds", type=float, required=True, help="one-off commands need a duration")
    f = sub.add_parser("off")
    f.add_argument("tool", nargs="?")
    sub.add_parser("status")
    sub.add_parser("temps")
    a = ap.parse_args(argv)
    connect(host=a.host, sim=a.sim)
    if a.cmd is None:
        return 0
    try:
        {"heat": lambda: heat(a.tool, a.temp, a.wait), "purge": lambda: purge(a.tool, a.mm, a.speed),
         "retract": lambda: retract(a.tool, a.mm), "on": lambda: on(a.tool, a.speed, a.seconds),
         "off": lambda: off(a.tool), "status": status, "temps": temps}[a.cmd]()
    except KeyboardInterrupt:
        off()
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    if sys.flags.interactive:
        connect(sim="--sim" in sys.argv, host=next((sys.argv[i + 1] for i, x in enumerate(sys.argv)
                                                    if x == "--host" and i + 1 < len(sys.argv)), None))
        print("Methods: heat, temps, purge, retract, on, off, flow, status   (help(purge) for details)")
    else:
        sys.exit(main())
