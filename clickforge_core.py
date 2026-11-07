"""ClickForge logic with no hooks and no UI, so tests can run it anywhere."""
import collections
import ctypes
import json
import math
import os
import queue
import shutil
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "ClickForge"
API_PORT = 47811  # localhost control API used by the Stream Deck plugin
ERROR_ALREADY_EXISTS = 183
UI_WAIT_SECONDS = 0.6  # under the plugin's 800 ms timeout, or a slow UI looks like a closed app

DEFAULTS = {"multiplier": 12, "delay_ms": 10, "buttons": "left", "hotkey": ["f6"], "sound": True,
            "led": "scroll"}

MOD_ORDER = ["ctrl", "alt", "shift", "win"]

# Low-level mouse messages -> (button name, is_press). XBUTTON carries 1/2 in mouseData's high word.
MOUSE_MSGS = {
    0x201: ("mouse_left", True), 0x202: ("mouse_left", False),
    0x204: ("mouse_right", True), 0x205: ("mouse_right", False),
    0x207: ("mouse_middle", True), 0x208: ("mouse_middle", False),
    0x20B: ("mouse_x", True), 0x20C: ("mouse_x", False),
}
REPEAT_GAP_SECONDS = 1.5  # Windows repeats a held key sooner than this, even at its slowest

PLAIN_UNBINDABLE = {"mouse_left", "mouse_right"}  # need a modifier, or every click would toggle

# Punctuation keys are stored by virtual-key code. These are the US layout's names for them.
KEY_LABELS = {"vk186": ";", "vk187": "=", "vk188": ",", "vk189": "-", "vk190": ".", "vk191": "/",
              "vk192": "`", "vk219": "[", "vk220": "\\", "vk221": "]", "vk222": "'",
              "vk106": "NUM *", "vk107": "NUM +", "vk109": "NUM -", "vk110": "NUM .",
              "vk111": "NUM /"}

# With Num Lock off, the number pad sends arrows, Home, End and so on. The real navigation keys
# carry the extended flag and the number pad's don't, which lets a number pad key keep one name.
LLKHF_EXTENDED = 0x01
NUMPAD_NAV = {0x2D: "num0", 0x23: "num1", 0x28: "num2", 0x22: "num3", 0x25: "num4",
              0x0C: "num5", 0x27: "num6", 0x24: "num7", 0x26: "num8", 0x21: "num9",
              0x2E: "vk110"}

MIN_UI_SCALE = 0.6  # below this the text stops being readable
MULTIPLIER_RANGE = (1, 100)
DELAY_RANGE = (1, 200)
BUTTON_CHOICES = ("left", "right", "both")
LED_CHOICES = ("off", "scroll", "num")


def config_path():
    return os.path.join(os.environ.get("APPDATA", "."), APP_NAME, "config.json")


def mouse_name(msg, mouse_data):
    entry = MOUSE_MSGS.get(msg)
    if entry is None:
        return None, False
    name, pressed = entry
    if name == "mouse_x":
        name = "mouse4" if (mouse_data >> 16) == 1 else "mouse5"
    return name, pressed


def valid_combo(combo):
    """A list of known modifiers plus exactly one other key, and never a plain left/right click."""
    if not isinstance(combo, list) or not all(isinstance(k, str) and k for k in combo):
        return False
    mods = [k for k in combo if k in MOD_ORDER]
    rest = [k for k in combo if k not in MOD_ORDER]
    return len(rest) == 1 and (bool(mods) or rest[0] not in PLAIN_UNBINDABLE)


def _number(value, low, high, default):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default
    try:
        return max(low, min(high, round(value)))
    except (OverflowError, ValueError):  # Infinity and NaN both parse as JSON here
        return default


def sanitize_config(raw):
    """A config that's safe to run, from whatever the file held.

    Keys we don't manage (the plugin's exe_path) pass through untouched.
    """
    if not isinstance(raw, dict):
        return dict(DEFAULTS)
    cfg = {**DEFAULTS, **raw}
    cfg["multiplier"] = _number(cfg["multiplier"], *MULTIPLIER_RANGE, DEFAULTS["multiplier"])
    cfg["delay_ms"] = _number(cfg["delay_ms"], *DELAY_RANGE, DEFAULTS["delay_ms"])
    if cfg["buttons"] not in BUTTON_CHOICES:
        cfg["buttons"] = DEFAULTS["buttons"]
    if cfg["led"] not in LED_CHOICES:
        cfg["led"] = DEFAULTS["led"]
    if not isinstance(cfg["sound"], bool):
        cfg["sound"] = DEFAULTS["sound"]
    if not valid_combo(cfg["hotkey"]):
        cfg["hotkey"] = list(DEFAULTS["hotkey"])
    return cfg


def load_config(path=None):
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as f:
            return sanitize_config(json.load(f))
    except OSError:
        return dict(DEFAULTS)
    except ValueError:
        keep_damaged_copy(path)
        return dict(DEFAULTS)


def keep_damaged_copy(path):
    """The next save overwrites the file, so hold on to what's in it."""
    try:
        shutil.copyfile(path, f"{path}.bad")
    except OSError:
        pass


def save_config(cfg, path=None):
    """Replace the file in one step and report success. A crash can't leave half a file."""
    path = path or config_path()
    tmp = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, path)
    except OSError:  # a read-only folder, or a scanner holding the file: settings are not worth
        return False  # a crash, and a failure here used to stop the window from closing at all
    return True


def vk_name(vk):
    """Name for a virtual-key code that pynput doesn't have a Key member for."""
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x60 <= vk <= 0x69:
        return f"num{vk - 0x60}"
    return f"vk{vk}"


class NumpadNames:
    """Gives number pad keys the same name whether Num Lock is on or off.

    Without this, a number pad shortcut stops working as soon as the Num Lock LED indicator
    flips Num Lock. The hook filter sees the extended flag and the key callback doesn't; the
    callbacks come later on another thread, in the same order, so the two are paired by order.
    """

    def __init__(self):
        self._seen = collections.deque(maxlen=64)

    def seen(self, vk, flags):
        """From the hook filter, for every event that goes on to the key callbacks."""
        if vk in NUMPAD_NAV:
            self._seen.append((vk, None if flags & LLKHF_EXTENDED else NUMPAD_NAV[vk]))

    def name(self, vk, fallback):
        """From the key callback: the number pad name for this event, or `fallback`."""
        if vk not in NUMPAD_NAV:
            return fallback
        while self._seen:
            seen_vk, pad = self._seen.popleft()
            if seen_vk == vk:
                return pad or fallback
        return fallback


def key_label(name):
    if name in KEY_LABELS:
        return KEY_LABELS[name]
    if name in ("mouse4", "mouse5"):
        return f"MOUSE {name[-1]}"
    return name.replace("_", " ").upper()


def format_combo(combo):
    mods = [m for m in MOD_ORDER if m in combo]
    rest = [k for k in combo if k not in MOD_ORDER]
    return " + ".join(key_label(k) for k in mods + rest)


def fit_scale(needed, available, smallest=MIN_UI_SCALE):
    """How much to shrink a layout `needed` pixels tall to fit in `available` pixels."""
    return max(smallest, min(1.0, available / needed))


def blend(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


class ClickMultiplier:
    """Watches real clicks and replays each one (multiplier - 1) extra times."""

    BUTTONS = {"left": {"left"}, "right": {"right"}, "both": {"left", "right"}}

    def __init__(self, cfg, click, is_own_window=lambda x, y: False, cursor=lambda: (0, 0),
                 sleep=time.sleep):
        self._enabled = False
        self._session = 0  # goes up at every switch-off; older work is dropped
        self.multiplier = cfg["multiplier"]
        self.delay_ms = cfg["delay_ms"]
        self.buttons = cfg["buttons"]
        self.real_clicks = 0
        self.generated = 0
        self._click = click
        self._is_own_window = is_own_window
        self._cursor = cursor
        self._sleep = sleep
        self._jobs = queue.Queue(maxsize=64)
        self._closing = False
        self._thread = None

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, on):
        if self._enabled and not on:
            self._session += 1
        self._enabled = on

    def start_worker(self):
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop_worker(self):
        """Stop clicking now and wait for the worker, which may be mid-burst."""
        self.enabled = False
        self._closing = True
        try:
            self._jobs.put_nowait(None)  # wakes the worker
        except queue.Full:
            pass  # it's awake: with `enabled` off it drains the queue and sees `_closing`
        if self._thread:
            self._thread.join(1.0)

    def on_click(self, x, y, button, pressed):
        # Act on release so the user's original click completes untouched.
        if pressed or not self.enabled or button not in self.BUTTONS[self.buttons]:
            return
        if self._is_own_window(x, y):
            return
        self.real_clicks += 1
        if self.multiplier > 1:
            try:
                self._jobs.put_nowait((button, self._session))
            except queue.Full:
                pass

    def _worker(self):
        while not self._closing:
            job = self._jobs.get()
            if job is not None:
                self._replay(*job)

    def _replay(self, button, session):
        for _ in range(self.multiplier - 1):
            self._sleep(max(self.delay_ms, 1) / 1000)
            if session != self._session:  # switched off, even if it's back on by now
                return
            # A long burst can outlast the user reaching for our OFF button or a slider.
            if self._is_own_window(*self._cursor()):
                return
            try:
                self._click(button)
            except Exception:  # the worker must outlive one failed click, or ON stops working
                return
            self.generated += 1


class HotkeyState:
    """Global toggle shortcut, rebindable at runtime (modifiers + one key)."""

    def __init__(self, combo, on_trigger, on_captured, mod_is_down=lambda mod: True,
                 clock=time.monotonic):
        self.combo = frozenset(combo)
        self.capturing = False
        self._on_trigger = on_trigger
        self._on_captured = on_captured
        self._mod_is_down = mod_is_down  # asks Windows, since the hook can miss releases
        self._clock = clock
        self._mods = set()
        self._held = {}  # key name -> when we last saw it go down
        self._mouse_held = set()  # pressed buttons we consumed; their release is consumed too

    def _forget_released_mods(self):
        # Keys released on the lock screen or Ctrl+Alt+Del never reach our hook, so Win+L
        # would otherwise leave "win" held for ever and put it in the next shortcut.
        self._mods = {mod for mod in self._mods if self._mod_is_down(mod)}

    def mod_down(self, mod):
        self._mods.add(mod)

    def mod_up(self, mod):
        self._mods.discard(mod)

    def key_down(self, name):
        if name is None:
            return
        now = self._clock()
        last, self._held[name] = self._held.get(name), now
        if last is not None and now - last < REPEAT_GAP_SECONDS:  # auto-repeat while held
            return
        self._forget_released_mods()
        if self.capturing:
            self.capturing = False
            if name == "esc":
                self._on_captured(None)
            else:
                self.combo = frozenset(self._mods | {name})
                self._on_captured(sorted(self.combo))
            return
        if self._matches(name):
            self._on_trigger()

    def key_up(self, name):
        self._held.pop(name, None)

    def _matches(self, name):
        # Required modifiers must be held, extra ones are fine (e.g. Shift held to sprint in a game).
        trigger = self.combo - set(MOD_ORDER)
        return trigger == {name} and (self.combo - trigger) <= self._mods

    def mouse(self, name, pressed):
        """Called from the mouse hook. Returns True when the event belongs to the shortcut."""
        if not pressed:
            if name in self._mouse_held:
                self._mouse_held.discard(name)
                return True
            return False
        self._forget_released_mods()
        combo = frozenset(self._mods | {name})
        bindable = name not in PLAIN_UNBINDABLE or self._mods
        if self.capturing:
            if not bindable:
                return False
            self.capturing = False
            self.combo = combo
            self._on_captured(sorted(combo))
        elif self._matches(name):
            self._on_trigger()
        else:
            return False
        self._mouse_held.add(name)
        return True


class LockLed:
    """Mirrors the on/off state on a keyboard lock LED (Scroll or Num Lock)."""

    MODES = ("scroll", "num")

    def __init__(self, read_state, tap):
        self._read_state = read_state  # mode -> is the lock key on?
        self._tap = tap  # mode -> press the lock key once
        self.mode = "off"
        self._original = None  # lock state before we took over, restored on exit

    def use(self, mode):
        self.mode = mode
        if mode in self.MODES:
            self._original = self._read_state(mode)

    def show(self, on):
        if self.mode in self.MODES:
            self._set(on)

    def restore(self):
        if self.mode in self.MODES and self._original is not None:
            self._set(self._original)
            self._original = None

    def _set(self, on):
        if self._read_state(self.mode) != on:
            self._tap(self.mode)  # injected, so our own hotkey hook ignores it


def claim_single_instance(name=f"Local\\{APP_NAME}"):
    """A release function, or None if another ClickForge holds `name`.

    Two copies would each hook the mouse, so every real click would multiply twice. Windows
    drops the claim when the process ends, even after a crash.
    """
    if os.name != "nt":
        return lambda: None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return lambda: None  # we can't tell, and refusing to start would be worse
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return lambda: kernel32.CloseHandle(handle)


def run_all(steps):
    """Run every step even if one fails. For shutdown, where stopping halfway leaves hooks behind."""
    for step in steps:
        try:
            step()
        except Exception:
            traceback.print_exc()


def run_pending(jobs):
    """Run what the other threads queued for the UI thread. One failure can't stop the rest."""
    while True:
        try:
            job = jobs.get_nowait()
        except queue.Empty:
            return
        try:
            job()
        except Exception:
            traceback.print_exc()


class ExclusiveHTTPServer(ThreadingHTTPServer):
    # SO_REUSEADDR on Windows lets a second process bind a port that's in use, and the two
    # then share the requests. Linux needs it to rebind quickly and doesn't have that problem.
    allow_reuse_address = os.name != "nt"


class ApiServer:
    """Localhost-only HTTP API: GET /state, POST /toggle, /multiplier/up, /multiplier/down.

    POSTs must carry an X-ClickForge header; browsers can't add one cross-origin without a
    CORS preflight we never answer, so web pages cannot toggle the clicker. The Host check
    closes the way around that: a site that points its own domain at 127.0.0.1 is same-origin
    with us as far as the browser knows, but its requests name that domain as the Host.
    """

    def __init__(self, routes, get_state, run_on_ui, port=API_PORT):
        def host_ok(host):
            return host in (f"127.0.0.1:{port}", f"localhost:{port}")

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if not host_ok(self.headers.get("Host")):
                    self._reply(403, {"error": "forbidden"})
                elif self.path == "/state":
                    self._reply(200, get_state())
                else:
                    self._reply(404, {"error": "not found"})

            def do_POST(self):
                if not host_ok(self.headers.get("Host")):
                    self._reply(403, {"error": "forbidden"})
                    return
                action = routes.get(self.path)
                if action is None or self.headers.get("X-ClickForge") != "1":
                    self._reply(404, {"error": "not found"})
                    return
                done = threading.Event()

                def run():
                    try:
                        action()
                    finally:
                        done.set()

                run_on_ui(run)  # Tk state changes on the Tk thread
                done.wait(UI_WAIT_SECONDS)
                self._reply(200, get_state())

            def log_message(self, *args):
                pass

        try:
            self.httpd = ExclusiveHTTPServer(("127.0.0.1", port), Handler)
        except OSError:  # port busy (e.g. a second instance): run without the API
            self.httpd = None
            return
        threading.Thread(target=self.httpd.serve_forever, args=(0.1,), daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
