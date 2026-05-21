import numpy as np

class CoordinateBaseSystem:
    def __init__(self, turntable_axis=np.array([0, 0, 1]), turntable_speed=0.0):
        """
        Initialize the coordinate system.
        Args:
            turntable_axis (np.array): Axis of rotation for the turntable (default: Z-axis).
            turntable_speed (float): Angular velocity of the turntable (rad/s).
        """
        self.turntable_axis = turntable_axis / np.linalg.norm(turntable_axis)  # Normalize
        self.turntable_speed = turntable_speed
        self.current_angle = 0.0  # Current rotation angle of the turntable
        self.model_points = None  # 3D model represented as a matrix of points

    def load_model(self, points):
        """Load a 3D model as a matrix of XYZ points."""
        self.model_points = np.array(points)

    def rotate_model(self, delta_time):
        """
        Rotate the model points based on turntable speed and elapsed time.
        Args:
            delta_time (float): Time elapsed since last rotation (seconds).
        """
        if self.turntable_speed == 0:
            return
        # Update turntable angle
        self.current_angle += self.turntable_speed * delta_time
        # Apply rotation to each point
        rotation_matrix = self._get_rotation_matrix(self.current_angle)
        self.model_points = np.dot(self.model_points, rotation_matrix.T)

    def _get_rotation_matrix(self, angle):
        """
        Generate a 3D rotation matrix around turntable_axis.
        Args:
            angle (float): Rotation angle in radians.
        Returns:
            np.array: 3x3 rotation matrix.
        """
        u = self.turntable_axis
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)
        return np.array([
            [cos_a + u[0]**2 * (1 - cos_a), u[0] * u[1] * (1 - cos_a) - u[2] * sin_a, u[0] * u[2] * (1 - cos_a) + u[1] * sin_a],
            [u[1] * u[0] * (1 - cos_a) + u[2] * sin_a, cos_a + u[1]**2 * (1 - cos_a), u[1] * u[2] * (1 - cos_a) - u[0] * sin_a],
            [u[2] * u[0] * (1 - cos_a) - u[1] * sin_a, u[2] * u[1] * (1 - cos_a) + u[0] * sin_a, cos_a + u[2]**2 * (1 - cos_a)]
        ])

    def get_toolpath_points(self):
        """Return the current model points as a toolpath."""
        return self.model_points

    def optimize_toolpath(self, tolerance=0.01):
        """
        Simplify the toolpath by removing redundant points.
        Args:
            tolerance (float): Maximum allowed deviation from original path.
        Returns:
            np.array: Optimized toolpath points.
        """
        # Example: Use the Ramer-Douglas-Peucker algorithm for simplification
        simplified_points = [self.model_points[0]]
        for point in self.model_points[1:]:
            if np.linalg.norm(point - simplified_points[-1]) > tolerance:
                simplified_points.append(point)
        return np.array(simplified_points)

    def calculate_move_speed(self, point1, point2, move_time):
        """
        Calculate the required speed for a move from point1 to point2.
        Args:
            point1 (np.array): Starting point.
            point2 (np.array): Ending point.
            move_time (float): Time allocated for the move (seconds).
        Returns:
            float: Speed in units per second.
        """
        distance = np.linalg.norm(point2 - point1)
        return distance / move_time