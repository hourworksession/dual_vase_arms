"""Tests for the polar / disc-frame conversions."""
from __future__ import annotations

import math

import numpy as np

from dual_arm_printer.geometry.polar import (
    angular_diff,
    batch_cart_to_polar,
    batch_polar_to_cart,
    cart_to_polar,
    unwrap_thetas,
)


def test_cart_polar_roundtrip():
    p = cart_to_polar(3.0, 4.0, 1.0)
    assert math.isclose(p.r, 5.0)
    x, y, z = p.to_world(0.0)
    assert math.isclose(x, 3.0, abs_tol=1e-9)
    assert math.isclose(y, 4.0, abs_tol=1e-9)
    assert math.isclose(z, 1.0)


def test_rotation_with_disc_angle():
    p = cart_to_polar(1.0, 0.0, 0.0)
    x, y, _ = p.to_world(math.pi / 2)
    assert math.isclose(x, 0.0, abs_tol=1e-9)
    assert math.isclose(y, 1.0, abs_tol=1e-9)


def test_angular_diff_wraps():
    assert math.isclose(angular_diff(math.pi - 0.1, -math.pi + 0.1), -0.2, abs_tol=1e-9)


def test_unwrap_monotonic():
    seq = [0.1, 3.0, -3.0, -2.5]
    out = unwrap_thetas(seq)
    # Differences should all be < pi after unwrap.
    diffs = [out[i + 1] - out[i] for i in range(len(out) - 1)]
    assert all(abs(d) < math.pi for d in diffs)


def test_batch_roundtrip():
    xyz = np.array([[10.0, 0.0, 0.0], [0.0, 5.0, 1.0], [-3.0, -4.0, 2.0]])
    rtz = batch_cart_to_polar(xyz)
    back = batch_polar_to_cart(rtz)
    assert np.allclose(back, xyz, atol=1e-9)
