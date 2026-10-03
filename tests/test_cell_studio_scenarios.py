#!/usr/bin/env python3
"""Fifty scripted user sessions against Cell Studio on simulated hardware.

Each scenario plays one person at the cell doing one job, the way people
actually use it (including the mistakes). It checks what the program told the
machine and the user against what should happen, and reports any flaw.

    QT_QPA_PLATFORM=offscreen python tests/test_cell_studio_scenarios.py
    (add --json results.json to save the table)

Runs without the cell: every device is the simulator in
scripts/cell_studio/sim_hardware.py, which can drop links, refuse connections
and hold an E-stop like the real devices.
"""

import json
import math
import os
import sys
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

import logging
logging.disable(logging.CRITICAL)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog
from PySide6.QtTest import QTest

from cell_studio import theme, home_config
from cell_studio import sim_hardware as sim

HOME_TMP = os.path.join(ROOT, "tests", "_home_positions_test.json")
home_config.HOME_FILE = HOME_TMP

from cell_studio import macros as _macros, tool as _tool
_macros.ALIGN_FILE = os.path.join(ROOT, "tests", "_alignment_test.json")
_tool.TOOL_FILE = os.path.join(ROOT, "tests", "_tool_test.json")
from cell_studio.main_window import MainWindow

MODE = "streamed" if "--mode" in sys.argv and sys.argv[sys.argv.index("--mode") + 1] == "streamed" else "single"
APP = QApplication.instance() or QApplication([])
APP.setStyle("Fusion")
APP.setStyleSheet(theme.QSS)

# ---------------------------------------------------------------- dialog capture
MSGS = []            # (kind, title, text, modal)
ANSWERS = []         # queued answers for questions: True = Yes


def _static(kind):
    def f(parent, title, text, *a, **k):
        MSGS.append((kind, title, str(text), True))
        if kind == "question":
            return QMessageBox.Yes if (ANSWERS.pop(0) if ANSWERS else True) else QMessageBox.No
        return QMessageBox.Ok
    return staticmethod(f)


for _k in ("information", "warning", "critical", "question"):
    setattr(QMessageBox, _k, _static(_k))

_orig_show = QMessageBox.show


def _mb_show(self):
    MSGS.append(("notice", self.windowTitle(), self.text(), self.isModal()))
    # never actually display during tests


QMessageBox.show = _mb_show


def _mb_exec(self):
    """Answer a modal prompt: True = the default/accept button, False = cancel, "text" = that button."""
    MSGS.append(("dialog", self.windowTitle(), self.text(), True))
    ans = ANSWERS.pop(0) if ANSWERS else True
    pick = None
    if isinstance(ans, str):
        pick = next((b for b in self.buttons() if b.text() == ans), None)
    elif ans:
        for role in (QMessageBox.AcceptRole, QMessageBox.YesRole, QMessageBox.DestructiveRole):
            pick = next((b for b in self.buttons() if self.buttonRole(b) == role), None)
            if pick:
                break
    if pick is not None:
        pick.click()
        self._clicked_for_test = pick
    return 0


QMessageBox.exec = _mb_exec
_orig_clicked = QMessageBox.clickedButton
QMessageBox.clickedButton = lambda self: getattr(self, "_clicked_for_test", None) or _orig_clicked(self)


# ---------------------------------------------------------------- helpers
def pump(sec=0.05):
    t = time.time()
    while time.time() - t < sec:
        APP.processEvents()
        time.sleep(0.005)
    APP.processEvents()


class Ctx:
    def __init__(self):
        sim.reset()
        MSGS.clear()
        ANSWERS.clear()
        for f in (HOME_TMP, _macros.ALIGN_FILE, _tool.TOOL_FILE):
            if os.path.exists(f):
                os.remove(f)
        self.w = MainWindow()
        self.w.resize(1480, 920)
        self.w.show()
        self.c = self.w.c
        self.c.extrusion_mode.set(MODE)
        self.flaws = []
        pump(0.05)

    # --- setup
    def connect(self, left=True, right=True, tt=True, ext=True, simulated=True):
        c = self.c
        c.conn_left.set(left); c.conn_right.set(right)
        c.conn_turntable.set(tt); c.conn_extruder.set(ext)
        c.conn_simulated.set(simulated)
        c.connect_hw()
        pump(0.05)

    def quick_cylinder(self, revs=0.4, speed=2.0):
        """Short, fast cylinder so a test finishes in about a second."""
        p = self.c.param_vars
        p['total_revs'].set(revs)
        self.c.turntable_speed_var.set(speed)
        # keep extrusion time equal to rotation time (as the defaults do at 0.6 rad/s)
        self.match_feed()

    def match_feed(self):
        c = self.c
        c.calculate_extrusion_lengths()
        t = c.param_vars['total_revs'].get() * 2 * math.pi / max(c.turntable_speed_var.get(), 1e-6)
        if t > 0:
            c.param_vars['feed_rate_left'].set(round(c.calc_left_len.get() / t, 4))
            c.param_vars['feed_rate_right'].set(round(c.calc_right_len.get() / t, 4))

    def wait_idle(self, timeout=8.0):
        t = time.time()
        while time.time() - t < timeout:
            pump(0.05)
            if not self.c.printing and not self.c.busy and self.c.job_state.get() == "idle":
                return True
        return False

    def wait_for(self, cond, timeout=5.0):
        t = time.time()
        while time.time() - t < timeout:
            pump(0.03)
            if cond():
                return True
        return False

    def mark(self):
        return len(sim.LOG)

    def log_since(self, m, dev=None, cmd=None):
        return [e for e in sim.LOG[m:] if (dev is None or e[1] == dev) and (cmd is None or e[2] == cmd)]

    def msgs_since(self, n):
        return MSGS[n:]

    def said(self, n, *words):
        text = " ".join((m[1] + " " + m[2]).lower() for m in MSGS[n:])
        return all(wd.lower() in text for wd in words)

    def expect(self, ok, flaw):
        if not ok:
            self.flaws.append(flaw)

    def close(self):
        try:
            if self.c.printing:
                self.c.stop_requested = True
                self.wait_idle(3)
            self.c.disconnect_hw() if not self.c.printing else None
        except Exception:
            pass
        self.c._poll_stop.set()
        self.w.hide()
        self.w.deleteLater()
        pump(0.02)


_SLICE_CACHE = {}


def sliced(ctx):
    m = ctx.w.model
    m.load_model(os.path.join(ROOT, "scripts", "cube40.3mf"))
    if "res" not in _SLICE_CACHE:
        m.do_slice()
        t = time.time()
        while getattr(m, "_slicing", False) and time.time() - t < 120:
            pump(0.05)
        _SLICE_CACHE["res"] = m.slice_result
    else:
        m.slice_result = _SLICE_CACHE["res"]
        m.layer_slider.setRange(0, len(m.slice_result.layers) - 1)
        m.do_plan()
    return m


# ---------------------------------------------------------------- scenarios
SCENARIOS = []


def scenario(who, when, job):
    def deco(fn):
        SCENARIOS.append((len(SCENARIOS) + 1, who, when, job, fn))
        return fn
    return deco


@scenario("Aisha (first day)", "08:55", "Presses Home before connecting anything")
def s01(x):
    n, m = len(MSGS), x.mark()
    x.c.home_all(); pump(0.2)
    x.expect(not x.log_since(m), "Home sent motion with nothing connected")
    x.expect(x.said(n, "not connected"), "No message explaining Home needs a connection")


@scenario("Aisha (first day)", "09:00", "Cylinder with the default connections (left arm unticked)")
def s02(x):
    x.connect(left=False)
    n, m = len(MSGS), x.mark()
    x.c.start_cylinder(); pump(0.2)
    x.expect(not x.log_since(m, cmd="move"), "Cylinder moved an arm without both arms connected")
    x.expect(x.said(n, "left"), "Warning does not say the left arm is the missing device")


@scenario("Ben (own laptop)", "09:10", "Connects real hardware on a laptop without the xArm SDK")
def s03(x):
    import importlib.util
    if importlib.util.find_spec("xarm") is not None:
        return "skipped: xArm SDK installed here"
    n = len(MSGS)
    x.connect(simulated=False)
    text = " ".join(m[2] for m in MSGS[n:]).lower()
    x.expect("pip install" in text or "sdk" in text or "simulated" in text,
             f"Connection error is raw and unhelpful: {text[:90]!r}")


@scenario("Ben (own laptop)", "09:15", "Right arm powered off; left arm and turntable fine")
def s04(x):
    sim.FAULTS["refuse_connect"] = {"right"}
    n = len(MSGS)
    x.connect()
    live = x.c.connected_var.get()
    x.expect(x.c.left is not None and x.c.hw_connected == bool(live),
             "After a partial connect the panel thinks nothing is connected although the left arm is")
    x.expect(x.said(n, "right"), "Failure message does not name the right arm")
    x.expect(x.c.turntable is not None or "turntable" in live or x.said(n, "turntable"),
             "Devices after the failing one were silently skipped (turntable, extruder never tried)")


@scenario("Chloe (PhD student)", "09:30", "Presses Connect twice in a row")
def s05(x):
    x.connect()
    first_left = x.c.left
    m = x.mark()
    x.c.connect_hw(); pump(0.1)
    disc = x.log_since(m, dev="left", cmd="disconnect")
    x.expect(bool(disc) or x.c.left is first_left,
             "Second Connect opened new sessions without closing the old ones (two live xArm connections)")


@scenario("Dan (technician)", "09:45", "Leaves Simulated ticked by accident and thinks he is printing")
def s06(x):
    x.connect(simulated=True)
    txt = " ".join(l.text() for l in x.w.findChildren(type(x.w.job_chip.lbl)) if l.isVisible()).lower()
    x.expect("simulat" in txt and hasattr(x.w, "sim_badge") and x.w.sim_badge.isVisible(),
             "No unmistakable SIMULATION banner; only teal dots distinguish it from the real cell")


@scenario("Dan (technician)", "10:00", "Standard 20 rev vase, shortened, run to completion")
def s07(x):
    x.connect(); x.quick_cylinder(0.4)
    ANSWERS.extend([True] * 3)
    x.c.start_cylinder()
    x.expect(x.wait_idle(8), "Cylinder never finished")
    tt = x.c.turntable
    x.expect(tt.velocity == 0, "Turntable still turning after the job finished")
    x.expect(abs(x.c.right.get_pose()[2] - 180.0) < 0.01, "Right arm did not park at Z 180")


@scenario("Erin (MSc)", "10:10", "Doubles turntable speed to print faster, leaves feed rates alone")
def s08(x):
    x.connect()
    x.c.param_vars['total_revs'].set(0.4)
    x.c.turntable_speed_var.set(1.2)     # defaults are tuned for 0.6 rad/s
    n = len(MSGS)
    ANSWERS.extend([False])              # would say No if asked
    x.c.start_cylinder(); pump(0.2)
    x.expect(x.said(n, "extru") and not x.c.printing,
             "No warning that extrusion time no longer matches rotation time (half the flow per mm)")
    x.wait_idle(6)


@scenario("Erin (MSc)", "10:20", "Presses Calculate, then changes total revs from 20 to 5")
def s09(x):
    x.connect()
    x.c.param_vars['total_revs'].set(20); x.c.calculate_extrusion_lengths()
    x.c.param_vars['total_revs'].set(0.3); x.c.turntable_speed_var.set(2.0)
    m = x.mark(); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.log_since(m, cmd="extrude_sync"), 3)
    ev = x.log_since(m, cmd="extrude_sync")
    used = x.c.calc_left_len.get()        # what the job was started with
    x.c.calculate_extrusion_lengths()
    fresh = x.c.calc_left_len.get()
    if MODE == "single":
        got = ev[0][3][0] if ev else None
    else:                                  # follow-turntable sends 10° chunks of the job's total
        got = used
    x.expect(got is not None and abs(got - fresh) < 0.2,
             f"Extruded the stale 20 rev amount ({got} mm) instead of {fresh:.1f} mm")
    x.c.stop_requested = True; x.wait_idle(5)


@scenario("Farid (visiting)", "10:40", "Types 0 in Total revolutions and presses Start")
def s10(x):
    x.connect(); x.c.param_vars['total_revs'].set(0)
    n, m = len(MSGS), x.mark(); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_idle(3)
    x.expect(not x.log_since(m, cmd="move"), "A 0 revolution job still moved both arms through the approach")
    x.expect(x.said(n, "revolution"), "No message about the invalid revolutions")


@scenario("Farid (visiting)", "10:45", "Radius left at 0 from a test")
def s11(x):
    x.connect(); x.c.param_vars['radius'].set(0); x.c.param_vars['total_revs'].set(0.3)
    m = x.mark(); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_idle(4)
    x.expect(not x.log_since(m, cmd="move"), "Radius 0 accepted: both nozzles driven to the turntable centre")


@scenario("Grace (undergrad)", "11:00", "Types -0.5 into turntable speed")
def s12(x):
    field = None
    from cell_studio.widgets import NumberField
    for f in x.w.cylinder.findChildren(NumberField):
        if f.var is x.c.turntable_speed_var:
            field = f
    field.spin.setValue(-0.5); pump()
    x.expect(x.c.turntable_speed_var.get() >= 0, "Negative turntable speed accepted (the job would never reach its end angle)")


@scenario("Grace (undergrad)", "11:05", "Drags the live speed slider to 0 mid-print")
def s13(x):
    x.connect(); x.quick_cylinder(0.6, 1.5); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3)
    x.c.turntable_speed_var.set(0.0); pump(1.2)
    stalled = x.c.printing and x.c.turntable.velocity == 0 and x.c.extruder.extruding()
    x.expect(not stalled, "Speed 0 mid-print: turntable stopped but extruder kept extruding into one spot, job never ends")
    x.c.stop_requested = True; x.wait_idle(5)


@scenario("Hassan (research fellow)", "11:15", "Slows the turntable mid-print to fix sagging")
def s14(x):
    x.connect(); x.quick_cylinder(0.8, 2.0); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3)
    busy_before = x.c.extruder.busy_until
    n = len(MSGS)
    x.c.turntable_speed_var.set(1.0); pump(0.3)
    x.expect(x.c.extruder.busy_until != busy_before or x.said(n, "flow") or x.said(n, "extru"),
             "Changing speed live does not change extrusion (fixed at start): flow per mm silently doubles")
    x.c.stop_requested = True; x.wait_idle(6)


@scenario("Hassan (research fellow)", "11:25", "Pauses a cylinder to look at a layer")
def s15(x):
    x.connect(); x.quick_cylinder(1.0, 1.5); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3)
    x.c.toggle_pause(); pump(0.5)
    blob = x.c.turntable.velocity == 0 and x.c.extruder.extruding()
    x.expect(not blob, "Pause stops the turntable but the extruder keeps going: a blob forms at the nozzle")
    x.c.toggle_pause(); x.c.stop_requested = True; x.wait_idle(6)


@scenario("Isla (lab manager)", "11:40", "Sees a problem during the approach and presses Stop")
def s16(x):
    x.connect(); x.quick_cylinder(0.5)
    orig = x.c.right.arm.set_position

    def slow(*a, **k):
        time.sleep(0.25)
        return orig(*a, **k)
    x.c.right.arm.set_position = slow
    m = x.mark(); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); pump(0.3)
    x.c.stop_print(); x.wait_idle(6)
    x.expect(not x.log_since(m, cmd="extrude_sync"), "Stop during the approach still started the full extrusion afterwards")


@scenario("Isla (lab manager)", "11:50", "Presses Stop halfway through a cylinder")
def s17(x):
    x.connect(); x.quick_cylinder(1.0, 1.5); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3); pump(0.8)
    x.c.stop_print(); x.wait_idle(6)
    x.expect(not x.c.extruder.extruding(), "After Stop the queued extrusion keeps running while the arms park")


@scenario("Jack (postdoc)", "12:00", "Hits EMERGENCY STOP mid-print")
def s18(x):
    x.connect(); x.quick_cylinder(1.0, 1.5); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3); pump(0.3)
    n = len(MSGS); m = x.mark()
    ANSWERS.append(True); x.w._estop(); x.wait_idle(5)
    after = [e for e in x.log_since(m) if e[2] in ("move", "move_rejected", "joint_move")]
    x.expect(not after, f"{len(after)} motion command(s) sent after the E-stop (park sequence still ran)")
    x.expect(not any(k == "critical" for k, *_ in MSGS[n:]),
             "E-stop produces a confusing 'Print Error: Turntable not connected' pop-up")


@scenario("Jack (postdoc)", "12:05", "After an E-stop, presses Home to recover")
def s19(x):
    x.connect(); ANSWERS.append(True); x.w._estop(); pump(0.1)
    n = len(MSGS)
    x.c.home_all(); x.wait_idle(3)
    x.expect(x.said(n, "connect"), "Home after E-stop fails with an xArm code instead of saying to reconnect")


@scenario("Jack (postdoc)", "12:10", "Glances at the header after an E-stop")
def s20(x):
    x.connect(); ANSWERS.append(True); x.w._estop(); pump(0.2)
    x.expect(not x.c.hw_connected and not x.c.connected_var.get(),
             "Header still shows every device green/connected after the E-stop disabled them")


@scenario("Kate (MSc)", "12:30", "Presses Disconnect while a cylinder is printing")
def s21(x):
    x.connect(); x.quick_cylinder(1.0, 1.5); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3)
    n = len(MSGS)
    x.c.disconnect_hw(); pump(0.3)
    x.expect(x.c.hw_connected, "Disconnect allowed mid-print: the job crashes and the arms are abandoned mid-move")
    x.c.stop_requested = True; x.wait_idle(6)


@scenario("Kate (MSc)", "12:35", "Closes the window with a print running")
def s22(x):
    x.connect(); x.quick_cylinder(3.0, 1.5); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3)
    tt = x.c.turntable
    n = len(MSGS)
    ANSWERS.append(True)
    x.w.close(); pump(0.8)
    x.expect(any(k in ("question", "dialog") for k, *_ in MSGS[n:]), "Window closes mid-print without asking")
    x.expect(tt.velocity == 0, "Turntable left spinning after the window closed")


@scenario("Liam (technician)", "13:00", "Right arm's Ethernet drops mid-print")
def s23(x):
    x.connect(); x.quick_cylinder(1.0, 1.5); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3)
    sim.FAULTS["drop_after_moves"] = {"right": 2}
    x.wait_idle(6); pump(0.3)          # allow the last queued 10° chunk in follow-turntable mode
    x.expect(x.c.turntable.velocity == 0, "Turntable kept spinning after the arm dropped")
    x.expect(not x.c.extruder.extruding(), "Extruder kept extruding after the arm dropped mid-print")


@scenario("Liam (technician)", "13:05", "Watches the Live panel after the arm dropped")
def s24(x):
    x.connect(); pump(1.2)
    sim.FAULTS["drop_after_moves"] = {"right": 0}
    before = x.c.tel['right_pos'].get()
    pump(2.3)
    x.expect(x.c.tel['right_pos'].get() != before, "Live panel keeps showing the last pose of an arm that stopped answering")


@scenario("Maya (undergrad)", "13:30", "Prepare to print with the extruder unticked")
def s25(x):
    x.connect(ext=False); n = len(MSGS)
    x.c.prepare_to_print(); x.wait_idle(9)
    x.expect(not x.said(n, "ready to print") or x.said(n, "extruder"),
             "Says 'Ready to print' although nothing was heated (extruder not connected)")


@scenario("Maya (undergrad)", "13:35", "Jogs an arm while Home is still moving")
def s26(x):
    x.connect()
    orig = x.c.left.arm.set_servo_angle
    x.c.left.arm.set_servo_angle = lambda *a, **k: (time.sleep(0.5), orig(*a, **k))[1]
    x.c.home_all(); pump(0.1)
    m = x.mark(); x.c.jog('Z', +1); pump(0.1)
    x.expect(not x.log_since(m, cmd="move"), "Jog accepted while the arms were homing")
    x.wait_idle(4)


@scenario("Maya (undergrad)", "13:40", "Tries to jog during a print")
def s27(x):
    x.connect(); x.quick_cylinder(1.0, 1.5); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3)
    n = len(MSGS); x.c.jog('X', +1); pump(0.1)
    x.expect(x.said(n, "jog"), "Jog during print not refused")
    x.c.stop_requested = True; x.wait_idle(6)


@scenario("Noor (PhD)", "14:00", "Sets joint homes for both arms and presses Home")
def s28(x):
    x.connect()
    d = home_config.load()
    d['left']['joints'] = [5, -40, -50, 0, 85, 0]; d['right']['joints'] = [-5, -40, -50, 0, 85, 0]
    x.c.save_home(d); x.c.home_all(); x.wait_idle(3)
    x.expect(x.c.left.get_joints() == [5, -40, -50, 0, 85, 0] and x.c.right.get_joints()[0] == -5,
             "Arms did not reach their custom joint homes")


@scenario("Noor (PhD)", "14:05", "Types J3 = 45° (outside the xArm 850 limit of 11°)")
def s29(x):
    from cell_studio.views.home_dialog import HomeDialog
    dlg = HomeDialog(x.c, x.w)
    dlg.eds['left'].joints[2].set(45.0)
    val = dlg.eds['left'].joints[2].get()
    x.expect(val <= 11.0, "Joint 3 accepts 45° although the xArm 850 limit is 11°; the error only appears when the arm refuses")
    dlg.deleteLater()


@scenario("Noor (PhD)", "14:10", "home_positions.json was corrupted by a bad edit")
def s30(x):
    x.close()
    with open(HOME_TMP, "w") as f:
        f.write("{ not json")
    MSGS.clear()
    w = MainWindow(); pump(0.2)
    x.w, x.c = w, w.c
    x.expect(any("home" in (m[1] + m[2]).lower() for m in MSGS),
             "Corrupt home file silently replaced by defaults; Home would drive to a pose nobody chose")


@scenario("Omar (visiting engineer)", "14:20", "Tests 'Move there now' in the Home dialog, then needs the E-stop")
def s31(x):
    x.connect()
    state = {}

    def probe():
        state['modal'] = QApplication.activeModalWidget() is not None
        for wdg in QApplication.topLevelWidgets():
            if isinstance(wdg, QDialog) and wdg.isVisible():
                wdg.reject()
    QTimer.singleShot(100, probe)
    x.w.open_home_dialog(); pump(0.3)
    x.expect(not state.get('modal'), "Home positions dialog is modal: the main window E-stop cannot be pressed while testing a move")


@scenario("Omar (visiting engineer)", "14:25", "A warning pops up during a print")
def s32(x):
    x.connect(); x.quick_cylinder(1.0, 1.5); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3)
    n = len(MSGS)
    x.c.jog('X', 1); pump(0.2)
    modal = [m for m in MSGS[n:] if m[3]]
    x.expect(not modal, "Pop-ups raised during a print are modal and block the E-stop until dismissed")
    x.c.stop_requested = True; x.wait_idle(6)


@scenario("Priya (MSc)", "15:00", "'Use current position' with the left arm unplugged")
def s33(x):
    x.connect(left=False)
    from cell_studio.views.home_dialog import HomeDialog
    dlg = HomeDialog(x.c, x.w); n = len(MSGS)
    dlg.eds['left'].capture(); pump()
    x.expect(x.said(n, "connect"), "No message when capturing from a disconnected arm")
    dlg.deleteLater()


@scenario("Priya (MSc)", "15:05", "Turns custom home off to use the xArm factory home")
def s34(x):
    x.connect()
    d = home_config.load(); d['left']['enabled'] = False; d['right']['enabled'] = False
    x.c.save_home(d); m = x.mark()
    x.c.home_all(); x.wait_idle(3)
    x.expect(len(x.log_since(m, cmd="factory_home")) == 2, "Factory home not used when custom home is off")


@scenario("Quinn (new starter)", "15:30", "Loads a colleague's preset that has live offsets saved in it")
def s35(x):
    import tempfile
    x.connect(); x.quick_cylinder(0.3)
    x.c.offset_vars['right_Z'].set(3.0)
    path = tempfile.mktemp(suffix=".json"); x.c.save_config_to(path)
    x.c.offset_vars['right_Z'].set(0.0); x.c.load_config_from(path)
    n = len(MSGS); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); pump(0.3)
    x.expect(x.said(n, "offset"), "Print starts with a +3 mm Z offset inherited from the preset and no mention of it")
    x.c.stop_requested = True; x.wait_idle(6)


@scenario("Quinn (new starter)", "15:35", "Loads a hand-edited preset with \"abc\" as the radius")
def s36(x):
    import tempfile
    path = tempfile.mktemp(suffix=".json")
    with open(path, "w") as f:
        json.dump({"radius": "abc", "pitch": 0.5}, f)
    try:
        x.c.load_config_from(path)
    except Exception:
        pass
    r = x.c.param_vars['radius'].get()
    x.expect(isinstance(r, float), f"Radius became {r!r}: the next print would crash mid-approach")


@scenario("Quinn (new starter)", "15:40", "Picks home_positions.json by mistake in Load config")
def s37(x):
    import tempfile
    path = tempfile.mktemp(suffix=".json")
    home_config.save.__wrapped__ if hasattr(home_config.save, "__wrapped__") else None
    with open(path, "w") as f:
        json.dump(home_config.DEFAULTS, f)
    n = len(MSGS)
    try:
        x.c.load_config_from(path)
    except Exception as e:
        MSGS.append(("critical", "Load error", str(e), True))
    x.expect(x.said(n, "no cylinder") or x.said(n, "not a"), "Loading the wrong file silently does nothing")


@scenario("Rosa (PhD)", "16:00", "Edits the radius mid-print expecting the next layer to widen")
def s38(x):
    x.connect(); x.quick_cylinder(1.0, 1.5); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3)
    from cell_studio.widgets import NumberField
    f = [f for f in x.w.cylinder.findChildren(NumberField) if f.var is x.c.param_vars['radius']][0]
    x.expect(not f.isEnabled(), "Radius stays editable mid-print but the change is silently ignored")
    x.c.stop_requested = True; x.wait_idle(6)


@scenario("Rosa (PhD)", "16:05", "Types a new pitch and clicks Start without pressing Enter")
def s39(x):
    from cell_studio.widgets import NumberField
    x.w.go(1); pump()
    f = [f for f in x.w.cylinder.findChildren(NumberField) if f.var is x.c.param_vars['pitch']][0]
    f.spin.setFocus(); f.spin.selectAll(); QTest.keyClicks(f.spin, "0.3")
    QTest.mouseClick(x.w.cylinder.start_btn, Qt.LeftButton); pump()
    x.expect(abs(x.c.param_vars['pitch'].get() - 0.3) < 1e-9, "Typed value lost when clicking Start without Enter")


@scenario("Sam (undergrad)", "16:30", "Scrolls the parameter list with the mouse over a field")
def s40(x):
    from cell_studio.widgets import NumberField
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import QPointF, QPoint
    f = [f for f in x.w.cylinder.findChildren(NumberField) if f.var is x.c.param_vars['radius']][0]
    before = x.c.param_vars['radius'].get()
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                     Qt.NoScrollPhase, False)
    APP.sendEvent(f.spin, ev); pump()
    x.expect(x.c.param_vars['radius'].get() == before, "Scrolling over a field changed the radius")


@scenario("Sam (undergrad)", "16:35", "Clicks nodes off the preview to change the printed shape")
def s41(x):
    x.w.go(1); pump()
    txt = x.w.cylinder.findChildren(type(x.w.cylinder.sim_btn))
    hint_ok = "preview only" in x.w.cylinder.view.hint_text().lower() if hasattr(x.w.cylinder.view, "hint_text") else False
    x.expect(hint_ok, "Node editor looks like it edits the toolpath, but the print ignores the nodes (preview only)")


@scenario("Tom (industry visitor)", "17:00", "Presses Print on Model print without slicing")
def s42(x):
    n = len(MSGS); x.w.model.start_print(); pump()
    x.expect(x.said(n, "slice"), "No prompt to slice first")


@scenario("Tom (industry visitor)", "17:05", "Slices, changes layer height, prints without re-slicing")
def s43(x):
    x.connect(); m = sliced(x)
    m.v_layer_height.set(0.3); pump()
    n = len(MSGS); ANSWERS.extend([False])
    m.start_print(); pump(0.2)
    x.expect(x.said(n, "slice") or x.said(n, "changed"), "Prints the old plan after settings changed, no warning")
    m.stop_print(); x.wait_for(lambda: not m.printing, 5)


@scenario("Uma (PhD)", "17:30", "Model print with the turntable unticked in Connections")
def s44(x):
    x.connect(tt=False); m = sliced(x)
    n, mk = len(MSGS), x.mark(); ANSWERS.extend([True, True])
    m.start_print(); pump(0.5)
    x.expect(not x.log_since(mk, cmd="move"), "Turntable-coordinated plan printed with no turntable: part comes out wrong")
    m.stop_print(); x.wait_for(lambda: not m.printing, 5)


@scenario("Uma (PhD)", "17:35", "Model print with the extruder unticked")
def s45(x):
    x.connect(ext=False); m = sliced(x)
    mk = x.mark(); ANSWERS.extend([True, True])
    m.start_print(); pump(0.5)
    x.expect(not x.log_since(mk, cmd="move"), "Model print runs with no extruder connected (prints air)")
    m.stop_print(); x.wait_for(lambda: not m.printing, 5)


@scenario("Victor (PhD)", "18:00", "Sets Arms = 2 for a model print to halve the time")
def s46(x):
    x.connect(); m = sliced(x)
    m.v_num_arms.set(2); m.do_plan()
    mk, n = x.mark(), len(MSGS); ANSWERS.extend([True, True])
    m.start_print(); pump(1.0); m.stop_print(); x.wait_for(lambda: not m.printing, 5)
    left_moves = x.log_since(mk, dev="left", cmd="move")
    right_moves = x.log_since(mk, dev="right", cmd="move")
    refused = not right_moves and x.said(n, "one arm")
    x.expect(left_moves or refused,
             "Arms = 2 plans for two arms but only the first arm is ever driven, without saying so")


@scenario("Victor (PhD)", "18:05", "Starts a model print while a cylinder is running")
def s47(x):
    x.connect(); x.quick_cylinder(1.5, 1.5); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_for(lambda: x.c.turntable.velocity > 0, 3)
    m = sliced(x); n = len(MSGS)
    m.start_print(); pump(0.2)
    x.expect(x.said(n, "running") or x.said(n, "busy") or x.said(n, "finish"),
             "Print button silently does nothing while the cylinder runs")
    x.c.stop_requested = True; x.wait_idle(6)


@scenario("Wil (owner)", "18:30", "Starts a cylinder while a model print is running")
def s48(x):
    x.connect(); m = sliced(x); ANSWERS.extend([True] * 3)
    m.start_print(); pump(0.3)
    n = len(MSGS); x.c.start_cylinder(); pump(0.2)
    x.expect(x.said(n, "running") or x.said(n, "busy") or x.said(n, "finish"),
             "Start cylinder silently does nothing while a model print runs")
    m.stop_print(); x.wait_for(lambda: not m.printing, 5)


@scenario("Xin (MSc)", "19:00", "Primes the LEFT nozzle, then prints with a higher left extrusion factor")
def s49(x):
    x.connect(); x.quick_cylinder(0.3)
    x.c.param_vars['extrusion_factor_left'].set(1.2); x.c.calculate_extrusion_lengths()
    m = x.mark(); x.c.prime_extruder('left'); pump()
    ANSWERS.extend([True] * 3); x.c.start_cylinder(); x.wait_idle(6)
    prime_tool = x.log_since(m, cmd="extrude")[0][3][0]
    sync = x.log_since(m, cmd="extrude_sync")
    # extrude_sync(l0 -> tool 0 / E axis, l1 -> tool 1 / X axis); left has the larger factor,
    # so whichever argument is larger carries the left filament (holds for chunks too)
    l0, l1 = sync[0][3][0], sync[0][3][2]
    left_to_tool = 0 if l0 > l1 else 1
    x.expect(prime_tool == left_to_tool,
             f"Left means tool {prime_tool} when priming but tool {left_to_tool} when printing: "
             "the left extrusion factor is applied to the right-hand motor")


@scenario("Yusuf (on a laptop)", "19:30", "Runs the panel on a 1366 × 768 laptop screen")
def s50(x):
    x.w.resize(1366, 740)
    ok = True
    for i in range(3):
        x.w.go(i); pump(0.1)
        if x.w.width() > 1366 or x.w.minimumSizeHint().width() > 1366 or x.w.minimumWidth() > 1366:
            ok = False
    x.w.go(1); pump(0.1)
    cyl = x.w.cylinder
    ok = ok and cyl.start_btn.visibleRegion().boundingRect().width() >= cyl.start_btn.width() - 2
    x.expect(ok, "Layout does not fit a 1366 px wide laptop (controls clipped)")



# ---------------------------------------------------------------- generators
import tempfile as _tempfile
from cell_studio import gen_library as _lib

GEN_TMP = _tempfile.mkdtemp(prefix="cellgen_tests_")
EXAMPLES = os.path.join(ROOT, "generators", "examples")


def gen_file(name, src):
    path = os.path.join(GEN_TMP, name)
    with open(path, "w") as f:
        f.write(src)
    return path


def gen(x, path, params=None, timeout=40):
    g = x.w.generators
    x.w.go(3)
    g.load_meta(_lib.read_meta(path))
    for k, v in (params or {}).items():
        g._param_vars[k][0].set(v)
    g.generate()
    t = time.time()
    while (g.proc is not None or g.planning) and time.time() - t < timeout:
        pump(0.03)
    pump(0.05)
    return g


def report(x):
    return x.w.generators.stats_text.toPlainText().lower()


@scenario("Zara (first FullControl user)", "09:00", "Generates the FullControl ripple vase and dry-runs it")
def s51(x):
    g = gen(x, os.path.join(EXAMPLES, "ripple_vase_fullcontrol.py"), {"height": 3.0})
    x.expect(g.program is not None, f"No plan after Generate: {g.v_gen_status.get()} / {report(x)[:200]}")
    if g.program is None:
        return
    g.start_dry_run()
    x.expect(x.wait_for(lambda: not g.printing, 60), "Dry run never finished")
    x.expect("dry run complete" in g.v_status.get().lower(), f"Dry run status: {g.v_status.get()}")


@scenario("George (FullControl author)", "09:20", "Brings his own FC script that saves its G-code to a file")
def s52(x):
    path = gen_file("george_ring.py", (
        "import fullcontrol as fc\nRADIUS = 20.0\nLAYERS = 3\n"
        "steps = [fc.ExtrusionGeometry(width=0.5, height=0.2)]\n"
        "for L in range(LAYERS):\n    steps += fc.circleXY(fc.Point(x=60, y=60, z=0.2*(L+1)), RADIUS, 0, 64)\n"
        "gcode = fc.transform(steps, 'gcode')\nopen('george_ring.gcode', 'w').write(gcode)\n"))
    g = gen(x, path, {"RADIUS": 30.0})
    x.expect(g.tp is not None, f"Plain FC script not accepted: {g.v_gen_status.get()}")
    if g.tp:
        x.expect(abs(g.tp_stats["size"][0] - 60.5) < 2, f"RADIUS parameter not applied (size {g.tp_stats['size'][0]:.1f} mm)")
    out = os.path.join(GEN_TMP, "george_ring.gcode")
    x.expect(os.path.exists(out) and os.path.getsize(out) > 100,
             "The script's own G-code file was emptied when run from the panel")


@scenario("Ibrahim (MSc)", "09:40", "His generator crashes with ZeroDivisionError")
def s53(x):
    path = gen_file("crashy.py", "def helper(n):\n    return 10 / n\n\ndef generate(n=0):\n    return helper(n)\n")
    g = gen(x, path)
    r = report(x)
    x.expect(g.tp is None and g.program is None, "A crashed generator left a toolpath to print")
    x.expect("zerodivisionerror" in r and "line 2" in r, "Report does not show the error and the line in his file")
    x.expect(not g.print_btn.isEnabled(), "Print enabled after a failed generation")


@scenario("Ibrahim (MSc)", "09:45", "His loop never ends (forgot to increment)")
def s54(x):
    path = gen_file("forever.py", "def generate(n=3):\n    i = 0\n    while i < n:\n        pass\n")
    g = x.w.generators
    g.v_timeout.set(5)
    t0 = time.time()
    g = gen(x, path, timeout=15)
    x.expect(g.proc is None, "Generator still running after its time limit")
    x.expect(time.time() - t0 < 12, "Time limit not enforced")
    x.expect("time limit" in (g.v_gen_status.get() + report(x)).lower(), "No message that it hit the time limit")


@scenario("Ibrahim (MSc)", "09:50", "His script asks for input() from the keyboard")
def s55(x):
    path = gen_file("asks.py", "def generate():\n    r = float(input('radius? '))\n    return [(r, 0, 0.2), (0, r, 0.2)]\n")
    t0 = time.time()
    g = gen(x, path, timeout=20)
    x.expect(time.time() - t0 < 10, "input() made the panel wait for keyboard input")
    x.expect("eof" in report(x), "No clear error for input()")


@scenario("Lena (designer)", "10:10", "Makes a 320 mm wide part (bigger than the 300 mm disc)")
def s56(x):
    g = gen(x, os.path.join(ROOT, "generators", "TEMPLATE.py"), {"radius": 160.0, "height": 1.0})
    x.expect(g.program is None and g.tp_errors, "Part beyond the disc was planned")
    x.expect(not g.print_btn.isEnabled() and not g.plan_btn.isEnabled(), "Plan/Print enabled for a part off the disc")
    x.expect("disc radius" in report(x), "No explanation that it reaches past the disc")


@scenario("Lena (designer)", "10:15", "Writes coordinates in metres (radius 0.03)")
def s57(x):
    path = gen_file("metres.py", "import math\ndef generate(r=0.03):\n    return [[(r*math.cos(a/50), r*math.sin(a/50), 0.0002+a*1e-6) for a in range(400)]]\n")
    gen(x, path)
    x.expect("metres" in report(x), "No hint that the units look like metres")


@scenario("Lena (designer)", "10:20", "First layer at z = 0 (nozzle on the disc)")
def s58(x):
    path = gen_file("zzero.py", "import math\ndef generate(r=20.0):\n    return [[(r*math.cos(a/30), r*math.sin(a/30), 0.0) for a in range(200)]]\n")
    gen(x, path)
    x.expect("almost touch" in report(x), "No warning that z = 0 would put the nozzle on the disc")


@scenario("Omar (visiting engineer)", "10:40", "A colleague's file has a syntax error")
def s59(x):
    path = gen_file("broken.py", "def generate(:\n    pass\n")
    m = _lib.read_meta(path)
    x.expect(m["error"] and "line 1" in m["error"].lower(), "Syntax error not reported with its line")
    n = len(MSGS)
    g = x.w.generators
    g.load_meta(m)
    g.generate(); pump(0.2)
    x.expect(g.proc is None and x.said(n, "syntax"), "Generate ran a file with a syntax error")


@scenario("Priya (MSc)", "11:00", "Loads G-code exported from another slicer (relative E, arcs)")
def s60(x):
    path = gen_file("ring.gcode", "M83\nG1 Z0.2 F600\nG1 X40 Y0 F3000\nG3 X40 Y0 I-40 J0 E8.4 F1200\n"
                                  "G1 Z0.4\nG3 X40 Y0 I-40 J0 E8.4\n")
    g = gen(x, path)
    x.expect(g.program is not None, f"G-code not planned: {g.v_gen_status.get()}")
    if g.tp:
        w0, w1 = g.tp_stats["width"]
        x.expect(0.3 < w0 and w1 < 0.8, f"Widths from E look wrong: {w0:.2f}-{w1:.2f} mm")


@scenario("Wil (owner)", "11:30", "Plans the two-arm interweave, then presses Print")
def s61(x):
    x.connect()
    g = x.w.generators
    g.v_num_arms.set(2)
    g = gen(x, os.path.join(EXAMPLES, "interweave_two_arms.py"), {"height": 2.0})
    x.expect(g.program is not None and g.program.config.num_arms == 2, "Two-arm plan not made")
    if g.program is not None:
        both = sum(1 for s in g.program.steps if s.arms[0] is not None and s.arms[1] is not None)
        x.expect(both > 100, "Arms not planned side by side")
    n, m = len(MSGS), x.mark()
    g.start_print(); pump(0.3)
    x.expect(not x.log_since(m, cmd="move") and x.said(n, "one arm"), "Two-arm plan streamed to one arm")


@scenario("Chloe (PhD student)", "12:00", "Changes a parameter after planning and presses Print")
def s62(x):
    x.connect()
    g = gen(x, os.path.join(ROOT, "generators", "TEMPLATE.py"), {"height": 0.6})
    g._param_vars["radius"][0].set(45.0)
    n, m = len(MSGS), x.mark()
    ANSWERS.extend([False])
    g.start_print(); pump(0.3)
    x.expect(not x.log_since(m, cmd="move"), "Printed the old toolpath after a parameter changed")
    x.expect(x.said(n, "changed"), "No prompt that the parameters changed since generating")


@scenario("Victor (PhD)", "12:20", "Starts a generator print while a model print runs")
def s63(x):
    x.connect()
    m_page = sliced(x)
    ANSWERS.extend([True])
    m_page.start_print(); pump(0.3)
    g = gen(x, os.path.join(ROOT, "generators", "TEMPLATE.py"), {"height": 0.4})
    n, mk = len(MSGS), x.mark()
    g.start_print(); pump(0.3)
    x.expect(not g.printing and x.said(n, "running"), "Two prints streamed at once")
    n2 = len(MSGS)
    x.c.start_cylinder(); pump(0.2)
    x.expect(not x.c.printing and x.said(n2, "running"), "Cylinder started during a model print")
    m_page.stop_print(); x.wait_for(lambda: not m_page.printing, 5)


@scenario("Sam (undergrad)", "13:00", "Returns 2D points (forgot z)")
def s64(x):
    path = gen_file("flat.py", "def generate():\n    return [[(0, 0), (10, 0), (10, 10)]]\n")
    gen(x, path)
    x.expect("x, y and z" in report(x), "No clear message that points need x, y and z")


@scenario("Sam (undergrad)", "13:10", "Cancels a slow generator")
def s65(x):
    path = gen_file("slow.py", "import time\ndef generate():\n    time.sleep(30)\n    return [[(0,0,0.2),(10,0,0.2)]]\n")
    g = x.w.generators
    x.w.go(3)
    g.load_meta(_lib.read_meta(path))
    g.generate(); pump(0.5)
    g.cancel()
    x.expect(x.wait_for(lambda: g.proc is None, 5), "Cancel did not stop the generator")
    x.expect("cancel" in g.v_gen_status.get().lower(), "No 'Cancelled' status")


@scenario("Yusuf (on a laptop)", "13:30", "Uses the Generators tab at 1280 px")
def s66(x):
    from PySide6.QtWidgets import QPushButton, QWidget
    x.w.resize(1280, 720); x.w.go(3)
    gen(x, os.path.join(EXAMPLES, "twisted_polygon.py"), {"height": 1.0})
    pump(0.2)
    bad = []
    root = x.w.generators
    for wdg in root.findChildren(QWidget):
        if not wdg.isVisibleTo(root):
            continue
        sibs = [s for s in wdg.children() if isinstance(s, QWidget) and s.isVisibleTo(root) and not s.isWindow()]
        for i, a in enumerate(sibs):
            for b in sibs[i + 1:]:
                r = a.geometry().intersected(b.geometry())
                if r.width() > 2 and r.height() > 2:
                    bad.append(f"{type(a).__name__} x {type(b).__name__}")
        if isinstance(wdg, QPushButton) and wdg.width() + 1 < wdg.sizeHint().width():
            bad.append(f"squeezed '{wdg.text()}'")
    x.expect(not bad, "Overlapping or clipped widgets: " + ", ".join(sorted(set(bad))[:5]))



# ---------------------------------------------------------------- macros
import math as _math


def calibrate(x, timeout=150):
    m = x.w.macros
    x.w.go(4)
    m.v_print_speed.set(1.2)
    ANSWERS.extend([True])
    m.run_calibration()
    x.wait_for(lambda: x.c.printing, 3)
    t = time.time()
    while x.c.printing and time.time() - t < timeout:
        pump(0.1)
    pump(0.2)
    return m


def run_spirals(x, fn, timeout=120):
    m = x.w.macros
    ANSWERS.extend([True])
    fn()
    t = time.time()
    while (m.proc is not None or x.c.printing) and time.time() - t < timeout:
        pump(0.1)
    pump(0.2)
    return m


def expected_merge(x, err, s_radius=40.0, angle=135.0, z=0.6):
    from cell_studio.geometry import CellGeometry
    g = CellGeometry(x.c)
    a = _math.radians(angle)
    p = g.to_arm("left", (s_radius * _math.cos(a), s_radius * _math.sin(a), z))
    true = tuple(g.centre("left")[i] + err[i] for i in range(3))
    w = g.to_world("left", p, centre=true)
    return s_radius - _math.hypot(w[0], w[1]), -err[2]


@scenario("Wil (owner)", "15:00", "Merge calibration finds a hidden left-arm error, applies it, re-checks")
def s67(x):
    err = (0.7, -0.4, -0.15)
    sim.FAULTS["frame_error"] = {"left": err}
    x.connect()
    eo, ez = expected_merge(x, err)
    m = calibrate(x)
    r = m.result
    x.expect(r is not None, f"Calibration failed: {m.res_lbl.text()}")
    if r is None:
        return
    x.expect(abs(r.merge_offset_mm - eo) < 0.06, f"Merge offset {r.merge_offset_mm:+.3f} mm, expected {eo:+.3f}")
    x.expect(abs(r.height_diff_mm - ez) < 0.03, f"Height difference {r.height_diff_mm:+.3f} mm, expected {ez:+.3f}")
    ANSWERS.extend([True])
    m.apply_result()
    n = len(MSGS)
    m = calibrate(x)
    x.expect(m.result is None and "already sees material" in m.res_lbl.text(),
             "Re-ran on top of the old rings without noticing the disc was not cleared")
    sim.WORLD.reset()                         # operator clears the disc
    m = calibrate(x)
    r2 = m.result
    x.expect(r2 is not None and abs(r2.merge_offset_mm) < 0.05 and abs(r2.height_diff_mm) < 0.03,
             f"Second calibration after applying is not ~0: offset {r2.merge_offset_mm if r2 else None}, "
             f"height {r2.height_diff_mm if r2 else None}")


@scenario("Liam (technician)", "15:20", "Extruder wiring is the other way round from the panel's setting")
def s68(x):
    x.connect()
    sim.WORLD.tool_for_arm = {"right": 1, "left": 0}
    m = calibrate(x)
    x.expect(m.result is None and "other way round" in m.res_lbl.text(),
             f"No clear hint about the tool wiring: {m.res_lbl.text()[:120]}")


@scenario("Omar (visiting engineer)", "15:40", "Camera mounted on the other side (image mirrored)")
def s69(x):
    sim.FAULTS["frame_error"] = {"left": (0.5, 0, 0)}
    x.connect()
    x.w.macros.v_outward_right.set(False)
    eo, _ = expected_merge(x, (0.5, 0, 0))
    m = calibrate(x)
    r = m.result
    x.expect(r is not None and abs(r.merge_offset_mm - eo) < 0.06,
             f"Wrong result with the camera setting the wrong way round: {r.merge_offset_mm if r else m.res_lbl.text()[:80]} "
             f"(expected {eo:+.3f})")
    x.expect(r is not None and any("larger radius to the right" in n_ for n_ in r.notes),
             "No note that the camera orientation setting is wrong")


@scenario("Priya (MSc)", "16:00", "Runs calibration with the simulated camera but nothing connected")
def s70(x):
    m = x.w.macros
    x.w.go(4)
    n = len(MSGS)
    m.run_calibration(); pump(0.2)
    x.expect(not x.c.printing and x.said(n, "not connected"), "Calibration started without the arms")


@scenario("Hassan (research fellow)", "16:20", "Prints the confirmation spirals after calibrating")
def s71(x):
    x.connect()
    x.c.turntable_speed_var.set(2.0)
    mk = x.mark()
    run_spirals(x, x.w.macros.confirm_spirals)
    ext = x.log_since(mk, cmd="extrude_sync")
    both = [e for e in ext if e[3][0] > 0 and e[3][2] > 0]
    x.expect(len(both) > 20, f"Both tools should extrude together ({len(both)} of {len(ext)} chunks)")
    moves = {s_: len(x.log_since(mk, dev=s_, cmd="move")) for s_ in ("left", "right")}
    x.expect(min(moves.values()) > 30, f"Both arms should follow their spirals: {moves}")
    x.expect(x.c.turntable.velocity == 0 and x.c.job_state.get() == "idle", "Did not finish cleanly")
    x.expect(x.said(0, "no merge calibration"), "No warning that no calibration was applied")


@scenario("Kate (MSc)", "16:40", "Pauses the dual vase, then hits EMERGENCY STOP")
def s72(x):
    x.connect()
    m = x.w.macros
    x.c.turntable_speed_var.set(1.5)
    m.v_dv["height"].set(6.0)
    ANSWERS.extend([True])
    m.start_dual()
    x.wait_for(lambda: x.c.printing and x.c.turntable.velocity != 0, 30)
    pump(0.8)
    x.c.toggle_pause(); pump(0.6)
    x.expect(x.c.turntable.velocity == 0, "Pause did not stop the disc")
    x.expect(x.c.extruder.busy_until - time.time() < 0.5, "Filament still queued long after pausing")
    x.c.toggle_pause(); pump(0.5)
    mk = x.mark()
    ANSWERS.append(True); x.w._estop(); x.wait_idle(5)
    after = [e for e in x.log_since(mk) if e[2] in ("move", "extrude_sync")]
    x.expect(not after, f"{len(after)} commands sent after the E-stop")


@scenario("Noor (PhD)", "17:00", "Starts the dual vase from different angles than she calibrated at")
def s73(x):
    x.connect()
    m = calibrate(x)
    if m.result is None:
        x.expect(False, "calibration failed")
        return
    ANSWERS.extend([True])
    m.apply_result()
    m.v_right_angle.set(0.0)
    m.v_left_angle.set(180.0)
    n = len(MSGS)
    ANSWERS.extend([False])                 # read the warning, then cancel
    m.start_dual()
    t = time.time()
    while m.proc is not None and time.time() - t < 30:
        pump(0.1)
    pump(0.2)
    x.expect(x.said(n, "from where it was calibrated"), "No warning about printing away from the calibrated angles")
    x.expect(not x.c.printing, "Started although she cancelled")


@scenario("Erin (MSc)", "17:20", "Speeds the disc up mid dual vase: the flow must follow")
def s74(x):
    x.connect()
    m = x.w.macros
    x.c.turntable_speed_var.set(0.8)
    m.v_dv["height"].set(12.0)
    ANSWERS.extend([True])
    mk = x.mark()
    m.start_dual()
    x.wait_for(lambda: len(x.log_since(mk, cmd="extrude_sync")) > 3, 40)
    slow = [e[3][1] for e in x.log_since(mk, cmd="extrude_sync")][-1]
    mk2 = x.mark()
    x.c.turntable_speed_var.set(1.6)
    x.wait_for(lambda: len(x.log_since(mk2, cmd="extrude_sync")) > 3, 20)
    fast = [e[3][1] for e in x.log_since(mk2, cmd="extrude_sync")][-1]
    x.expect(1.6 < fast / slow < 2.4, f"Feed went {slow:.3f} → {fast:.3f} mm/s when the disc doubled its speed")
    x.c.stop_print(); x.wait_idle(10)


@scenario("George (slicer author)", "17:40", "Slices a model with the stand-in slicer from the Macros page")
def s75(x):
    m = x.w.macros
    x.w.go(4)
    n = len(MSGS)
    m.v_slice_model.set("")
    m.slice_in_generators(); pump(0.1)
    x.expect(x.said(n, "model"), "No prompt to choose a model")
    m.v_slice_model.set(os.path.join(ROOT, "scripts", "cube40.3mf"))
    m.slice_in_generators()
    g = x.w.generators
    t = time.time()
    while (g.proc is not None or g.planning) and time.time() - t < 60:
        pump(0.1)
    x.expect(g.program is not None and g.program.config.num_arms == 1,
             f"Stand-in slicer not planned for the right arm: {g.v_gen_status.get()} {report(x)[:150]}")


@scenario("George (slicer author)", "17:50", "His plain FullControl slicer script reads MODEL_PATH")
def s76(x):
    path = gen_file("george_slicer.py", (
        "import fullcontrol as fc\nimport trimesh\nMODEL_PATH = 'part.stl'\nLAYER = 0.6\n"
        "mesh = trimesh.load(MODEL_PATH, force='mesh')\nzmax = float(mesh.bounds[1][2])\n"
        "steps = []\nfor i in range(3):\n    steps += fc.circleXY(fc.Point(x=0, y=0, z=LAYER*(i+1)), zmax, 0, 48)\n"
        "fc.transform(steps, 'plot')\n"))
    g = x.w.generators
    x.w.go(3)
    g.load_meta(_lib.read_meta(path))
    x.expect(g.model_row.isVisible(), "No model row for a script with a MODEL_PATH constant")
    g.set_model(os.path.join(ROOT, "scripts", "cube40.3mf"))
    g.generate()
    t = time.time()
    while (g.proc is not None or g.planning) and time.time() - t < 60:
        pump(0.1)
    x.expect(g.tp is not None and abs(g.tp_stats["size"][0] - 2 * 40.0) < 1.5,
             f"MODEL_PATH not replaced by the chosen model: {g.v_gen_status.get()} {report(x)[:150]}")


@scenario("Wil (owner)", "18:00", "Sets the new vertical nozzle in Tool and nozzle, runs a cylinder")
def s77(x):
    x.connect()
    x.c.tool = dict(x.c.tool, pitch=0.0, roll=180.0, yaw=20.0)
    x.quick_cylinder(0.3)
    mk = x.mark(); ANSWERS.extend([True] * 3)
    x.c.start_cylinder(); x.wait_idle(8)
    moves = x.log_since(mk, dev="right", cmd="move")
    pitches = {round(e[3][0][4], 1) for e in moves}
    x.expect(pitches == {0.0}, f"Cylinder commanded pitch {sorted(pitches)} with a vertical nozzle set")


@scenario("Yusuf (on a laptop)", "18:20", "Uses the Macros tab at 1280 px")
def s78(x):
    from PySide6.QtWidgets import QPushButton, QWidget
    x.w.resize(1280, 720); x.w.go(4); pump(0.2)
    bad = []
    root = x.w.macros
    for wdg in root.findChildren(QWidget):
        if not wdg.isVisibleTo(root):
            continue
        sibs = [s_ for s_ in wdg.children() if isinstance(s_, QWidget) and s_.isVisibleTo(root) and not s_.isWindow()]
        for i, a in enumerate(sibs):
            for b in sibs[i + 1:]:
                r = a.geometry().intersected(b.geometry())
                if r.width() > 2 and r.height() > 2:
                    bad.append(f"{type(a).__name__} x {type(b).__name__}")
        if isinstance(wdg, QPushButton) and wdg.width() + 1 < wdg.sizeHint().width():
            bad.append(f"squeezed '{wdg.text()}'")
    x.expect(not bad, "Overlapping or clipped widgets: " + ", ".join(sorted(set(bad))[:5]))



@scenario("Wil (owner)", "18:30", "Imports a thin-walled Tinkercad part with holes (several outlines per layer)")
def s79(x):
    import trimesh
    ring = trimesh.creation.annulus(r_min=28.0, r_max=30.2, height=20.0)
    ring.apply_translation((0, 0, 10.0))
    path = os.path.join(GEN_TMP, "thin_ring.obj")
    ring.export(path)
    m = x.w.model
    x.w.go(2)
    m.load_model(path)
    m.do_slice()
    statuses = set()
    t = time.time()
    while getattr(m, "_slicing", False) and time.time() - t < 120:
        statuses.add(m.v_status.get())
        pump(0.02)
    rep = m.stats_text.toPlainText()
    walls = sum(1 for L in (m.slice_result.layers if m.slice_result else []) for p in L.paths if p.kind == "WALL_OUTER")
    x.expect(walls >= 2 * len(m.slice_result.layers) if m.slice_result else False,
             f"Part with an inner and outer outline sliced to {walls} outer walls: {rep[:160]}")
    x.expect(m.program is not None, f"Not planned: {m.v_status.get()}")
    x.expect(any("Slicing…" in st for st in statuses), "No progress shown while slicing (panel looks frozen)")



@scenario("Wil (owner)", "18:40", "Tinkercad part made of overlapping shapes that were never merged")
def s80(x):
    import trimesh
    box = trimesh.creation.box((80, 60, 38))
    cross = [trimesh.creation.box((80, 8, 38)), trimesh.creation.box((8, 60, 38))]
    m_ = trimesh.util.concatenate([box] + cross)
    m_.apply_translation((0, 0, 19))
    path = os.path.join(GEN_TMP, "overlapping.obj")
    m_.export(path)
    m = x.w.model
    x.w.go(2)
    m.v_layer_height.set(0.6)
    m.load_model(path)
    m.do_slice()
    t = time.time()
    while getattr(m, "_slicing", False) and time.time() - t < 120:
        pump(0.05)
    res = m.slice_result
    if res is None:
        x.expect(False, f"Not sliced: {m.v_status.get()}")
        return
    outers = [len([p for p in L.paths if p.kind == "WALL_OUTER"]) for L in res.layers]
    x.expect(max(outers) == 1, f"The overlap between the shapes was cut out: {max(outers)} separate outlines per layer "
                               "instead of 1")


# ---------------------------------------------------------------- runner
def run(selected=None):
    rows = []
    for sid, who, when, job, fn in SCENARIOS:
        if selected and sid not in selected:
            continue
        x = Ctx()
        status, note = "ok", ""
        try:
            r = fn(x)
            if isinstance(r, str):
                status, note = "skip", r
            elif x.flaws:
                status, note = "FLAW", " | ".join(x.flaws)
        except Exception as e:
            status, note = "ERROR", f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            x.close()
        rows.append(dict(id=sid, who=who, when=when, job=job, status=status, note=note))
        print(f"{sid:>2} {status:<5} {who:<24} {job[:58]:<58} {note[:140]}", flush=True)
    return rows


if __name__ == "__main__":
    sel = None
    if "--only" in sys.argv:
        sel = {int(v) for v in sys.argv[sys.argv.index("--only") + 1].split(",")}
    rows = run(sel)
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            json.dump(rows, f, indent=1)
    for f in (HOME_TMP, _macros.ALIGN_FILE, _tool.TOOL_FILE):
        if os.path.exists(f):
            os.remove(f)
    bad = [r for r in rows if r["status"] in ("FLAW", "ERROR")]
    print(f"\n{len(rows) - len(bad)} passed, {len(bad)} with flaws or errors")
