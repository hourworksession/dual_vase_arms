"""Bench test: does pause actually stop the filament, and how fast?

Starts the fake Moonraker, streams at 3 mm/s on E and 2 mm/s on X for 4 s,
pauses, measures what the "motor" did after the pause call, resumes for 2 s,
stops. Prints a short report. No hardware needed.
"""
import sys, os, threading, time, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test import fake_moonraker
from src.extruder_controller import ExtruderController
from src.extruder_stream import ExtruderStream

threading.Thread(target=fake_moonraker.serve, daemon=True).start(); time.sleep(0.3)
ext = ExtruderController("127.0.0.1", 7125)
motor = lambda: requests.get("http://127.0.0.1:7125/printer/objects/query").json()["result"]["status"]

E0, E1, LEAD = 3.0, 2.0, 0.5
s = ExtruderStream(ext, lead_s=LEAD)
s.start()
t0 = time.monotonic(); leads = []
while time.monotonic() - t0 < 4.0:
    s.tick(E0, E1); leads.append(s.status()["lead_s"]); time.sleep(0.05)

sent_at_pause = s.status()["sent_e0_mm"]
done_at_pause = motor()["e"]
tp = time.monotonic(); s.pause(); t_pause = time.monotonic() - tp
after = motor()
time.sleep(1.0)                                   # sit paused for a second
later = motor()

s.resume()
t1 = time.monotonic()
while time.monotonic() - t1 < 2.0:
    s.tick(E0, E1); time.sleep(0.05)
s.stop()
final = motor()

print(f"commanded during run : {E0*4:.2f} mm E   ({E0} mm/s x 4 s)")
print(f"sent to queue        : {sent_at_pause:.2f} mm E")
print(f"motor done at pause(): {done_at_pause:.2f} mm E   (queue held {sent_at_pause-done_at_pause:.2f} mm, ~{LEAD}s)")
print(f"pause() returned in  : {t_pause*1000:.0f} ms (M400 drain + retract)")
print(f"motor after pause()  : {after['e']:.2f} mm E   -> retract of 1 mm applied: {after['e'] < sent_at_pause}")
print(f"motor 1 s later      : {later['e']:.2f} mm E   -> moved while paused: {later['e']!=after['e']}")
print(f"motor after resume+stop: {final['e']:.2f} mm E, {final['x']:.2f} mm X, {final['moves']} moves")
print(f"lead stability       : min {min(leads):.3f} s, max {max(leads):.3f} s (target {LEAD})")
