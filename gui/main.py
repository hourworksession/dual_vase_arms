#!/usr/bin/env python3
"""
Main entry point for the dual‑arm printer control GUI.
"""
import sys
import os
import tkinter as tk

# Make sure the project root is on the Python path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from gui.control_panel import ControlPanel
from config_loader import load_config


def main():
    config = load_config()
    root = tk.Tk()
    app = ControlPanel(root, config)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
