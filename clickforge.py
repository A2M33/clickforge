"""ClickForge - turn one real mouse click into many."""
import ctypes
import json
import math
import os
import queue
import sys
import threading
import time
import winsound
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import customtkinter as ctk
from pynput import keyboard, mouse

APP_NAME = "ClickForge"
CONFIG_PATH = os.path.join(os.environ.get("APPDATA", "."), APP_NAME, "config.json")
API_PORT = 47811  # localhost control API used by the Stream Deck plugin

# Windows low-level hook flags: set on events produced by SendInput (our own clicks).
LLMHF_INJECTED = 0x01
LLKHF_INJECTED = 0x10
GA_ROOT = 2

user32 = ctypes.windll.user32
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND

# Palette (Razer Synapse-like: black surfaces, neon green accent).
BG = "#0A0A0A"
PANEL = "#121212"
PANEL_BORDER = "#232323"
TRACK = "#2A2A2A"
ACCENT = "#44D62C"
ACCENT_HOVER = "#5BEF42"
ACCENT_DIM = "#1C5A12"
TEXT = "#EDEDED"
MUTED = "#7A7A7A"
OFF = "#FF3B3B"

DEFAULTS = {"multiplier": 12, "delay_ms": 10, "buttons": "left", "hotkey": ["f6"], "sound": True,
            "led": "scroll"}

MODIFIERS = {
    keyboard.Key.ctrl: "ctrl", keyboard.Key.ctrl_l: "ctrl", keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.shift: "shift", keyboard.Key.shift_l: "shift", keyboard.Key.shift_r: "shift",
    keyboard.Key.alt: "alt", keyboard.Key.alt_l: "alt", keyboard.Key.alt_r: "alt",
    keyboard.Key.alt_gr: "alt",
    keyboard.Key.cmd: "win", keyboard.Key.cmd_l: "win", keyboard.Key.cmd_r: "win",
}
MOD_ORDER = ["ctrl", "alt", "shift", "win"]

# Low-level mouse messages -> (button name, is_press). XBUTTON carries 1/2 in mouseData's high word.
MOUSE_MSGS = {
    0x201: ("mouse_left", True), 0x202: ("mouse_left", False),
    0x204: ("mouse_right", True), 0x205: ("mouse_right", False),
    0x207: ("mouse_middle", True), 0x208: ("mouse_middle", False),
    0x20B: ("mouse_x", True), 0x20C: ("mouse_x", False),
}
PLAIN_UNBINDABLE = {"mouse_left", "mouse_right"}  # need a modifier, or every click would toggle


def mouse_name(msg, data):
    entry = MOUSE_MSGS.get(msg)
    if entry is None:
        return None, False
    name, pressed = entry
    if name == "mouse_x":
        name = "mouse4" if (data.mouseData >> 16) == 1 else "mouse5"
    return name, pressed


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def key_name(key):
    if isinstance(key, keyboard.Key):
        return key.name
    vk = getattr(key, "vk", None)
    if vk is not None:
        if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
            return chr(vk).lower()
        if 0x60 <= vk <= 0x69:
            return f"num{vk - 0x60}"
        return f"vk{vk}"
    return key.char.lower() if key.char else None


def format_combo(combo):
    mods = [m for m in MOD_ORDER if m in combo]
    rest = [k for k in combo if k not in MOD_ORDER]
    return " + ".join(p.replace("mouse_", "mouse ").replace("mouse4", "mouse 4")
                      .replace("mouse5", "mouse 5").upper() for p in mods + rest)


def blend(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


class ClickEngine:
    """Watches real clicks and replays each one (multiplier - 1) extra times."""

    BUTTONS = {
        "left": {mouse.Button.left},
        "right": {mouse.Button.right},
        "both": {mouse.Button.left, mouse.Button.right},
    }

    def __init__(self, cfg, hotkeys=None):
        self.enabled = False
        self.hotkeys = hotkeys
        self.multiplier = cfg["multiplier"]
        self.delay_ms = cfg["delay_ms"]
        self.buttons = cfg["buttons"]
        self.own_hwnd = None
        self.real_clicks = 0
        self.generated = 0
        self._jobs = queue.Queue(maxsize=64)
        self._mouse = mouse.Controller()
        threading.Thread(target=self._worker, daemon=True).start()
        self._listener = mouse.Listener(on_click=self._on_click, win32_event_filter=self._filter)
        self._listener.start()

    def _filter(self, msg, data):
        # Returning False hides the event from on_click; our own clicks must never re-trigger.
        if data.flags & LLMHF_INJECTED:
            return False
        name, pressed = mouse_name(msg, data)
        if name and self.hotkeys and self.hotkeys.mouse(name, pressed):
            # A bound mouse button is swallowed system-wide (no browser "Back" on mouse 4).
            self._listener.suppress_event()
        return True

    def _is_own_window(self, x, y):
        hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        return bool(hwnd) and user32.GetAncestor(hwnd, GA_ROOT) == self.own_hwnd

    def _on_click(self, x, y, button, pressed):
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
            button = self._jobs.get()
            for _ in range(self.multiplier - 1):
                if not self.enabled:
                    break
                time.sleep(max(self.delay_ms, 1) / 1000)
                self._mouse.click(button)
                self.generated += 1

    def stop(self):
        self._listener.stop()


class HotkeyManager:
    """Global toggle shortcut, rebindable at runtime (modifiers + one key)."""

    def __init__(self, combo, on_trigger, on_captured):
        self.combo = frozenset(combo)
        self.capturing = False
        self._on_trigger = on_trigger
        self._on_captured = on_captured
        self._mods = set()
        self._held = set()
        self._mouse_held = set()  # pressed buttons we consumed; their release is consumed too
        self._listener = keyboard.Listener(
            on_press=self._press, on_release=self._release,
            win32_event_filter=lambda msg, data: not (data.flags & LLKHF_INJECTED))
        self._listener.start()

    def _press(self, key):
        mod = MODIFIERS.get(key)
        if mod:
            self._mods.add(mod)
            return
        name = key_name(key)
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

    def _release(self, key):
        mod = MODIFIERS.get(key)
        if mod:
            self._mods.discard(mod)
        else:
            self._held.discard(key_name(key))

    def stop(self):
        self._listener.stop()


class Card(ctk.CTkFrame):
    def __init__(self, master, title, **kw):
        super().__init__(master, fg_color=PANEL, corner_radius=14,
                         border_width=1, border_color=PANEL_BORDER, **kw)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=title, text_color=MUTED,
                     font=("Bahnschrift SemiBold", 12)).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 0))


class Segmented(ctk.CTkFrame):
    """Pill-style option group; built by hand so the selected label can be dark on neon."""

    def __init__(self, master, options, command, font_size=13):
        super().__init__(master, fg_color=TRACK, corner_radius=10)
        self.buttons = {}
        for i, (key, text) in enumerate(options):
            self.grid_columnconfigure(i, weight=1)
            b = ctk.CTkButton(self, text=text, height=34, width=20, corner_radius=8,
                              font=("Bahnschrift SemiBold", font_size),
                              command=lambda k=key: command(k))
            b.grid(row=0, column=i, sticky="ew", padx=3, pady=3)
            self.buttons[key] = b

    def select(self, key):
        for k, b in self.buttons.items():
            active = k == key
            b.configure(fg_color=ACCENT if active else TRACK,
                        hover_color=ACCENT_HOVER if active else "#383838",
                        text_color=BG if active else MUTED)


class LockLed:
    """Mirrors the on/off state on a keyboard lock LED (Scroll or Num Lock)."""

    KEYS = {"scroll": (0x91, keyboard.Key.scroll_lock), "num": (0x90, keyboard.Key.num_lock)}

    def __init__(self):
        self._kb = keyboard.Controller()
        self.mode = "off"
        self._original = None  # lock state before we took over, restored on exit

    @staticmethod
    def _state(vk):
        return bool(user32.GetKeyState(vk) & 1)

    def use(self, mode):
        self.mode = mode
        if mode in self.KEYS:
            self._original = self._state(self.KEYS[mode][0])

    def show(self, on):
        if self.mode in self.KEYS:
            self._set(on)

    def restore(self):
        if self.mode in self.KEYS and self._original is not None:
            self._set(self._original)
            self._original = None

    def _set(self, on):
        vk, key = self.KEYS[self.mode]
        if self._state(vk) != on:
            self._kb.tap(key)  # injected, so our own hotkey hook ignores it


class ApiServer:
    """Localhost-only HTTP API: GET /state, POST /toggle, /multiplier/up, /multiplier/down.

    POSTs must carry an X-ClickForge header; browsers can't add one cross-origin without a
    CORS preflight we never answer, so web pages cannot toggle the clicker.
    """

    def __init__(self, app):
        routes = {
            "/toggle": app.toggle,
            "/multiplier/up": lambda: app._set_multiplier(app.engine.multiplier + 1),
            "/multiplier/down": lambda: app._set_multiplier(app.engine.multiplier - 1),
        }

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
                    self._reply(200, app.api_state())
                else:
                    self._reply(404, {"error": "not found"})

            def do_POST(self):
                action = routes.get(self.path)
                if action is None or self.headers.get("X-ClickForge") != "1":
                    self._reply(404, {"error": "not found"})
                    return
                done = threading.Event()
                app.ui_queue.put(lambda: (action(), done.set()))  # Tk state changes on the Tk thread
                done.wait(1.0)
                self._reply(200, app.api_state())

            def log_message(self, *args):
                pass

        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", API_PORT), Handler)
        except OSError:  # port busy (e.g. a second instance): run without the API
            self.httpd = None
            return
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.title(APP_NAME)
        self.resizable(False, False)
        self.configure(fg_color=BG)

        self.ui_queue = queue.Queue()  # listener threads hand work to the Tk thread through this
        self.hotkeys = HotkeyManager(
            self.cfg["hotkey"],
            on_trigger=lambda: self.ui_queue.put(self.toggle),
            on_captured=lambda c: self.ui_queue.put(lambda: self._on_captured(c)))
        self.engine = ClickEngine(self.cfg, self.hotkeys)
        self._save_job = None
        self._phase = 0.0
        self.led = LockLed()

        self._build()
        self.update_idletasks()
        self.engine.own_hwnd = user32.GetAncestor(self.winfo_id(), GA_ROOT)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_power()
        if getattr(sys, "frozen", False):  # lets the Stream Deck plugin launch the app
            self._queue_save("exe_path", sys.executable)
        self.api = ApiServer(self)
        self._tick()

    def api_state(self):
        return {"enabled": self.engine.enabled, "multiplier": self.engine.multiplier}

    # ---------- layout ----------
    def _build(self):
        pad = {"padx": 22}

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(22, 0), **pad)
        title = ctk.CTkFrame(header, fg_color="transparent")
        title.pack(side="left")
        ctk.CTkLabel(title, text="CLICK", text_color=TEXT,
                     font=("Bahnschrift SemiBold", 28)).pack(side="left")
        ctk.CTkLabel(title, text="FORGE", text_color=ACCENT,
                     font=("Bahnschrift SemiBold", 28)).pack(side="left")
        self.status = ctk.CTkLabel(header, text="", corner_radius=10, height=26, width=96,
                                   font=("Bahnschrift SemiBold", 12))
        self.status.pack(side="right")
        ctk.CTkLabel(self, text="MULTI-CLICK ENGINE", text_color=MUTED,
                     font=("Bahnschrift", 11)).pack(anchor="w", **pad)

        # A frame, not a CTkButton: buttons widen to fit text padding and stop being circular.
        self.power = ctk.CTkFrame(self, width=170, height=170, corner_radius=85, border_width=3,
                                  cursor="hand2")
        self.power.pack(pady=(22, 8))
        self.power.pack_propagate(False)
        self.power_text = ctk.CTkLabel(self.power, text="", font=("Bahnschrift SemiBold", 34),
                                       cursor="hand2")
        self.power_text.place(relx=0.5, rely=0.5, anchor="center")
        self._power_hover = False
        for w in (self.power, self.power_text):
            w.bind("<Button-1>", lambda e: self.toggle())
            w.bind("<Enter>", lambda e: self._hover_power(True))
            w.bind("<Leave>", lambda e: self._hover_power(False))
        self.hint = ctk.CTkLabel(self, text="", text_color=MUTED, font=("Bahnschrift", 12))
        self.hint.pack(pady=(0, 16))

        # Multiplier
        card = Card(self, "CLICKS PER CLICK")
        card.pack(fill="x", pady=(0, 10), **pad)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=18, pady=(2, 0))
        self.mult_label = ctk.CTkLabel(row, text="", text_color=ACCENT,
                                       font=("Bahnschrift SemiBold", 34))
        self.mult_label.pack(side="left")
        for text, step in (("+", 1), ("−", -1)):
            ctk.CTkButton(row, text=text, width=36, height=36, corner_radius=10,
                          fg_color=TRACK, hover_color="#383838", text_color=TEXT,
                          font=("Bahnschrift SemiBold", 18),
                          command=lambda s=step: self._set_multiplier(self.engine.multiplier + s)
                          ).pack(side="right", padx=(6, 0))
        self.mult_slider = self._slider(card, 1, 100, self._set_multiplier)
        self.mult_slider.grid(row=2, column=0, sticky="ew", padx=18, pady=(6, 16))

        # Interval
        card = Card(self, "INTERVAL BETWEEN CLICKS")
        card.pack(fill="x", pady=(0, 10), **pad)
        self.delay_label = ctk.CTkLabel(card, text="", text_color=TEXT,
                                        font=("Bahnschrift SemiBold", 22))
        self.delay_label.grid(row=1, column=0, sticky="w", padx=18)
        self.delay_slider = self._slider(card, 1, 200, self._set_delay)
        self.delay_slider.grid(row=2, column=0, sticky="ew", padx=18, pady=(6, 16))

        # Mouse button
        card = Card(self, "MOUSE BUTTON")
        card.pack(fill="x", pady=(0, 10), **pad)
        self.button_seg = Segmented(card, (("left", "LEFT"), ("right", "RIGHT"), ("both", "BOTH")),
                                    self._set_buttons)
        self.button_seg.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 16))

        # Keyboard LED: an on/off indicator that also works over exclusive-fullscreen games.
        card = Card(self, "KEYBOARD LED INDICATOR")
        card.pack(fill="x", pady=(0, 10), **pad)
        self.led_seg = Segmented(card, (("off", "OFF"), ("scroll", "SCROLL LOCK"), ("num", "NUM LOCK")),
                                 self._set_led, font_size=12)
        self.led_seg.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 16))

        # Shortcut
        card = Card(self, "TOGGLE SHORTCUT")
        card.pack(fill="x", pady=(0, 10), **pad)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 16))
        self.key_box = ctk.CTkLabel(row, text="", height=38, corner_radius=8, fg_color=TRACK,
                                    text_color=TEXT, font=("Bahnschrift SemiBold", 15))
        self.key_box.pack(side="left", fill="x", expand=True)
        self.rebind_btn = ctk.CTkButton(row, text="REBIND", width=96, height=38, corner_radius=8,
                                        fg_color="transparent", border_width=1, border_color=ACCENT,
                                        hover_color=ACCENT_DIM, text_color=ACCENT,
                                        font=("Bahnschrift SemiBold", 13), command=self._start_rebind)
        self.rebind_btn.pack(side="right", padx=(10, 0))

        # Footer: live counters + sound
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", pady=(6, 22), **pad)
        ctk.CTkFrame(self, width=480, height=0, fg_color="transparent").pack()  # fixes window width
        self.real_label = self._stat(footer, "REAL")
        self.gen_label = self._stat(footer, "GENERATED")
        self.sound = ctk.CTkSwitch(footer, text="SOUND", text_color=MUTED, progress_color=ACCENT,
                                   button_color=TEXT, fg_color=TRACK, font=("Bahnschrift", 11),
                                   command=self._set_sound, width=40)
        self.sound.pack(side="right")
        if self.cfg["sound"]:
            self.sound.select()

        self._set_multiplier(self.cfg["multiplier"], save=False)
        self._set_delay(self.cfg["delay_ms"], save=False)
        self._set_buttons(self.cfg["buttons"], save=False)
        self._set_led(self.cfg["led"], save=False)
        self._show_hotkey()

    def _slider(self, master, lo, hi, command):
        return ctk.CTkSlider(master, from_=lo, to=hi, number_of_steps=hi - lo, height=18,
                             fg_color=TRACK, progress_color=ACCENT, button_color=ACCENT,
                             button_hover_color=ACCENT_HOVER, command=command)

    def _stat(self, master, name):
        box = ctk.CTkFrame(master, fg_color="transparent")
        box.pack(side="left", padx=(0, 22))
        ctk.CTkLabel(box, text=name, text_color=MUTED, font=("Bahnschrift", 10)).pack(anchor="w")
        value = ctk.CTkLabel(box, text="0", text_color=TEXT, font=("Bahnschrift SemiBold", 18))
        value.pack(anchor="w")
        return value

    # ---------- state ----------
    def toggle(self):
        self.engine.enabled = not self.engine.enabled
        self._refresh_power()
        self.led.show(self.engine.enabled)
        if self.cfg["sound"]:
            freq = 1320 if self.engine.enabled else 660
            threading.Thread(target=winsound.Beep, args=(freq, 70), daemon=True).start()

    def _hover_power(self, inside):
        self._power_hover = inside
        self._refresh_power()

    def _refresh_power(self):
        on, hover = self.engine.enabled, self._power_hover
        fill = (ACCENT_HOVER if hover else ACCENT) if on else ("#1B1B1B" if hover else PANEL)
        self.power.configure(fg_color=fill, border_color=ACCENT if on else "#333333")
        self.power_text.configure(text="ON" if on else "OFF", fg_color=fill,
                                  text_color=BG if on else MUTED)
        self.status.configure(text="● ACTIVE" if on else "● IDLE",
                              text_color=ACCENT if on else OFF,
                              fg_color=ACCENT_DIM if on else "#2A1212")

    def _set_multiplier(self, value, save=True):
        value = max(1, min(100, int(round(float(value)))))
        self.engine.multiplier = value
        self.mult_label.configure(text=f"×{value}")
        self.mult_slider.set(value)
        if save:
            self._queue_save("multiplier", value)

    def _set_delay(self, value, save=True):
        value = max(1, min(200, int(round(float(value)))))
        self.engine.delay_ms = value
        cps = 1000 // value
        self.delay_label.configure(text=f"{value} ms   ·   ~{cps} CPS")
        self.delay_slider.set(value)
        if save:
            self._queue_save("delay_ms", value)

    def _set_buttons(self, key, save=True):
        self.engine.buttons = key
        self.button_seg.select(key)
        if save:
            self._queue_save("buttons", key)

    def _set_led(self, mode, save=True):
        self.led.restore()  # hand the previous lock key back to the user
        self.cfg["led"] = mode
        self.led_seg.select(mode)
        self.led.use(mode)
        self.led.show(self.engine.enabled)
        if save:
            self._queue_save("led", mode)

    def _set_sound(self):
        self._queue_save("sound", bool(self.sound.get()))

    def _show_hotkey(self):
        text = format_combo(self.cfg["hotkey"])
        self.hotkey_text = text
        self.key_box.configure(text=text, text_color=TEXT)
        self.hint.configure(text=f"PRESS  {text}  TO TOGGLE")
        self.rebind_btn.configure(text="REBIND")

    def _start_rebind(self):
        self.hotkeys.capturing = True
        self.key_box.configure(text="PRESS KEY OR MOUSE BUTTON…", text_color=ACCENT)
        self.rebind_btn.configure(text="ESC = CANCEL")

    def _on_captured(self, combo):
        if combo:
            self._queue_save("hotkey", combo)
        self._show_hotkey()

    def _queue_save(self, key, value):
        self.cfg[key] = value
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(400, lambda: save_config(self.cfg))

    # ---------- loop ----------
    def _tick(self):
        while not self.ui_queue.empty():
            self.ui_queue.get_nowait()()
        self.real_label.configure(text=f"{self.engine.real_clicks:,}")
        self.gen_label.configure(text=f"{self.engine.generated:,}")
        if self.engine.enabled:  # breathing glow on the power ring
            self._phase += 0.12
            k = (math.sin(self._phase) + 1) / 2
            self.power.configure(border_color=blend("#2E8F1E", "#B4FF9E", k))
        self.after(33, self._tick)

    def _on_close(self):
        save_config(self.cfg)
        self.led.restore()
        self.api.stop()
        self.engine.stop()
        self.hotkeys.stop()
        self.destroy()


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    App().mainloop()
