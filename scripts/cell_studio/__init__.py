"""Cell Studio: the Qt rebuild of the Gleadall dual arm control panel.

Launch with ``python scripts/gleadell_panel_qt.py``.

Layout
------
state.py        thread safe Var model (replaces tk.DoubleVar & co) and the UI bridge
theme.py        colours and the Qt style sheet
widgets.py      reusable cards, number fields, switches, segmented controls
controller.py   all machine logic (connect, home, prepare, cylinder print, jog,
                emergency stop), ported from gleadell_panel.py without behaviour changes
home_config.py  user defined home positions per arm (Settings > Home positions)
sim_hardware.py simulated arms / turntable / extruder for rehearsal without the cell
views/          the pages: machine, cylinder, model print, plus the canvases
main_window.py  header, navigation, live panel, log console
"""
