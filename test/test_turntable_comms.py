import automation1 as a1
import time

# ---------------- CONFIG ----------------
CONTROLLER_IP = "192.168.7.1"
AXIS_NAME = "U"
MOVE_DISTANCE_DEGREES = [360.0]
SPEED_DEG_PER_SEC = [30.0]

# ------------- HELPER FUNCTIONS -------------
def wait_for_task(controller_task_status, action_name="Action"):
    """
    Polls the task status until the controller task is no longer running.
    Prints errors if they occur.
    """
    while controller_task_status.task_state == "Running":
        print(f"{action_name} in progress...")
        time.sleep(0.5)
    if controller_task_status.error:
        raise RuntimeError(f"Error during {action_name}: {controller_task_status.error_message}")
    print(f"{action_name} completed successfully.")

# ------------- MAIN SCRIPT -------------
def main():
    print(f"Connecting to iXC4 at {CONTROLLER_IP}...")
    controller = a1.Controller.connect(host=CONTROLLER_IP)
    print("Connected!")

    # Grab the task status object
    task_status = controller.runtime.tasks[1].status
    print("Controller task status object loaded.")

    try:
        # Start controller
        print("Starting controller...")
        controller.start()
        print("Controller started.")

        # Enable axis
        print(f"Enabling axis '{AXIS_NAME}'...")
        controller.runtime.commands.motion.enable([AXIS_NAME])
        wait_for_task(task_status, action_name=f"Enabling axis '{AXIS_NAME}'")

        # Home axis
        print(f"Homing axis '{AXIS_NAME}'...")
        controller.runtime.commands.motion.home([AXIS_NAME])
        wait_for_task(task_status, action_name=f"Homing axis '{AXIS_NAME}'")

        # Move axis
        print(f"Moving axis '{AXIS_NAME}' to {MOVE_DISTANCE_DEGREES} degrees at {SPEED_DEG_PER_SEC} deg/sec...")
        controller.runtime.commands.motion.moveabsolute([AXIS_NAME], MOVE_DISTANCE_DEGREES, SPEED_DEG_PER_SEC)
        wait_for_task(task_status, action_name=f"Moving axis '{AXIS_NAME}'")

    except Exception as e:
        print(f"An error occurred: {e}")

    finally:
        # Disable axis
        print(f"Disabling axis '{AXIS_NAME}'...")
        controller.runtime.commands.motion.disable([AXIS_NAME])
        wait_for_task(task_status, action_name=f"Disabling axis '{AXIS_NAME}'")

        # Disconnect controller
        print("Disconnecting controller...")
        controller.disconnect()
        print("Controller disconnected.")

if __name__ == "__main__":
    main()



