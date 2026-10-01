"""Side-camera nozzle detection for the alignment macro.

Set-up: a camera looking across the disc, perpendicular to the line along which
the two arms approach each other, with a plain light backdrop (a white card)
behind the nozzles. Both nozzles point down, so each nozzle's tip is the lowest
point of its silhouette.

detect() finds the two largest dark shapes (or, with method="edges", the shapes
outlined by strong edges), takes the lowest point of each as its tip, and
reports the horizontal tip-to-tip gap and the height difference in pixels. The
alignment macro measures the pixel scale itself by moving an arm a known
distance, so no lens calibration is needed.

Only numpy and scipy are needed for detection; OpenCV is only used to read a
real camera (pip install opencv-python).
"""

from dataclasses import dataclass, field
import threading
import time

import numpy as np

try:
    from scipy import ndimage
except ImportError:  # pragma: no cover
    ndimage = None


@dataclass
class VisionSettings:
    method: str = "silhouette"     # "silhouette" (backdrop) or "edges"
    invert: bool = False           # True when the nozzles are lighter than the backdrop
    flip: bool = False             # True when the LEFT arm appears on the right of the image
    roi: tuple = None              # (x0, y0, x1, y1) pixels, or None for the whole frame
    min_area: int = 150            # ignore specks smaller than this (pixels)
    blur: float = 1.2
    frames: int = 3                # frames averaged per measurement
    tip_rows: int = 12             # rows above the tip used to find the nozzle's centre line


@dataclass
class Detection:
    ok: bool
    message: str = ""
    left: tuple = None             # (x, y) pixels in the full frame
    right: tuple = None
    gap_px: float = None           # right tip x − left tip x (along the image horizontal)
    dz_px: float = None            # left tip y − right tip y (positive: left tip LOWER in the image)
    components: int = 0
    mask: np.ndarray = field(default=None, repr=False)
    offset: tuple = (0, 0)


def _otsu(img):
    hist, edges = np.histogram(img, bins=256, range=(float(img.min()), float(img.max()) + 1e-6))
    hist = hist.astype(float)
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    mids = (edges[:-1] + edges[1:]) / 2
    m0 = np.cumsum(hist * mids)
    mu0 = m0 / np.maximum(w0, 1e-9)
    mu1 = (m0[-1] - m0) / np.maximum(w1, 1e-9)
    between = w0 * w1 * (mu0 - mu1) ** 2
    return mids[int(np.argmax(between[:-1]))]


def to_gray(frame):
    a = np.asarray(frame, dtype=np.float32)
    if a.ndim == 3:
        a = a[..., :3].mean(axis=2)
    return a


def detect(frame, s: VisionSettings = None):
    s = s or VisionSettings()
    if ndimage is None:
        return Detection(False, "scipy is needed for nozzle detection (pip install scipy).")
    img = to_gray(frame)
    ox = oy = 0
    if s.roi:
        x0, y0, x1, y1 = [int(v) for v in s.roi]
        img = img[y0:y1, x0:x1]
        ox, oy = x0, y0
    if img.size == 0:
        return Detection(False, "The region of interest is empty.")
    if s.blur > 0:
        img = ndimage.gaussian_filter(img, s.blur)
    if float(img.max()) - float(img.min()) < 20:
        return Detection(False, "The image has almost no contrast: is the camera covered or the light off?")
    if s.method == "edges":
        gx = ndimage.sobel(img, axis=1)
        gy = ndimage.sobel(img, axis=0)
        mag = np.hypot(gx, gy)
        edges = mag > _otsu(mag)
        edges = ndimage.binary_closing(edges, iterations=2)
        fg = ndimage.binary_fill_holes(edges)
    else:
        t = _otsu(img)
        fg = img > t if s.invert else img < t
    fg = ndimage.binary_opening(fg, iterations=1)
    labels, n = ndimage.label(fg)
    if n == 0:
        return Detection(False, "No nozzle found. Check the backdrop and the 'nozzles lighter' setting.", mask=fg)
    areas = ndimage.sum(np.ones_like(labels), labels, index=np.arange(1, n + 1))
    big = [i + 1 for i in np.argsort(areas)[::-1] if areas[i] >= s.min_area]
    if len(big) < 2:
        msg = ("Only one shape found: the nozzles may be touching or overlapping, or one is out of view."
               if big else "No shapes large enough. Move the camera closer or lower 'min area'.")
        return Detection(False, msg, components=len(big), mask=fg, offset=(ox, oy))
    tips = []
    for lab in big[:2]:
        comp = labels == lab
        ys, xs = np.nonzero(comp)
        ymax = int(ys.max())
        # centre line of the nozzle over its lowest rows, extrapolated to the very tip: unbiased
        # for a tilted nozzle, where the middle of the lowest rows sits off the tip
        rows = np.arange(max(int(ys.min()), ymax - s.tip_rows), ymax + 1)
        cx, cy = [], []
        for r in rows:
            cols = np.nonzero(comp[r])[0]
            if cols.size:
                cx.append((cols.min() + cols.max()) / 2.0)
                cy.append(r)
        if len(cy) >= 4:
            k, b = np.polyfit(np.asarray(cy, float), np.asarray(cx, float), 1)
            tip_x = k * (ymax + 0.5) + b
        else:
            tip_x = float(xs[ys >= ymax - 1].mean())
        tips.append((float(tip_x) + ox, float(ymax) + oy, float(xs.mean())))
    tips.sort(key=lambda t: t[2])           # by mean x of the whole shape
    left, right = (tips[1], tips[0]) if s.flip else (tips[0], tips[1])
    gap = (right[0] - left[0]) * (-1 if s.flip else 1)
    return Detection(True, "", left=left[:2], right=right[:2], gap_px=gap, dz_px=left[1] - right[1],
                     components=len(big), mask=fg, offset=(ox, oy))


@dataclass
class BeadDetection:
    ok: bool
    message: str = ""
    beads: list = field(default_factory=list)    # [(u_centre, v_top, width_px, area)], largest first
    mask: np.ndarray = field(default=None, repr=False)


def detect_beads(frame, s: VisionSettings = None):
    """Bead cross-sections seen edge-on by a camera looking along the wall at the disc edge:
    one shape when the two arms' beads merge, two when they lie side by side."""
    s = s or VisionSettings()
    if ndimage is None:
        return BeadDetection(False, "scipy is needed for bead detection (pip install scipy).")
    img = to_gray(frame)
    ox = oy = 0
    if s.roi:
        x0, y0, x1, y1 = [int(v) for v in s.roi]
        img = img[y0:y1, x0:x1]
        ox, oy = x0, y0
    if img.size == 0:
        return BeadDetection(False, "The region of interest is empty.")
    if s.blur > 0:
        img = ndimage.gaussian_filter(img, s.blur)
    lo, hi = np.percentile(img, [0.5, 99.5])
    if hi - lo < 35:                     # after blurring, noise alone stays well under this
        return BeadDetection(False, "Nothing in view (no contrast): no bead, or camera covered / light off.")
    if s.method == "edges":
        mag = np.hypot(ndimage.sobel(img, axis=1), ndimage.sobel(img, axis=0))
        fg = ndimage.binary_fill_holes(ndimage.binary_closing(mag > _otsu(mag), iterations=2))
    else:
        t = _otsu(img)
        fg = img > t if s.invert else img < t
    fg = ndimage.binary_opening(fg, iterations=1)
    labels, n = ndimage.label(fg)
    beads = []
    for lab in range(1, n + 1):
        ys, xs = np.nonzero(labels == lab)
        if xs.size < s.min_area:
            continue
        beads.append((float(xs.mean()) + ox, float(ys.min()) + oy, float(xs.max() - xs.min() + 1), int(xs.size)))
    beads.sort(key=lambda b: -b[3])
    if not beads:
        return BeadDetection(False, "No bead found in view.", mask=fg)
    return BeadDetection(True, "", beads[:2], mask=fg)


class SimBeadCamera:
    """Edge-on view of the wall at one disc angle, drawn from what the simulated cell really deposited.

    deposits(theta_view) -> [(r_mm, z_top_mm, width_mm)] beads crossing the plate angle under the
    camera. Image: horizontal = radius (outwards to the right), vertical = height."""

    def __init__(self, deposits, r_centre, scale=40.0, size=(640, 360), noise=3.0, seed=2):
        self.deposits = deposits
        self.r_centre = r_centre
        self.scale = scale
        self.w, self.h = size
        self.noise = noise
        self.rng = np.random.default_rng(seed)

    def open(self):
        pass

    def close(self):
        pass

    def read(self):
        img = np.full((self.h, self.w), 210.0, dtype=np.float32)
        base_v = self.h * 0.8                       # image row of the disc surface
        yy, xx = np.mgrid[0:self.h, 0:self.w].astype(np.float32) + 0.5
        for r, ztop, wd in self.deposits():
            u = self.w / 2 + (r - self.r_centre) * self.scale
            hh = max(ztop, 0.05) * self.scale       # bead from the disc up to the nozzle
            vc = base_v - hh / 2
            inside = ((xx - u) / (wd * self.scale / 2)) ** 2 + ((yy - vc) / (hh / 2)) ** 2 <= 1.0
            img[inside] = 40.0
        if self.noise:
            img += self.rng.normal(0, self.noise, img.shape).astype(np.float32)
        return np.clip(img, 0, 255).astype(np.uint8)


def measure(camera, s: VisionSettings, frames=None):
    """Average several detections. Returns a Detection (ok=False if any frame failed)."""
    n = max(1, frames or s.frames)
    dets = []
    for _ in range(n):
        d = detect(camera.read(), s)
        if not d.ok:
            return d
        dets.append(d)
    out = dets[-1]
    out.gap_px = float(np.mean([d.gap_px for d in dets]))
    out.dz_px = float(np.mean([d.dz_px for d in dets]))
    out.left = tuple(np.mean([d.left for d in dets], axis=0))
    out.right = tuple(np.mean([d.right for d in dets], axis=0))
    return out


# ---------------------------------------------------------------- cameras
class CvCamera:
    """A real camera through OpenCV."""

    def __init__(self, index=0):
        self.index = index
        self.cap = None
        self.lock = threading.Lock()

    def open(self):
        try:
            import cv2
        except ImportError:
            raise RuntimeError("OpenCV is not installed: pip install opencv-python")
        self.cv2 = cv2
        self.cap = cv2.VideoCapture(self.index)
        if not self.cap or not self.cap.isOpened():
            raise RuntimeError(f"Camera {self.index} could not be opened. Is it plugged in, or used by another app?")
        for _ in range(3):          # let auto-exposure settle
            self.cap.read()

    def read(self):
        with self.lock:
            if self.cap is None:
                self.open()
            for _ in range(2):           # drop a buffered frame so the image is current
                self.cap.grab()
            ok, frame = self.cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"Camera {self.index} stopped returning images.")
        return self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)

    def close(self):
        with self.lock:
            if self.cap is not None:
                self.cap.release()
                self.cap = None


class SimCamera:
    """Renders both nozzle silhouettes from the simulated arms' physical positions.

    world_tip(side) -> (X, Y, Z) world mm of that arm's nozzle tip, or None. The
    camera looks along world +Y, centred on `centre` (X, Z); `scale` px/mm."""

    def __init__(self, world_tip, centre=(0.0, 30.0), scale=30.0, size=(640, 480), noise=3.0, seed=1,
                 lean_deg=45.0):
        self.world_tip = world_tip
        self.lean = np.tan(np.radians(lean_deg))     # each nozzle leans away from the other
        self.centre = centre
        self.scale = scale
        self.w, self.h = size
        self.noise = noise
        self.rng = np.random.default_rng(seed)

    def open(self):
        pass

    def close(self):
        pass

    def _half_width(self, hmm):
        """Nozzle silhouette half width (mm) at height hmm above the tip."""
        hw = np.where(hmm < 0, -1.0, 0.25 + hmm * 0.55)              # cone
        hw = np.where(hmm >= 6, 4.0, hw)                             # heater block
        hw = np.where(hmm >= 16, 7.0, hw)                            # heat sink / body
        return hw

    def read(self):
        img = np.full((self.h, self.w), 205.0, dtype=np.float32)
        ys = np.arange(self.h, dtype=np.float32)[:, None] + 0.5
        sub = 4                                                     # 4× horizontal supersampling
        xs = (np.arange(self.w * sub, dtype=np.float32)[None, :] + 0.5) / sub
        cover = np.zeros((self.h, self.w * sub), dtype=bool)
        for side in ("left", "right"):
            tip = self.world_tip(side)
            if tip is None:
                continue
            u = self.w / 2 + (tip[0] - self.centre[0]) * self.scale
            v = self.h / 2 - (tip[2] - self.centre[1]) * self.scale
            hmm = (v - ys) / self.scale
            hw = self._half_width(hmm) * self.scale
            lean = (-1.0 if side == "left" else 1.0) * self.lean * np.maximum(hmm, 0) * self.scale
            cover |= np.abs(xs - (u + lean)) <= hw
        frac = cover.reshape(self.h, self.w, sub).mean(axis=2)
        img = img * (1 - frac) + 45.0 * frac
        if self.noise:
            img += self.rng.normal(0, self.noise, img.shape).astype(np.float32)
        return np.clip(img, 0, 255).astype(np.uint8)
