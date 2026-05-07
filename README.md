# Dual xArm 8 + Hemera + Turntable Print Controller

Control two UFactory xArm 8 robots, each carrying a Hemera extruder, and a
motorised turntable from a single Python program.

## Hardware
- 2x xArm 8 (firmware >= 1.9)
- 1x SKR Pico running Klipper → two extruders (T0, T1)
- Raspberry Pi 5 with Moonraker (Klipper API)
- Turntable with serial MCU (speaks ROT: commands)
- PC (Ubuntu) on same network

## Installation
1. Clone this repository onto your PC.
2. Create a virtual environment (optional):
   `python3 -m venv venv && source venv/bin/activate`
3. Install dependencies:
   `pip install -r requirements.txt`
4. Edit `config/settings.yaml` to match your IP addresses and serial port.
5. Ensure the turntable MCU is plugged in and listening on the specified COM port.

## Testing individual components
- `python test/test_arm_comms.py`
- `python test/test_extruder_comms.py`
- `python test/test_turntable_comms.py`

## Running the first demo
1. Home and zero all axes:
   `python scripts/home_all.py`
2. Start the dual print:
   `python scripts/dual_print_demo.py`

## Safety
- Keep emergency stop accessible.
- The script does NOT check collisions – dry-run without filament first.
- Set software limits in config/settings.yaml.