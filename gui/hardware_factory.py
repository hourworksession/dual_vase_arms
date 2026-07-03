"""
Decides whether to use real hardware or mocks.
Returns the appropriate controller classes or instances.
"""

import importlib
import sys
import logging

logger = logging.getLogger(__name__)

# List of modules that must be importable for real hardware
REQUIRED_MODULES = {
    "xarm": "xarm-python-sdk not installed",
    "automation1": "automation1 not installed",
    "requests": "requests not installed",  # always present, but keep for completeness
}

def can_use_real_hardware():
    """Check if all required modules are available."""
    for mod, reason in REQUIRED_MODULES.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            logger.warning(f"Cannot use real hardware: {reason}")
            return False
    return True


def create_arm_controller(ip, name, use_real=None):
    """Return an ArmController (real or mock)."""
    if use_real is None:
        use_real = can_use_real_hardware()

    if use_real:
        from src.arm_controller import ArmController
        return ArmController(ip, name)
    else:
        from gui.mock_hardware import MockArmController
        return MockArmController(ip, name)


def create_turntable_controller(host, axis, use_real=None):
    if use_real is None:
        use_real = can_use_real_hardware()

    if use_real:
        from src.turntable_controller import TurntableController
        return TurntableController(host=host, axis=axis)
    else:
        from gui.mock_hardware import MockTurntableController
        return MockTurntableController(host=host, axis=axis)


def create_extruder_controller(host, port=7125, use_real=None):
    if use_real is None:
        use_real = can_use_real_hardware()

    if use_real:
        from src.extruder_controller import ExtruderController
        return ExtruderController(host, port)
    else:
        from gui.mock_hardware import MockExtruderController
        return MockExtruderController(host, port)