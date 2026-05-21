from dual_arm_printer.control.safety import SafetyPolicy


def test_violation_when_tcps_close():
    s = SafetyPolicy(arm_arm_min_distance_mm=50.0)
    ok, why = s.check_step((0, 0, 100), (10, 0, 100))
    assert not ok and "close" in why


def test_violation_when_below_disc():
    s = SafetyPolicy(arm_arm_min_distance_mm=10.0, nozzle_to_disc_z_min_mm=1.0)
    ok, why = s.check_step((0, 0, 0.5), (100, 0, 100))
    assert not ok
