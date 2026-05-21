"""Matplotlib visualisation of toolpaths and the disc.

Used by ``examples/`` and by ``scripts/simulate.py --no-gui``. PyBullet
runs the dynamics; this module renders the post-print result so the user
can sanity-check a SplitResult without spinning up GUI graphics.
"""
from __future__ import annotations

import math

import matplotlib.pyplot as plt
import numpy as np

from ..coordination.scheduler import ExecutionPlan
from ..coordination.turntable_sync import transform_segment
from ..splitting.base import SplitResult


def plot_split(split: SplitResult, disc_radius_mm: float = 300.0, save_to: str | None = None) -> None:
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.set_aspect("equal")
    ax.set_title(f"Dual-arm split — {len(split.left.segments)}L / {len(split.right.segments)}R")

    # Disc outline.
    theta = np.linspace(0, 2 * math.pi, 200)
    ax.plot(disc_radius_mm * np.cos(theta), disc_radius_mm * np.sin(theta), "k--", lw=0.8)

    for arm, color in ((split.left, "tab:blue"), (split.right, "tab:red")):
        for seg, angle in zip(arm.segments, arm.disc_angle_at_start_rad):
            if seg.is_travel:
                continue
            s, e = transform_segment(seg.start_xyz, seg.end_xyz, angle)
            ax.plot([s[0], e[0]], [s[1], e[1]], color=color, lw=0.6, alpha=0.7)

    ax.set_xlabel("X world (mm)")
    ax.set_ylabel("Y world (mm)")

    if save_to:
        fig.savefig(save_to, dpi=140, bbox_inches="tight")
    else:
        plt.show()


def plot_execution(plan: ExecutionPlan, save_to: str | None = None) -> None:
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.set_aspect("equal")
    lx = [w.xyz_mm[0] for w in plan.left_waypoints]
    ly = [w.xyz_mm[1] for w in plan.left_waypoints]
    rx = [w.xyz_mm[0] for w in plan.right_waypoints]
    ry = [w.xyz_mm[1] for w in plan.right_waypoints]
    ax.plot(lx, ly, "tab:blue", lw=0.7, label="left arm TCP")
    ax.plot(rx, ry, "tab:red", lw=0.7, label="right arm TCP")
    ax.legend()
    ax.set_xlabel("X world (mm)")
    ax.set_ylabel("Y world (mm)")
    if save_to:
        fig.savefig(save_to, dpi=140, bbox_inches="tight")
    else:
        plt.show()
