# DMX Controller - DJPOWER H-IP20V Fog Machine

A Python-based DMX lighting controller for the **DJPOWER H-IP20V** fog machine (16-channel mode) with a web interface and GPIO trigger support. Uses an ENTTEC Open DMX USB adapter to send DMX512 frames from a Raspberry Pi.

## Features

- **Web Interface** - Control all 16 DMX channels via a modern dark-themed UI
- **GPIO Trigger** - Automatic fog/lighting sequences via contact closure (pin 17)
- **Scene Management** - 4 scenes (A-D), save/recall custom settings from the UI
- **ENTTEC Support** - Communicates over ENTTEC Open DMX USB (FTDI-based)
- **Live Control** - Real-time sliders for fog, LEDs (RGBA), strobe, dimmer, and effects
- **Emergency Blackout** - One-button kill to zero all channels instantly
- **Auto-start** - Runs as a systemd service, starts on boot
- **Optional API Token** - Protect API endpoints with a simple bearer token
- **Health Check** - `/api/health` endpoint for monitoring and install verification
- **Auto-recovery** - Automatic ENTTEC USB reconnection and GPIO re-initialization

## Hardware Requirements

- **Raspberry Pi** (tested on Pi 5 with Raspberry Pi OS Bookworm)
- **ENTTEC Open DMX USB** adapter (FTDI-based)
- **DJPOWER H-IP20V** fog machine (or any compatible 16-channel DMX fixture)
- Standard USB and XLR DMX cables

### Wiring

```
Raspberry Pi USB --> ENTTEC Open DMX USB --> (XLR) --> DJPOWER H-IP20V

GPIO Pin 17 --> Contact closure switch --> GND
```

## DMX Channel Map (16-channel mode)

| Channel | Function           | Range                                  |
|---------|--------------------|----------------------------------------|
| 1       | Fog output         | 0-9 Off, 10-255 On                    |
| 2       | *(Disabled)*       | --                                     |
| 3       | Outer LED Red      | 0-9 Off, 10-255 Dim to bright         |
| 4       | Outer LED Green    | 0-9 Off, 10-255 Dim to bright         |
| 5       | Outer LED Blue     | 0-9 Off, 10-255 Dim to bright         |
| 6       | Outer LED Amber    | 0-9 Off, 10-255 Dim to bright         |
| 7       | Inner LED Red      | 0-9 Off, 10-255 Dim to bright         |
| 8       | Inner LED Green    | 0-9 Off, 10-255 Dim to bright         |
| 9       | Inner LED Blue     | 0-9 Off, 10-255 Dim to bright         |
| 10      | Inner LED Amber    | 0-9 Off, 10-255 Dim to bright         |
| 11      | LED Mix Color 1    | 0-9 Off, 10-255 Mix color selection   |
| 12      | LED Mix Color 2    | 0-9 Off, 10-255 Mix color selection   |
| 13      | LED Auto Color     | 0-9 Off, 10-255 Slow to fast cycling  |
| 14      | Strobe             | 0-9 Off, 10-255 Slow to fast          |
| 15      | Dimmer             | 0-9 Off, 10-255 Dim to bright         |
| 16      | Safety Channel     | 0-49 Invalid, 50-200 Valid, 201-255 Invalid |

## Quick Install (Recommended)

The installer handles everything: system packages, Python venv, FTDI kernel module blacklisting, udev rules, and systemd service.

```bash
# 1. Clone the repository
git clone https://github.com/morroware/djpower-dmx.git
cd djpower-dmx

# 2. Run the installer
sudo ./install.sh
```

That's it. The controller is now running and will auto-start on every boot.

Open `http://<your-pi-ip>:5000` in a browser to access the web interface.

### What the installer does

1. Installs system packages (`python3`, `python3-venv`, `libusb-1.0`, `libgpiod-dev`, `curl`)
2. Blacklists the `ftdi_sio` kernel module so pyftdi can access the ENTTEC adapter
3. Creates udev rules so the ENTTEC adapter is accessible without root
4. Stops any existing DMX service (safe for reinstalls/upgrades)
5. Copies application files to `/opt/dmx`
6. Creates a Python virtual environment and installs dependencies
7. Generates an API token and stores it in `/etc/dmx/dmx.env`
8. Creates and enables a `dmx` systemd service
9. Starts the service and runs a health check
10. Displays the web interface URL and API token

### Reinstalling / Upgrading

Just re-run the installer. It is idempotent:

```bash
cd djpower-dmx
git pull
sudo ./install.sh
```

Existing configuration (`/var/lib/dmx/config.json`) and API tokens (`/etc/dmx/dmx.env`) are preserved across reinstalls.

## Uninstall

Run the included uninstall script:

```bash
sudo ./uninstall.sh
```

This will:
- Stop and disable the systemd service
- Remove application files from `/opt/dmx`
- Remove udev rules and the ftdi_sio blacklist
- Optionally remove saved configuration and API tokens (you will be prompted)

System packages (`python3`, `libusb`, etc.) are left in place.

## Manual Installation

If you prefer to set things up yourself:

```bash
# System packages
sudo apt update
sudo apt install -y python3 python3-venv python3-pip python3-dev \
    libusb-1.0-0-dev libgpiod-dev curl git

# Blacklist ftdi_sio (required for ENTTEC access)
echo "blacklist ftdi_sio" | sudo tee /etc/modprobe.d/ftdi-blacklist.conf
sudo rmmod ftdi_sio 2>/dev/null || true

# Clone
git clone https://github.com/morroware/djpower-dmx.git
cd djpower-dmx

# Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run manually (uses gunicorn)
./start.sh
```

### Setting up udev rules (required for non-root access)

```bash
sudo tee /etc/udev/rules.d/99-ftdi-dmx.rules <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="0403", ATTR{idProduct}=="6001", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="0403", ATTR{idProduct}=="6014", MODE="0666", GROUP="plugdev"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -aG plugdev $USER
```

Unplug and re-plug the ENTTEC adapter after applying these rules.

### Setting up the systemd service manually

```bash
sudo tee /etc/systemd/system/dmx.service <<EOF
[Unit]
Description=DMX Controller - DJPOWER H-IP20V
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/gunicorn --workers 1 --threads 4 --bind 0.0.0.0:5000 app:app
Restart=on-failure
RestartSec=5
SupplementaryGroups=plugdev gpio

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable dmx
sudo systemctl start dmx
```

## Service Management

```bash
sudo systemctl status dmx        # Check if running
sudo systemctl restart dmx       # Restart after config changes
sudo systemctl stop dmx          # Stop the controller
sudo systemctl disable dmx       # Disable auto-start on boot
sudo journalctl -u dmx -f        # Follow live logs
sudo journalctl -u dmx --since "5 min ago"  # Recent logs
```

## ENTTEC Detection Troubleshooting

If the app starts but reports ENTTEC disconnected, use this sequence:

```bash
# 1) Confirm USB device is present
lsusb | rg -i '0403|ftdi|enttec'

# 2) Confirm kernel driver is not claiming it
lsmod | rg ftdi_sio

# 3) Check app health/status payload (includes ENTTEC URL + last error)
curl -s http://127.0.0.1:5000/api/health | python3 -m json.tool
curl -s http://127.0.0.1:5000/api/status | python3 -m json.tool

# 4) Check live service logs for URL attempts/open failures
sudo journalctl -u dmx -f
```

What to look for:

- `enttec_connected: false` with `enttec_last_error` mentioning permissions:
  - Re-apply udev rules and re-plug the adapter.
  - Ensure service user is in `plugdev`.
- `enttec_last_error` mentioning kernel claim / busy device:
  - `sudo rmmod ftdi_sio` and reboot if needed.
- Multiple FTDI devices connected:
  - Set `DMX_FTDI_URL` explicitly in `/etc/dmx/dmx.env` to the correct adapter.

Example explicit URL:

```bash
DMX_FTDI_URL=ftdi://0403:6001/1
```

After changes:

```bash
sudo systemctl restart dmx
```

## Usage

Access the web interface at `http://<your-pi-ip>:5000`

### Quick Controls
- **Trigger** - Activates Scene B (fog + full LEDs) for the configured duration (default 10 s), then auto-reverts to Scene A
- **Scene A-D** - Switch between predefined scenes
- **Emergency Blackout** - Zeros all 512 DMX channels immediately

### Live Controls
- **Fog** - Direct fog output level
- **Dimmer** - Overall LED brightness
- **Strobe** - Strobe speed
- **Outer / Inner LEDs** - Individual RGBA color control
- **LED Effects** - Mix colors and auto-color cycling
- **Safety Channel** - Must be in the 50-200 range for the fixture to operate (the API enforces this range)

### Scene Editor
Adjust controls to your desired settings, pick a scene slot (A-D), and click **Save Current Settings to Scene** to store them for quick recall.

### GPIO Trigger
Connect a contact closure between **GPIO pin 17** and **GND**. When the contact closes, the controller fires the trigger sequence (same as the web Trigger button). The pin uses an internal pull-up, so no external resistor is needed.

## Configuration

Edit scene presets and timing in `app.py` (or `/opt/dmx/app.py` if installed via the installer) under the `Config` class. The controller persists scene updates to `/var/lib/dmx/config.json` by default.

- `CONTACT_PIN` - GPIO pin number for trigger input (default: 17)
- `GPIO_CHIP` - Optional GPIO chip override (e.g., `0`, `gpiochip0`, `/dev/gpiochip0`). Auto-detects if unset.
- `SCENE_B_DURATION` - How long the triggered scene lasts in seconds (default: 10)
- `SCENES` - Channel values for each of the four scenes
- `DMX_FTDI_URL` - Environment variable to select a specific FTDI device (default: `ftdi://0403:6001/1`)
- `DMX_API_TOKEN` - Optional environment variable to require an API token for `/api/*` endpoints

After editing, restart the service:

```bash
sudo systemctl restart dmx
```

### API Authentication (Optional)

If installed via `install.sh`, an API token is automatically generated and stored in `/etc/dmx/dmx.env`. The installer prints the token at the end of the installation.

To view the current token:

```bash
sudo cat /etc/dmx/dmx.env
```

To set a custom token manually, edit the environment file:

```bash
sudo nano /etc/dmx/dmx.env
```

Then restart the service:

```bash
sudo systemctl restart dmx
```

Send the token via either header:

- `Authorization: Bearer <token>`
- `X-Api-Token: <token>`

> **Note:** Without a token, the API is open to anyone with network access to port 5000.

The built-in web UI supports tokens by adding `?token=<your-token>` once in the URL. It will store the token in your browser and remove it from the URL on refresh.

## API Endpoints

| Method   | Endpoint            | Auth | Description                                |
|----------|---------------------|------|--------------------------------------------|
| GET      | `/`                 | No   | Web interface                              |
| GET      | `/api/health`       | No   | Health check (200 = ok, 503 = degraded)    |
| GET      | `/api/status`       | Yes  | System status and channel values           |
| POST     | `/api/trigger`      | Yes  | Fire the trigger sequence                  |
| POST     | `/api/scene/<name>` | Yes  | Apply a scene (scene_a through scene_d)    |
| GET      | `/api/scenes`       | Yes  | List all scenes and their channels         |
| POST     | `/api/channel`      | Yes  | Set a single channel `{channel, value}`    |
| POST     | `/api/blackout`     | Yes  | Emergency blackout (all channels to 0)     |
| GET/POST | `/api/config`       | Yes  | Read or update scene config and duration   |

**Auth** column: "Yes" means the `DMX_API_TOKEN` is required if one is set. "No" means the endpoint is always accessible.

## File Layout

| Path | Description |
|------|-------------|
| `app.py` | Flask application - DMX control, GPIO, API routes |
| `index.html` | Web UI (dark-themed, responsive) |
| `install.sh` | Automated installer for Raspberry Pi |
| `uninstall.sh` | Clean uninstaller |
| `start.sh` | Manual startup script (loads env, activates venv) |
| `requirements.txt` | Python dependencies |
| `/opt/dmx/` | Installed application (created by installer) |
| `/var/lib/dmx/config.json` | Persisted scene configuration |
| `/etc/dmx/dmx.env` | API token and environment variables |
| `/etc/systemd/system/dmx.service` | Systemd service unit |
| `/etc/udev/rules.d/99-ftdi-dmx.rules` | FTDI USB permissions |
| `/etc/modprobe.d/ftdi-blacklist.conf` | Blocks ftdi_sio kernel driver |

## Troubleshooting

### ENTTEC adapter not detected
```bash
# Check USB devices
lsusb | grep -i ftdi

# Verify ftdi_sio is NOT loaded (it must be blacklisted)
lsmod | grep ftdi_sio
# If it shows output, unload it:
sudo rmmod ftdi_sio

# Check udev rules are loaded
sudo udevadm test /sys/bus/usb/devices/*  2>&1 | grep -i ftdi

# Verify permissions
ls -la /dev/bus/usb/*/*
```

### Service won't start
```bash
# Check logs for error details
sudo journalctl -u dmx -n 50 --no-pager

# Run the health check manually
curl -s http://localhost:5000/api/health | python3 -m json.tool

# Try running manually to see output
cd /opt/dmx
sudo -u $USER venv/bin/python3 app.py
```

### GPIO not working
```bash
# Verify gpiod is installed
dpkg -l | grep gpiod

# Check GPIO chip is accessible
gpioinfo gpiochip4 2>/dev/null || gpioinfo gpiochip0

# Test pin 17 manually
gpioget gpiochip4 17
```

If your GPIO chip is not `gpiochip4`, update `GPIO_CHIP` in `app.py` (or set it to `gpiochip0`), or use `gpioget gpiochip0 17` to verify the pin.

### Web interface not loading
```bash
# Confirm the service is running
sudo systemctl status dmx

# Check if port 5000 is listening
ss -tlnp | grep 5000

# Check firewall (if enabled)
sudo ufw status
sudo ufw allow 5000/tcp   # if needed
```

### ftdi_sio kernel module keeps loading
```bash
# Verify the blacklist file exists
cat /etc/modprobe.d/ftdi-blacklist.conf

# Should contain:
# blacklist ftdi_sio

# If it doesn't exist, create it:
echo "blacklist ftdi_sio" | sudo tee /etc/modprobe.d/ftdi-blacklist.conf

# Unload immediately:
sudo rmmod ftdi_sio

# Restart the service:
sudo systemctl restart dmx
```
