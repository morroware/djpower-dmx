#!/usr/bin/env python3
"""
DMX Controller for DJPOWER H-IP20V Fog Machine
16-channel mode with LED control and GPIO trigger
"""

from flask import Flask, jsonify, request, send_file
import time
import json
import os
import glob
from threading import Lock, Timer, Thread

from pyftdi.ftdi import Ftdi
import importlib
import importlib.util

# Detect GPIO library (will work on Pi 5)
GPIO_AVAILABLE = False
GPIO_LIB = None
gpiod = None
lgpio = None

if importlib.util.find_spec("gpiod"):
    gpiod = importlib.import_module("gpiod")
    GPIO_AVAILABLE = True
    GPIO_LIB = 'gpiod'
elif importlib.util.find_spec("lgpio"):
    lgpio = importlib.import_module("lgpio")
    GPIO_AVAILABLE = True
    GPIO_LIB = 'lgpio'
else:
    print("WARNING: No GPIO library available")

app = Flask(__name__)

# Path for persisting scene config across restarts
CONFIG_DIR = os.environ.get("DMX_CONFIG_DIR", "/var/lib/dmx")
CONFIG_FILE = os.environ.get(
    "DMX_CONFIG_FILE",
    os.path.join(CONFIG_DIR, "config.json"),
)
API_TOKEN = os.environ.get("DMX_API_TOKEN")

# ============================================
# CONFIGURATION
# ============================================

class Config:
    """Application configuration"""

    # GPIO Settings
    CONTACT_PIN = 17
    GPIO_CHIP = None  # Optional override: int index or string like "gpiochip0" or "/dev/gpiochip0"

    # DMX Settings
    DMX_CHANNELS = 512
    DMX_REFRESH_RATE = 44
    FTDI_URL = os.environ.get("DMX_FTDI_URL", "ftdi://0403:6001/1")

    # Timing
    SCENE_B_DURATION = 10.0  # seconds

    # GPIO debounce - ignore transitions within this window
    DEBOUNCE_TIME = 0.3  # seconds

    # DJPOWER H-IP20V Fog Machine (16-channel mode)
    # Full channel map:
    # Ch1: Fog (0-9 Off, 10-255 On)
    # Ch2: Disabled
    # Ch3: Outer LED Red (0-9 Off, 10-255 Dim to bright)
    # Ch4: Outer LED Green (0-9 Off, 10-255 Dim to bright)
    # Ch5: Outer LED Blue (0-9 Off, 10-255 Dim to bright)
    # Ch6: Outer LED Amber (0-9 Off, 10-255 Dim to bright)
    # Ch7: Inner LED Red (0-9 Off, 10-255 Dim to bright)
    # Ch8: Inner LED Green (0-9 Off, 10-255 Dim to bright)
    # Ch9: Inner LED Blue (0-9 Off, 10-255 Dim to bright)
    # Ch10: Inner LED Amber (0-9 Off, 10-255 Dim to bright)
    # Ch11: LED Mix Color 1 (0-9 Off, 10-255 Mix color)
    # Ch12: LED Mix Color 2 (0-9 Off, 10-255 Mix color)
    # Ch13: LED Auto Color (0-9 Off, 10-255 Slow to fast)
    # Ch14: Strobe (0-9 Off, 10-255 Slow to fast)
    # Ch15: Dimmer (0-9 Off, 10-255 Dim to bright)
    # Ch16: Safety Channel (0-49 Invalid, 50-200 Valid, 201-255 Invalid)

    SCENES = {
        'scene_a': {
            'name': 'All OFF (Default)',
            'channels': {
                1: 0,     # Fog: Off
                2: 0,     # Disabled
                3: 0,     # Outer Red: Off
                4: 0,     # Outer Green: Off
                5: 0,     # Outer Blue: Off
                6: 0,     # Outer Amber: Off
                7: 0,     # Inner Red: Off
                8: 0,     # Inner Green: Off
                9: 0,     # Inner Blue: Off
                10: 0,    # Inner Amber: Off
                11: 0,    # LED Mix 1: Off
                12: 0,    # LED Mix 2: Off
                13: 0,    # Auto Color: Off
                14: 0,    # Strobe: Off
                15: 0,    # Dimmer: Off
                16: 100,  # Safety: Valid
            }
        },
        'scene_b': {
            'name': 'Fog ON (Triggered)',
            'channels': {
                1: 255,   # Fog: Full
                2: 0,     # Disabled
                3: 255,   # Outer Red: Full
                4: 255,   # Outer Green: Full
                5: 255,   # Outer Blue: Full
                6: 0,     # Outer Amber: Off
                7: 255,   # Inner Red: Full
                8: 255,   # Inner Green: Full
                9: 255,   # Inner Blue: Full
                10: 0,    # Inner Amber: Off
                11: 0,    # LED Mix 1: Off
                12: 0,    # LED Mix 2: Off
                13: 0,    # Auto Color: Off
                14: 0,    # Strobe: Off
                15: 255,  # Dimmer: Full
                16: 100,  # Safety: Valid
            }
        },
        'scene_c': {
            'name': 'Custom Scene 1',
            'channels': {
                1: 255,   # Fog: Full
                2: 0,     # Disabled
                3: 0,     # Outer Red: Off
                4: 0,     # Outer Green: Off
                5: 255,   # Outer Blue: Full
                6: 0,     # Outer Amber: Off
                7: 0,     # Inner Red: Off
                8: 0,     # Inner Green: Off
                9: 255,   # Inner Blue: Full
                10: 0,    # Inner Amber: Off
                11: 0,    # LED Mix 1: Off
                12: 0,    # LED Mix 2: Off
                13: 0,    # Auto Color: Off
                14: 50,   # Strobe: Slow
                15: 200,  # Dimmer: 80%
                16: 100,  # Safety: Valid
            }
        },
        'scene_d': {
            'name': 'Custom Scene 2',
            'channels': {
                1: 200,   # Fog: High
                2: 0,     # Disabled
                3: 255,   # Outer Red: Full
                4: 0,     # Outer Green: Off
                5: 0,     # Outer Blue: Off
                6: 200,   # Outer Amber: High
                7: 255,   # Inner Red: Full
                8: 0,     # Inner Green: Off
                9: 0,     # Inner Blue: Off
                10: 200,  # Inner Amber: High
                11: 0,    # LED Mix 1: Off
                12: 0,    # LED Mix 2: Off
                13: 100,  # Auto Color: Medium
                14: 0,    # Strobe: Off
                15: 255,  # Dimmer: Full
                16: 100,  # Safety: Valid
            }
        }
    }

config = Config()

# ============================================
# Config Persistence
# ============================================

def save_config():
    """Save current scene config and duration to disk"""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        data = {
            'scene_b_duration': config.SCENE_B_DURATION,
            'scenes': {}
        }
        for key, scene in config.SCENES.items():
            data['scenes'][key] = {
                'name': scene['name'],
                'channels': {str(k): v for k, v in scene['channels'].items()}
            }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"WARNING: Could not save config: {e}")


def load_config():
    """Load scene config from disk if it exists"""
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE, 'r') as f:
            data = json.load(f)
        if 'scene_b_duration' in data:
            config.SCENE_B_DURATION = float(data['scene_b_duration'])
        if 'scenes' in data:
            for key, scene in data['scenes'].items():
                if key in config.SCENES:
                    safe_channels = {}
                    raw_channels = scene.get('channels', {})
                    for ch_key, ch_value in raw_channels.items():
                        channel = int(ch_key)
                        value = int(ch_value)
                        if not (1 <= channel <= config.DMX_CHANNELS):
                            continue
                        if channel == 16 and not (50 <= value <= 200):
                            value = 100
                        safe_channels[channel] = max(0, min(255, value))
                    config.SCENES[key]['name'] = scene.get('name', config.SCENES[key]['name'])
                    if safe_channels:
                        config.SCENES[key]['channels'] = safe_channels
        print("Loaded saved configuration from disk")
    except Exception as e:
        print(f"WARNING: Could not load config (using defaults): {e}")

# ============================================
# Global State
# ============================================

class SystemState:
    """Global system state manager"""

    def __init__(self):
        self.ftdi_device = None
        self.dmx_data = bytearray([0] * (config.DMX_CHANNELS + 1))
        self.dmx_lock = Lock()
        self.current_scene = None
        self.scene_b_timer = None
        self.timer_lock = Lock()  # Protects scene_b_timer access
        self.gpio_line = None
        self.gpio_chip = None
        self.gpio_chip_id = None
        self.gpio_ready = False  # Explicit flag for GPIO readiness
        self.dmx_thread = None
        self.dmx_running = False

state = SystemState()

# ============================================
# ENTTEC DMX Functions
# ============================================

def init_enttec():
    """Initialize ENTTEC Open DMX USB"""
    try:
        print("Initializing ENTTEC Open DMX USB...")

        devices = Ftdi.list_devices()

        if not devices:
            print("ERROR: No FTDI devices found!")
            return False

        print(f"Found {len(devices)} FTDI device(s)")

        state.ftdi_device = Ftdi()
        state.ftdi_device.open_from_url(config.FTDI_URL)

        # Configure for DMX512
        state.ftdi_device.set_baudrate(250000)
        state.ftdi_device.set_line_property(8, 2, 'N')
        state.ftdi_device.set_latency_timer(1)

        print("ENTTEC initialized successfully")
        return True

    except Exception as e:
        print(f"ERROR initializing ENTTEC: {e}")
        return False


def reinit_enttec():
    """Attempt to re-initialize the ENTTEC after a failure"""
    try:
        if state.ftdi_device:
            try:
                state.ftdi_device.close()
            except Exception:
                pass
            state.ftdi_device = None
        return init_enttec()
    except Exception as e:
        print(f"ERROR re-initializing ENTTEC: {e}")
        return False


def dmx_refresh_thread():
    """Background thread to continuously send DMX frames.

    Automatically recovers from USB errors by re-initializing the ENTTEC device.
    """
    refresh_interval = 1.0 / config.DMX_REFRESH_RATE
    consecutive_errors = 0
    MAX_ERRORS_BEFORE_REINIT = 3
    REINIT_BACKOFF = 2.0  # seconds to wait before attempting reinit
    offline_backoff = 1.0
    offline_backoff_max = 10.0

    print(f"DMX refresh thread started ({config.DMX_REFRESH_RATE} Hz)")

    while state.dmx_running:
        try:
            if state.ftdi_device is None:
                raise Exception("FTDI device not available")
            with state.dmx_lock:
                # Send BREAK
                state.ftdi_device.set_break(True)
                time.sleep(0.000088)
                state.ftdi_device.set_break(False)
                time.sleep(0.000008)

                # Send data
                state.ftdi_device.write_data(state.dmx_data)

            consecutive_errors = 0
            offline_backoff = 1.0
            time.sleep(refresh_interval)

        except Exception as e:
            consecutive_errors += 1
            if "FTDI device not available" in str(e):
                print(f"WARNING: DMX refresh offline: {e}")
                time.sleep(offline_backoff)
                offline_backoff = min(offline_backoff * 2, offline_backoff_max)
                reinit_enttec()
                continue

            if consecutive_errors <= MAX_ERRORS_BEFORE_REINIT:
                print(f"WARNING: DMX refresh error ({consecutive_errors}/{MAX_ERRORS_BEFORE_REINIT}): {e}")
                time.sleep(0.1)
                continue

            # Too many consecutive errors - attempt to re-initialize
            print(f"ERROR: {consecutive_errors} consecutive DMX failures. Attempting ENTTEC re-init...")
            time.sleep(REINIT_BACKOFF)

            if reinit_enttec():
                print("ENTTEC re-initialized successfully, resuming DMX output")
                consecutive_errors = 0
            else:
                print(f"ENTTEC re-init failed. Retrying in {REINIT_BACKOFF}s...")
                # Keep looping - don't break out. Will retry on next iteration.

    print("DMX refresh thread stopped")


def start_dmx_refresh():
    """Start background DMX refresh thread"""
    if state.dmx_thread is None or not state.dmx_thread.is_alive():
        state.dmx_running = True
        state.dmx_thread = Thread(target=dmx_refresh_thread, daemon=True)
        state.dmx_thread.start()


def stop_dmx_refresh():
    """Stop background DMX refresh thread"""
    if state.dmx_thread is not None:
        state.dmx_running = False
        state.dmx_thread.join(timeout=2)
        state.dmx_thread = None


def set_channel(channel, value):
    """Set a single DMX channel value"""
    if 1 <= channel <= config.DMX_CHANNELS:
        with state.dmx_lock:
            state.dmx_data[int(channel)] = max(0, min(255, int(value)))


def apply_scene(scene_name):
    """Apply a scene to DMX channels"""
    if scene_name not in config.SCENES:
        print(f"ERROR: Scene {scene_name} not found")
        return False

    scene = config.SCENES[scene_name]

    # Apply scene values atomically
    with state.dmx_lock:
        for channel, value in scene['channels'].items():
            if 1 <= int(channel) <= config.DMX_CHANNELS:
                state.dmx_data[int(channel)] = max(0, min(255, int(value)))

    state.current_scene = scene_name
    print(f"Applied scene: {scene['name']}")

    return True


def get_current_channels():
    """Get current DMX channel values"""
    with state.dmx_lock:
        return {
            'fog': state.dmx_data[1],
            'outer_red': state.dmx_data[3],
            'outer_green': state.dmx_data[4],
            'outer_blue': state.dmx_data[5],
            'outer_amber': state.dmx_data[6],
            'inner_red': state.dmx_data[7],
            'inner_green': state.dmx_data[8],
            'inner_blue': state.dmx_data[9],
            'inner_amber': state.dmx_data[10],
            'led_mix1': state.dmx_data[11],
            'led_mix2': state.dmx_data[12],
            'auto_color': state.dmx_data[13],
            'strobe': state.dmx_data[14],
            'dimmer': state.dmx_data[15],
            'safety': state.dmx_data[16],
        }

# ============================================
# GPIO Functions
# ============================================

def _normalize_gpiochip_id(chip_id):
    if chip_id is None:
        return None
    if isinstance(chip_id, int):
        return chip_id
    chip_id = str(chip_id).strip()
    if chip_id.isdigit():
        return int(chip_id)
    if chip_id.startswith("/dev/") or chip_id.startswith("gpiochip"):
        return chip_id
    return chip_id


def _gpiochip_candidates():
    if config.GPIO_CHIP is not None:
        return [_normalize_gpiochip_id(config.GPIO_CHIP)]
    candidates = []
    for path in sorted(glob.glob("/dev/gpiochip*")):
        candidates.append(path)
    return candidates


def _open_gpiod_line(chip_id):
    chip_id = _normalize_gpiochip_id(chip_id)
    chip = gpiod.Chip(chip_id) if chip_id is not None else None
    try:
        if hasattr(gpiod, "request_lines") and hasattr(gpiod, "LineSettings"):
            direction_enum = getattr(gpiod, "LineDirection", None)
            bias_enum = getattr(gpiod, "LineBias", None)
            if direction_enum is None and hasattr(gpiod, "line"):
                direction_enum = gpiod.line.Direction
                bias_enum = gpiod.line.Bias
            line_settings = gpiod.LineSettings(
                direction=direction_enum.INPUT,
                bias=bias_enum.PULL_UP,
            )
            request = gpiod.request_lines(
                chip,
                consumer="dmx_controller",
                config={config.CONTACT_PIN: line_settings},
            )
            return chip, request

        line = chip.get_line(config.CONTACT_PIN)
        line.request(
            consumer="dmx_controller",
            type=gpiod.LINE_REQ_DIR_IN,
            flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP,
        )
        return chip, line
    except Exception:
        if chip is not None:
            try:
                chip.close()
            except Exception:
                pass
        raise


def _open_lgpio_line(chip_id):
    chip_id = _normalize_gpiochip_id(chip_id)
    if isinstance(chip_id, str):
        digits = "".join(ch for ch in chip_id if ch.isdigit())
        chip_id = int(digits) if digits else None
    chip_id = 0 if chip_id is None else chip_id
    chip = lgpio.gpiochip_open(chip_id)
    try:
        lgpio.gpio_claim_input(chip, config.CONTACT_PIN, lgpio.SET_PULL_UP)
        return chip
    except Exception:
        try:
            lgpio.gpiochip_close(chip)
        except Exception:
            pass
        raise


def init_gpio():
    """Initialize GPIO for contact closure detection"""
    if not GPIO_AVAILABLE:
        print("GPIO not available (not running on Raspberry Pi)")
        return False

    if state.gpio_line is not None or state.gpio_chip is not None:
        try:
            if GPIO_LIB == 'gpiod' and state.gpio_line is not None:
                state.gpio_line.release()
            if GPIO_LIB == 'gpiod' and state.gpio_chip is not None:
                state.gpio_chip.close()
            if GPIO_LIB == 'lgpio' and state.gpio_chip is not None:
                lgpio.gpiochip_close(state.gpio_chip)
        except Exception as e:
            print(f"WARNING: GPIO cleanup before init failed: {e}")
        state.gpio_line = None
        state.gpio_chip = None
        state.gpio_chip_id = None

    try:
        if GPIO_LIB == 'gpiod':
            for chip_id in _gpiochip_candidates():
                try:
                    state.gpio_chip, state.gpio_line = _open_gpiod_line(chip_id)
                    state.gpio_ready = True
                    state.gpio_chip_id = chip_id
                    print(f"GPIO initialized (gpiod) - {chip_id} pin {config.CONTACT_PIN} with pull-up")
                    return True
                except Exception as e:
                    print(f"GPIO init failed on {chip_id}: {e}")

        elif GPIO_LIB == 'lgpio':
            for chip_id in _gpiochip_candidates():
                try:
                    state.gpio_chip = _open_lgpio_line(chip_id)
                    state.gpio_ready = True
                    state.gpio_chip_id = chip_id
                    print(f"GPIO initialized (lgpio) - {chip_id} pin {config.CONTACT_PIN} with pull-up")
                    return True
                except Exception as e:
                    print(f"GPIO init failed on {chip_id}: {e}")

    except Exception as e:
        print(f"GPIO initialization failed: {e}")
    state.gpio_ready = False
    return False


def check_contact_state():
    """Check current contact closure state"""
    if not GPIO_AVAILABLE or not state.gpio_ready:
        return None

    try:
        if GPIO_LIB == 'gpiod':
            try:
                return state.gpio_line.get_value()
            except TypeError:
                return state.gpio_line.get_value(config.CONTACT_PIN)
        elif GPIO_LIB == 'lgpio':
            return lgpio.gpio_read(state.gpio_chip, config.CONTACT_PIN)
    except Exception as e:
        print(f"WARNING: GPIO read error: {e}")
        return None


def trigger_sequence():
    """Execute the lighting sequence (thread-safe)"""
    print("\nTRIGGER DETECTED!")

    with state.timer_lock:
        # Cancel any existing timer
        if state.scene_b_timer is not None:
            state.scene_b_timer.cancel()

        # Apply Scene B (Light ON)
        apply_scene('scene_b')

        # Set timer to return to Scene A (Light OFF)
        def _return_to_scene_a():
            with state.timer_lock:
                apply_scene('scene_a')
                state.scene_b_timer = None

        state.scene_b_timer = Timer(config.SCENE_B_DURATION, _return_to_scene_a)
        state.scene_b_timer.daemon = True
        state.scene_b_timer.start()

    print(f"Timer set: Scene A (OFF) in {config.SCENE_B_DURATION} seconds")

# ============================================
# Flask Routes
# ============================================

def _auth_required():
    return API_TOKEN is not None and API_TOKEN != ""


def _authorized():
    if not _auth_required():
        return True
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):].strip() == API_TOKEN
    return request.headers.get("X-Api-Token") == API_TOKEN


@app.before_request
def enforce_auth():
    if request.path.startswith("/api/") and not _authorized():
        return jsonify({'error': 'Unauthorized'}), 401


def _validate_channel(channel):
    return 1 <= channel <= config.DMX_CHANNELS


def _sanitize_channel_value(channel, value):
    if channel == 16:
        if not (50 <= value <= 200):
            raise ValueError("Safety channel must be between 50 and 200")
    return max(0, min(255, int(value)))


@app.route('/')
def index():
    """Main web interface - serve index.html directly"""
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html'))


@app.route('/api/status')
def api_status():
    """Get current system status"""
    contact_state = check_contact_state()

    return jsonify({
        'enttec_connected': state.ftdi_device is not None,
        'dmx_running': state.dmx_running and (state.dmx_thread is not None and state.dmx_thread.is_alive()),
        'current_scene': state.current_scene,
        'contact_state': 'closed' if contact_state == 0 else 'open' if contact_state == 1 else 'unknown',
        'gpio_available': GPIO_AVAILABLE,
        'gpio_ready': state.gpio_ready,
        'scene_b_duration': config.SCENE_B_DURATION,
        'channels': get_current_channels(),
    })


@app.route('/api/trigger', methods=['POST'])
def api_trigger():
    """Manually trigger the sequence"""
    trigger_sequence()
    return jsonify({'success': True})


@app.route('/api/scene/<scene_name>', methods=['POST'])
def api_apply_scene(scene_name):
    """Apply a specific scene"""
    with state.timer_lock:
        if state.scene_b_timer is not None:
            state.scene_b_timer.cancel()
            state.scene_b_timer = None

    if apply_scene(scene_name):
        return jsonify({'success': True, 'scene': scene_name})
    else:
        return jsonify({'error': 'Scene not found'}), 404


@app.route('/api/scenes', methods=['GET'])
def api_list_scenes():
    """List all available scenes"""
    scenes = {}
    for key, scene in config.SCENES.items():
        scenes[key] = {
            'name': scene['name'],
            'channels': scene['channels']
        }
    return jsonify(scenes)


@app.route('/api/channel', methods=['POST'])
def api_set_channel():
    """Set individual channel value"""
    data = request.get_json()
    if not data or 'channel' not in data or 'value' not in data:
        return jsonify({'error': 'Missing channel or value'}), 400

    try:
        channel = int(data['channel'])
        value = int(data['value'])
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid channel or value'}), 400

    if not _validate_channel(channel):
        return jsonify({'error': 'Channel out of range'}), 400

    try:
        safe_value = _sanitize_channel_value(channel, value)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    set_channel(channel, safe_value)
    return jsonify({'success': True, 'channel': channel, 'value': safe_value})


@app.route('/api/blackout', methods=['POST'])
def api_blackout():
    """Emergency blackout - all channels to zero"""
    with state.timer_lock:
        if state.scene_b_timer is not None:
            state.scene_b_timer.cancel()
            state.scene_b_timer = None
    with state.dmx_lock:
        for i in range(1, config.DMX_CHANNELS + 1):
            state.dmx_data[i] = 0
        # Keep safety channel valid so fixture stays responsive to future commands
        state.dmx_data[16] = 100
    state.current_scene = None
    print("BLACKOUT - All channels zeroed (safety channel kept valid)")
    return jsonify({'success': True})


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    """Get or update configuration"""
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': 'Invalid or missing JSON body'}), 400

        # Update any scene
        for scene_key in ['scene_a', 'scene_b', 'scene_c', 'scene_d']:
            if scene_key in data:
                try:
                    raw = data[scene_key]
                    if not isinstance(raw, dict):
                        return jsonify({'error': f'Invalid data for {scene_key}'}), 400
                    channels = {}
                    for k, v in raw.items():
                        channel = int(k)
                        if not _validate_channel(channel):
                            return jsonify({'error': f'Channel out of range in {scene_key}: {channel}'}), 400
                        channels[channel] = _sanitize_channel_value(channel, int(v))
                except (TypeError, ValueError):
                    return jsonify({'error': f'Invalid channel data in {scene_key}'}), 400
                config.SCENES[scene_key]['channels'] = channels
                print(f"Updated {scene_key}: {config.SCENES[scene_key]['channels']}")
                # Re-apply if it's the current scene
                if state.current_scene == scene_key:
                    apply_scene(scene_key)

        # Update duration (clamp to safe range)
        if 'scene_b_duration' in data:
            try:
                dur = float(data['scene_b_duration'])
                if dur != dur:  # NaN check
                    return jsonify({'error': 'Invalid duration value'}), 400
                dur = max(0.5, min(300.0, dur))
                config.SCENE_B_DURATION = dur
                print(f"Updated Scene B duration: {config.SCENE_B_DURATION}s")
            except (TypeError, ValueError):
                return jsonify({'error': 'Invalid duration value'}), 400

        # Persist to disk
        save_config()

        return jsonify({'success': True})
    else:
        return jsonify({
            'scene_a': config.SCENES['scene_a']['channels'],
            'scene_b': config.SCENES['scene_b']['channels'],
            'scene_c': config.SCENES['scene_c']['channels'],
            'scene_d': config.SCENES['scene_d']['channels'],
            'scene_b_duration': config.SCENE_B_DURATION,
            'contact_pin': config.CONTACT_PIN,
        })

# ============================================
# Main Entry Point
# ============================================

def main():
    """Initialize and run the application"""
    print("=" * 60)
    print("DMX CONTROLLER - DJPOWER H-IP20V Fog Machine")
    print("=" * 60)
    print()

    # Load saved config before anything else
    load_config()

    # Initialize ENTTEC (do not exit if the adapter isn't present yet)
    if not init_enttec():
        print("WARNING: ENTTEC not available at startup. Will keep retrying in the background.")

    # Start continuous DMX refresh
    start_dmx_refresh()
    time.sleep(0.5)

    # Initialize GPIO (optional)
    init_gpio()

    # Apply initial scene (Scene A - Light OFF)
    apply_scene('scene_a')

    print()
    print("=" * 60)
    print("System ready!")
    print("   Default: All OFF (Scene A)")
    print(f"   On trigger: Fog ON for {config.SCENE_B_DURATION} seconds (Scene B)")
    print("   Custom scenes: C & D available")
    print()
    print("   Web interface: http://0.0.0.0:5000")
    if GPIO_AVAILABLE and state.gpio_ready:
        print(f"   GPIO Pin {config.CONTACT_PIN} monitoring active")
    elif GPIO_AVAILABLE:
        print(f"   GPIO available but init failed - monitor will retry automatically")
    print("=" * 60)
    print()

    # Start Flask app
    try:
        # GPIO monitoring with debounce and error recovery
        # Always start the monitor thread if GPIO libraries are available, so it
        # can recover from transient init failures at boot.
        if GPIO_AVAILABLE:
            def gpio_monitor():
                last_state = None
                last_trigger_time = 0.0
                consecutive_errors = 0
                max_errors_before_reinit = 3

                while True:
                    try:
                        # If GPIO isn't ready yet, attempt initialization
                        if not state.gpio_ready:
                            if init_gpio():
                                print("GPIO initialized from monitor thread")
                                last_state = None  # Reset edge detection
                            else:
                                time.sleep(5.0)  # Retry init every 5s
                                continue

                        current_state = check_contact_state()
                        if current_state is not None:
                            now = time.monotonic()
                            # Detect falling edge (open -> closed) with debounce
                            if (last_state == 1 and current_state == 0
                                    and (now - last_trigger_time) >= config.DEBOUNCE_TIME):
                                last_trigger_time = now
                                trigger_sequence()
                            last_state = current_state
                            consecutive_errors = 0
                        time.sleep(0.05)  # 50ms poll - fast enough for human-speed contact closures
                    except Exception as e:
                        consecutive_errors += 1
                        print(f"WARNING: GPIO monitor error ({consecutive_errors}/{max_errors_before_reinit}): {e}")
                        if consecutive_errors >= max_errors_before_reinit:
                            print("Attempting GPIO re-initialization...")
                            state.gpio_ready = False
                            last_state = None  # Reset edge detection after reinit
                            consecutive_errors = 0
                        time.sleep(1.0)  # Back off on error, then continue

            gpio_thread = Thread(target=gpio_monitor, daemon=True)
            gpio_thread.start()

        app.run(host='0.0.0.0', port=5000, debug=False)

    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        stop_dmx_refresh()
        with state.timer_lock:
            if state.scene_b_timer:
                state.scene_b_timer.cancel()
        if state.ftdi_device:
            try:
                state.ftdi_device.close()
            except Exception:
                pass
        if GPIO_AVAILABLE and state.gpio_ready:
            try:
                if GPIO_LIB == 'gpiod' and state.gpio_line is not None:
                    state.gpio_line.release()
                if GPIO_LIB == 'gpiod' and state.gpio_chip is not None:
                    state.gpio_chip.close()
                if GPIO_LIB == 'lgpio' and state.gpio_chip is not None:
                    lgpio.gpiochip_close(state.gpio_chip)
            except Exception as e:
                print(f"WARNING: GPIO cleanup failed: {e}")
        print("Shutdown complete")


if __name__ == "__main__":
    main()
