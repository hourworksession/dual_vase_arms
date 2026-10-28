"""A minimal fake Moonraker/Klipper for bench-testing ExtruderStream without hardware.

Accepts POST /printer/gcode/script (sync or ?async=true), parses G1 X/E/F,
queues each move with its real duration and executes the queue in real time
in a background thread. M400 blocks until the queue is empty, like Klipper.
Tracks total E and X extruded so a test can read what "the motor" did.

Run:  python -m test.fake_moonraker   (listens on 127.0.0.1:7125)
"""
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
import json

_lock = threading.Lock()
_queue = []            # (e_mm, x_mm, seconds)
_done = {"e": 0.0, "x": 0.0, "moves": 0, "last_move_end": 0.0}
_idle = threading.Event(); _idle.set()
_G1 = re.compile(r"G1\s+(.*)")


def _parse(script):
    for line in script.splitlines():
        m = _G1.match(line.strip())
        if not m:
            continue
        e = x = 0.0; f = 300.0
        for tok in m.group(1).split():
            if tok[0] == "E": e = float(tok[1:])
            elif tok[0] == "X": x = float(tok[1:])
            elif tok[0] == "F": f = float(tok[1:])
        dist = (e * e + x * x) ** 0.5
        secs = dist / (f / 60.0) if f > 0 else 0.0
        with _lock:
            _queue.append((e, x, secs)); _idle.clear()


def _runner():
    while True:
        with _lock:
            mv = _queue.pop(0) if _queue else None
        if mv is None:
            _idle.set(); time.sleep(0.005); continue
        e, x, secs = mv
        time.sleep(secs)                 # the "motor" runs for this long
        with _lock:
            _done["e"] += e; _done["x"] += x; _done["moves"] += 1
            _done["last_move_end"] = time.monotonic()
            if not _queue: _idle.set()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode()
        script = parse_qs(raw).get("script", [raw])[0]
        is_async = "async=true" in (u.query or "")
        if script.strip().startswith("M400"):
            _idle.wait(); self._json({"result": "ok"}); return
        _parse(script)
        if not is_async:
            _idle.wait()                 # sync = wait for completion, like Klipper
        self._json({"result": "ok"})

    def do_GET(self):
        with _lock:
            q = len(_queue); d = dict(_done)
        self._json({"result": {"status": {"queue": q, **d}}})


def serve(port=7125):
    threading.Thread(target=_runner, daemon=True).start()
    HTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    serve()
