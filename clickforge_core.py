"""ClickForge logic with no hooks, no Windows calls and no UI, so tests can run it anywhere."""
import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "ClickForge"
API_PORT = 47811  # localhost control API used by the Stream Deck plugin

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
PLAIN_UNBINDABLE = {"mouse_left", "mouse_right"}  # need a modifier, or every click would toggle


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


def load_config(path=None):
    cfg = dict(DEFAULTS)
    try:
        with open(path or config_path(), encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def vk_name(vk):
    """Name for a virtual-key code that pynput doesn't have a Key member for."""
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x60 <= vk <= 0x69:
        return f"num{vk - 0x60}"
    return f"vk{vk}"


def format_combo(combo):
    mods = [m for m in MOD_ORDER if m in combo]
    rest = [k for k in combo if k not in MOD_ORDER]
    return " + ".join(p.replace("mouse_", "mouse ").replace("mouse4", "mouse 4")
                      .replace("mouse5", "mouse 5").upper() for p in mods + rest)


def blend(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


class ClickMultiplier:
    """Watches real clicks and replays each one (multiplier - 1) extra times."""

    BUTTONS = {"left": {"left"}, "right": {"right"}, "both": {"left", "right"}}

    def __init__(self, cfg, click, is_own_window=lambda x, y: False, sleep=time.sleep):
        self.enabled = False
        self.multiplier = cfg["multiplier"]
        self.delay_ms = cfg["delay_ms"]
        self.buttons = cfg["buttons"]
        self.real_clicks = 0
        self.generated = 0
        self._click = click
        self._is_own_window = is_own_window
        self._sleep = sleep
        self._jobs = queue.Queue(maxsize=64)

    def start_worker(self):
        threading.Thread(target=self._worker, daemon=True).start()

    def on_click(self, x, y, button, pressed):
        # Act on release so the user's original click completes untouched.
        if pressed or not self.enabled or button not in self.BUTTONS[self.buttons]:
            return
        if self._is_own_window(x, y):
            return
        self.real_clicks += 1
        if self.multiplier > 1:
            try:
                self._jobs.put_nowait(button)
            except queue.Full:
                pass

    def _worker(self):
        while True:
            self._replay(self._jobs.get())

    def _replay(self, button):
        for _ in range(self.multiplier - 1):
            if not self.enabled:
                break
            self._sleep(max(self.delay_ms, 1) / 1000)
            self._click(button)
            self.generated += 1


class HotkeyState:
    """Global toggle shortcut, rebindable at runtime (modifiers + one key)."""

    def __init__(self, combo, on_trigger, on_captured):
        self.combo = frozenset(combo)
        self.capturing = False
        self._on_trigger = on_trigger
        self._on_captured = on_captured
        self._mods = set()
        self._held = set()
        self._mouse_held = set()  # pressed buttons we consumed; their release is consumed too

    def mod_down(self, mod):
        self._mods.add(mod)

    def mod_up(self, mod):
        self._mods.discard(mod)

    def key_down(self, name):
        if name is None or name in self._held:  # ignore auto-repeat while held
            return
        self._held.add(name)
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
        self._held.discard(name)

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


class ApiServer:
    """Localhost-only HTTP API: GET /state, POST /toggle, /multiplier/up, /multiplier/down.

    POSTs must carry an X-ClickForge header; browsers can't add one cross-origin without a
    CORS preflight we never answer, so web pages cannot toggle the clicker.
    """

    def __init__(self, routes, get_state, run_on_ui, port=API_PORT):
        class Handler(BaseHTTPRequestHandler):
            def _reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/state":
                    self._reply(200, get_state())
                else:
                    self._reply(404, {"error": "not found"})

            def do_POST(self):
                action = routes.get(self.path)
                if action is None or self.headers.get("X-ClickForge") != "1":
                    self._reply(404, {"error": "not found"})
                    return
                done = threading.Event()
                run_on_ui(lambda: (action(), done.set()))  # Tk state changes on the Tk thread
                done.wait(1.0)
                self._reply(200, get_state())

            def log_message(self, *args):
                pass

        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:  # port busy (e.g. a second instance): run without the API
            self.httpd = None
            return
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
