#!/usr/bin/env python3
"""cellcam: the cell's camera Pi (Pi 3 B + official touchscreen + Pi camera).

Streams the camera over the network and shows it full screen on the touchscreen
with an alignment crosshair, so a nozzle can be jogged onto the cross by eye
(the same idea as a tool-changer's nozzle-alignment camera).

    python3 cellcam.py                 # on the camera Pi (picamera2 ships with Raspberry Pi OS)
    python3 cellcam.py --test          # anywhere: a moving test pattern instead of the camera

Then open  http://<camera-pi>:8080  (touchscreen: http://localhost:8080 in kiosk mode).

    /                 live view, crosshair, zoom and freeze buttons
    /stream.mjpg      MJPEG stream (OpenCV: cv2.VideoCapture("http://<pi>:8080/stream.mjpg"))
    /snapshot.jpg     the latest frame as one JPEG (for scripts on the lab PC)
    /zoom?level=2     digital zoom about the centre (1, 2, 4 or 8); /status gives JSON

Only the standard library and picamera2 (or Pillow for --test) are needed.
"""
from __future__ import annotations

import argparse
import io
import json
import socketserver
import threading
import time
from http import server
from urllib.parse import parse_qs, urlparse

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cell camera</title>
<style>
  html,body{margin:0;height:100%;background:#000;color:#eee;font:16px system-ui,sans-serif;overflow:hidden}
  #wrap{position:relative;width:100vw;height:100vh}
  #view{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
  svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:visible}
  #bar{position:absolute;left:0;right:0;bottom:0;display:flex;gap:8px;padding:8px;
       background:rgba(0,0,0,.55);align-items:center;flex-wrap:wrap}
  button{font:inherit;font-size:18px;min-width:64px;min-height:48px;border:1px solid #666;
         border-radius:8px;background:#222;color:#eee}
  button.on{background:#c8102e;border-color:#c8102e}
  #info{margin-left:auto;font-size:14px;opacity:.8}
</style></head><body>
<div id="wrap">
  <img id="view" src="stream.mjpg" alt="camera">
  <svg id="cross" viewBox="-500 -500 1000 1000" preserveAspectRatio="xMidYMid meet">
    <g stroke="#00ff66" stroke-width="2" fill="none" opacity="0.9">
      <line x1="-5000" y1="0" x2="-40" y2="0"/><line x1="40" y1="0" x2="5000" y2="0"/>
      <line x1="0" y1="-5000" x2="0" y2="-40"/><line x1="0" y1="40" x2="0" y2="5000"/>
      <circle r="40"/><circle r="120" stroke-dasharray="8 8"/><circle r="240" stroke-dasharray="4 12"/>
      <circle r="2" fill="#00ff66"/>
    </g>
  </svg>
  <div id="bar">
    <button data-z="1">1x</button><button data-z="2">2x</button>
    <button data-z="4">4x</button><button data-z="8">8x</button>
    <button id="freeze">Freeze</button><button id="xh" class="on">Cross</button>
    <span id="info"></span>
  </div>
</div>
<script>
const view = document.getElementById('view'), info = document.getElementById('info');
let frozen = false;
function mark(z){ document.querySelectorAll('[data-z]').forEach(b => b.classList.toggle('on', +b.dataset.z === z)); }
document.querySelectorAll('[data-z]').forEach(b => b.onclick = async () => {
  const r = await fetch('zoom?level=' + b.dataset.z); const s = await r.json(); mark(s.zoom);
});
document.getElementById('freeze').onclick = e => {
  frozen = !frozen; e.target.classList.toggle('on', frozen);
  view.src = frozen ? 'snapshot.jpg?t=' + Date.now() : 'stream.mjpg?t=' + Date.now();
};
document.getElementById('xh').onclick = e => {
  const c = document.getElementById('cross'); const on = c.style.display === 'none';
  c.style.display = on ? '' : 'none'; e.target.classList.toggle('on', on);
};
async function poll(){
  try { const s = await (await fetch('status')).json(); mark(s.zoom);
        info.textContent = `${s.size[0]}x${s.size[1]}  ${s.fps.toFixed(1)} fps  zoom ${s.zoom}x  ${s.source}`; }
  catch(e) { info.textContent = 'no connection'; }
  setTimeout(poll, 2000);
}
poll();
</script></body></html>
"""


class FrameBuffer(io.BufferedIOBase):
    """Holds the latest JPEG; the encoder writes into it, HTTP clients wait on it."""

    def __init__(self):
        self.frame = None
        self.count = 0
        self.t0 = time.monotonic()
        self.cond = threading.Condition()

    def write(self, buf):
        with self.cond:
            self.frame = bytes(buf)
            self.count += 1
            self.cond.notify_all()
        return len(buf)

    def fps(self):
        dt = time.monotonic() - self.t0
        return self.count / dt if dt > 0 else 0.0


class PiCamera:
    """picamera2 with the hardware MJPEG encoder (Pi 3/4); digital zoom via ScalerCrop."""

    source = "picamera2"

    def __init__(self, size, out):
        from picamera2 import Picamera2
        from picamera2.encoders import MJPEGEncoder
        from picamera2.outputs import FileOutput

        self.cam = Picamera2()
        self.cam.configure(self.cam.create_video_configuration(main={"size": size}))
        self.size = size
        self.full = self.cam.camera_properties.get("ScalerCropMaximum") or (
            0, 0, *self.cam.camera_properties["PixelArraySize"])
        if "AfMode" in self.cam.camera_controls:          # Camera Module 3: keep refocusing
            self.cam.set_controls({"AfMode": 2})
        self.cam.start_recording(MJPEGEncoder(), FileOutput(out))
        self.zoom = 1

    def set_zoom(self, level):
        x, y, w, h = self.full
        # keep the stream's aspect ratio so the picture is not stretched
        aspect = self.size[0] / self.size[1]
        cw = w / level
        ch = cw / aspect
        if ch > h / level:
            ch = h / level
            cw = ch * aspect
        crop = (int(x + (w - cw) / 2), int(y + (h - ch) / 2), int(cw), int(ch))
        self.cam.set_controls({"ScalerCrop": crop})
        self.zoom = level

    def close(self):
        self.cam.stop_recording()


class TestCamera:
    """A moving test pattern (needs Pillow), for trying the page without a camera."""

    source = "test pattern"

    def __init__(self, size, out):
        from PIL import Image, ImageDraw

        self.size, self.out, self.zoom = size, out, 1
        self._img, self._draw = Image, ImageDraw
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        w, h = self.size
        t = 0
        while self.running:
            im = self._img.new("RGB", (w, h), (40, 40, 48))
            d = self._draw.Draw(im)
            r = 12 * self.zoom
            cx = w / 2 + 60 * self.zoom * (0.5 + 0.5 * ((t % 100) / 50 - 1))
            cy = h / 2 - 25 * self.zoom
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(230, 180, 60))     # "nozzle tip"
            d.text((10, 10), f"test frame {t}  zoom {self.zoom}x", fill=(200, 200, 200))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            self.out.write(buf.getvalue())
            t += 1
            time.sleep(1 / 15)

    def set_zoom(self, level):
        self.zoom = level

    def close(self):
        self.running = False


def make_handler(out, cam):
    class Handler(server.BaseHTTPRequestHandler):
        def log_message(self, *a):                   # keep the console quiet
            pass

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _latest(self, timeout=5.0):
            with out.cond:
                if out.frame is None:
                    out.cond.wait(timeout)
                return out.frame

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif url.path == "/snapshot.jpg":
                frame = self._latest()
                if frame is None:
                    self._send(503, "text/plain", b"no frame yet")
                else:
                    self._send(200, "image/jpeg", frame)
            elif url.path == "/status":
                body = {"size": list(cam.size), "fps": out.fps(), "zoom": cam.zoom, "source": cam.source}
                self._send(200, "application/json", json.dumps(body).encode())
            elif url.path == "/zoom":
                try:
                    level = int(parse_qs(url.query).get("level", ["1"])[0])
                except ValueError:
                    level = 0
                if level not in (1, 2, 4, 8):
                    self._send(400, "text/plain", b"level must be 1, 2, 4 or 8")
                    return
                cam.set_zoom(level)
                self._send(200, "application/json", json.dumps({"zoom": cam.zoom}).encode())
            elif url.path == "/stream.mjpg":
                self.send_response(200)
                self.send_header("Age", "0")
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Pragma", "no-cache")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
                self.end_headers()
                try:
                    while True:
                        with out.cond:
                            out.cond.wait()
                            frame = out.frame
                        self.wfile.write(b"--FRAME\r\nContent-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self._send(404, "text/plain", b"not found")

    return Handler


class Server(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--size", default="1280x720", help="stream size WxH (default 1280x720)")
    ap.add_argument("--test", action="store_true", help="test pattern instead of the camera")
    args = ap.parse_args(argv)
    size = tuple(int(v) for v in args.size.lower().split("x"))

    out = FrameBuffer()
    cam = (TestCamera if args.test else PiCamera)(size, out)
    srv = Server(("", args.port), make_handler(out, cam))
    print(f"cellcam: {cam.source} {size[0]}x{size[1]} on http://0.0.0.0:{args.port}/  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cam.close()
        srv.server_close()


if __name__ == "__main__":
    main()
