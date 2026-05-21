# URDFs

Drop the official UFactory xArm 850 URDF here as `xarm850.urdf`. UFactory
publishes it at:

    https://github.com/xArm-Developer/xarm_ros2

(under `xarm_description/urdf/xarm850/`). Copy that file and its mesh
folder into `meshes/`, then update the `<mesh filename="…">` paths to be
relative to `meshes/`.

`adrs_turntable.urdf` is a simple URDF with one revolute joint about Z;
the link mesh can be a 600-mm-diameter cylinder of acrylic material. A
template is included below.

`hemera.urdf` describes the Hemera assembly mounted on the arm flange and
is attached as a fixed child link to the arm's last link in the cell URDF.
