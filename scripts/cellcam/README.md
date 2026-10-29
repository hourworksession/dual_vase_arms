# cellcam: the camera Pi

Raspberry Pi 3 B + official 7" touchscreen + Pi camera. `cellcam.py` streams the camera
on port 8080 and the touchscreen shows it full screen with an alignment crosshair.

| URL | What |
| --- | --- |
| `http://<camera-pi>:8080/` | live view, crosshair, zoom 1/2/4/8x, freeze |
| `/stream.mjpg` | MJPEG stream (browser, OpenCV, Mainsail webcam) |
| `/snapshot.jpg` | latest frame, for scripts on the lab PC |
| `/zoom?level=4`, `/status` | digital zoom about the centre; JSON status |

Try it anywhere without a camera: `python3 cellcam.py --test`.

## 1. Flash the SD card (on any computer)

Raspberry Pi Imager > Raspberry Pi 3 > **Raspberry Pi OS (64-bit)** (with desktop, for the
touchscreen). In the settings (gear / "Edit settings"):
- hostname `cellcam`, username `pi` and a password
- the lab Wi-Fi, or leave it off and use Ethernet
- Services: **enable SSH**

Plug in the camera ribbon (blue side towards the Ethernet port on the Pi 3) with the power off.
Boot. The touchscreen should show the desktop.

## 2. Check the camera (on the Pi's terminal, or `ssh pi@cellcam.local`)

    rpicam-hello --list-cameras        # should list the sensor (ov5647 = v1, imx219 = v2, imx708 = v3)
    rpicam-hello -t 5000               # 5 s preview on the touchscreen

Older images call these `libcamera-hello`. If no camera is found: power off, reseat the ribbon
at both ends.

## 3. Copy and run the server

    mkdir -p ~/cellcam
    # copy cellcam.py into ~/cellcam (USB stick, scp, or curl from GitHub)
    python3 ~/cellcam/cellcam.py

Open `http://cellcam.local:8080` on the lab PC. picamera2 ships with Raspberry Pi OS; if it is
missing: `sudo apt install -y python3-picamera2`.

## 4. Start at boot and show it on the touchscreen

    sudo cp cellcam.service /etc/systemd/system/      # edit User/paths if not "pi"
    sudo systemctl enable --now cellcam

Kiosk browser on the touchscreen. Recent Raspberry Pi OS (labwc desktop):

    mkdir -p ~/.config/labwc
    echo 'chromium-browser --kiosk --noerrdialogs --disable-infobars http://localhost:8080 &' >> ~/.config/labwc/autostart

Older desktop (X11 / LXDE): put the same line, without the `&`, prefixed with `@`, in
`~/.config/lxsession/LXDE-pi/autostart`. Reboot. To get out of kiosk: Alt+F4 with a keyboard.

## Lens (HQ Camera, IMX477) for a nozzle 15-30 mm from the lens

Preferred: **6 mm CS-mount wide lens** (the official HQ kit "wide angle" lens) with a
**1-3 mm spacer** between lens and camera so it focuses that close. Expected view:

| Nozzle to lens | Spacer (approx.) | Field of view | Detail at 1280 px |
| --- | --- | --- | --- |
| 30 mm | ~1.5 mm | ~25 mm wide | ~0.02 mm/px |
| 20 mm | ~2.5 mm | ~15 mm wide | ~0.012 mm/px |

(Thin-lens estimates; the real distances shift by a few mm because the lens's optical centre
sits behind its front glass. Set focus by eye on the 4x view.)

- Alternative: an M12 lens of about 4-8 mm on the HQ Camera's M12 version or an M12 adapter.
- Not suitable: the 16 mm C-mount telephoto (view only ~3 mm wide, needs ~28 mm of extension)
  and the microscope lens (works ~100 mm away).
- Do not use the C-CS adapter ring as the spacer with the 6 mm lens: 5 mm is too much
  (focus ~13 mm away, view ~8 mm).
- Depth of focus is only about a millimetre this close: useful as a Z check, but the nozzle
  must be near the focus plane before it looks sharp.
- A hot nozzle 15-30 mm from the glass can drip or radiate onto it: align cold or briefly, and
  consider a thin glass cover slip in front of the lens.

## 5. Focus

Camera Module 1/2 are fixed-focus, set for about 1 m. For a nozzle 50-100 mm away, turn the
lens anticlockwise with the small focus tool (or fine pliers) a little at a time while watching
the 4x view. Camera Module 3 focuses itself.
