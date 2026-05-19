"""xArm 8 robot model using DH parameters."""
import numpy as np
from roboticstoolbox import DHRobot, RevoluteDH

class XArm8(DHRobot):
    def __init__(self, name="xArm8"):
        L = [
            RevoluteDH(d=0.267,  a=0,      alpha=np.pi/2, qlim=[-np.pi, np.pi]),
            RevoluteDH(d=0,      a=0.289,  alpha=0,       qlim=[-np.pi, np.pi]),
            RevoluteDH(d=0,      a=0.0775, alpha=-np.pi/2,qlim=[-np.pi, np.pi]),
            RevoluteDH(d=0.3425, a=0,      alpha=np.pi/2, qlim=[-np.pi, np.pi]),
            RevoluteDH(d=0,      a=0,      alpha=-np.pi/2,qlim=[-np.pi, np.pi]),
            RevoluteDH(d=0.097,  a=0,      alpha=0,       qlim=[-np.pi, np.pi]),
        ]
        super().__init__(L, name=name, manufacturer="UFactory")
        self.qz = np.zeros(6)
        self.qr = np.array([0, -0.3, 0, -2.2, 0, 2.0])
        self.addconfiguration("qz", self.qz)
        self.addconfiguration("qr", self.qr)