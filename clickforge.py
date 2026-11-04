"""ClickForge - turn one real mouse click into many."""
import ctypes
import math
import queue
import sys
import threading
import winsound
from ctypes import wintypes

import customtkinter as ctk
from pynput import keyboard, mouse

from clickforge_core import (APP_NAME, ApiServer, ClickMultiplier, HotkeyState, LockLed, blend,
                             claim_single_instance, fit_scale, format_combo, load_config, mouse_name, run_all, run_pending,
                             save_config, vk_name)

# Windows low-level hook flags: set on events produced by SendInput (our own clicks).
LLMHF_INJECTED = 0x01
LLKHF_INJECTED = 0x10
GA_ROOT = 2
SW_RESTORE = 9
MONITOR_DEFAULTTOPRIMARY = 1
TITLE_BAR = 40  # room for the window frame, at 100% display scaling

user32 = ctypes.WinDLL("user32")  # our own instance: argtypes set here don't reach pynput
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
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

MODIFIER_VKS = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "win": (0x5B, 0x5C)}

MODIFIERS = {
    keyboard.Key.ctrl: "ctrl", keyboard.Key.ctrl_l: "ctrl", keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.shift: "shift", keyboard.Key.shift_l: "shift", keyboard.Key.shift_r: "shift",
    keyboard.Key.alt: "alt", keyboard.Key.alt_l: "alt", keyboard.Key.alt_r: "alt",
    keyboard.Key.alt_gr: "alt",
    keyboard.Key.cmd: "win", keyboard.Key.cmd_l: "win", keyboard.Key.cmd_r: "win",
}

def key_name(key):
    if isinstance(key, keyboard.Key):
        return key.name
    vk = getattr(key, "vk", None)
    if vk is not None:
        return vk_name(vk)
    return key.char.lower() if key.char else None


class ClickEngine(ClickMultiplier):
    """Connects ClickMultiplier to the real mouse: a low-level hook in, SendInput out."""

    def __init__(self, cfg, hotkeys=None):
        self._mouse = mouse.Controller()
        super().__init__(cfg, lambda button: self._mouse.click(mouse.Button[button]),
                         is_own_window=self._is_own_window, cursor=lambda: self._mouse.position)
        self.hotkeys = hotkeys
        self.own_hwnd = None
        self.start_worker()
        self._listener = mouse.Listener(on_click=self._on_click, win32_event_filter=self._filter)
        self._listener.start()

    def _filter(self, msg, data):
        # Returning False hides the event from on_click; our own clicks must never re-trigger.
        if data.flags & LLMHF_INJECTED:
            return False
        name, pressed = mouse_name(msg, data.mouseData)
        if name and self.hotkeys and self.hotkeys.mouse(name, pressed):
            # A bound mouse button is swallowed system-wide (no browser "Back" on mouse 4).
            self._listener.suppress_event()
        return True

    def _is_own_window(self, x, y):
        hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        return bool(hwnd) and user32.GetAncestor(hwnd, GA_ROOT) == self.own_hwnd

    def _on_click(self, x, y, button, pressed):
        self.on_click(x, y, button.name, pressed)

    def stop(self):
        self._listener.stop()
        self.stop_worker()


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def work_area(hwnd):
    """(left, top, right, bottom) of the window's monitor, without the taskbar."""
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTOPRIMARY)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    area = info.rcWork
    return area.left, area.top, area.right, area.bottom


def mod_is_down(mod):
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in MODIFIER_VKS[mod])


class HotkeyManager(HotkeyState):
    """Feeds HotkeyState from a low-level keyboard hook."""

    def __init__(self, combo, on_trigger, on_captured):
        super().__init__(combo, on_trigger, on_captured, mod_is_down=mod_is_down)
        self._listener = keyboard.Listener(
            on_press=self._press, on_release=self._release,
            win32_event_filter=lambda msg, data: not (data.flags & LLKHF_INJECTED))
        self._listener.start()

    def _press(self, key):
        mod = MODIFIERS.get(key)
        if mod:
            self.mod_down(mod)
        else:
            self.key_down(key_name(key))

    def _release(self, key):
        mod = MODIFIERS.get(key)
        if mod:
            self.mod_up(mod)
        else:
            self.key_up(key_name(key))

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


LOCK_KEYS = {"scroll": (0x91, keyboard.Key.scroll_lock), "num": (0x90, keyboard.Key.num_lock)}


def make_led():
    controller = keyboard.Controller()
    return LockLed(read_state=lambda mode: bool(user32.GetKeyState(LOCK_KEYS[mode][0]) & 1),
                   tap=lambda mode: controller.tap(LOCK_KEYS[mode][1]))


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
        self._shown_counts = None
        self.led = make_led()

        self._build()
        self.update_idletasks()
        self.engine.own_hwnd = user32.GetAncestor(self.winfo_id(), GA_ROOT)
        self._fit_to_screen()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_power()
        if getattr(sys, "frozen", False):  # lets the Stream Deck plugin launch the app
            self._queue_save("exe_path", sys.executable)
        self.api = ApiServer(
            {
                "/toggle": self.toggle,
                "/multiplier/up": lambda: self._set_multiplier(self.engine.multiplier + 1),
                "/multiplier/down": lambda: self._set_multiplier(self.engine.multiplier - 1),
            },
            self.api_state, self.ui_queue.put)
        self._tick()

    def api_state(self):
        return {"enabled": self.engine.enabled, "multiplier": self.engine.multiplier}

    # ---------- layout ----------
    def _fit_to_screen(self):
        """Shrink the layout on short screens and open in the middle of the work area.

        It's a fixed-size window, so anything past the bottom edge could never be reached.
        """
        left, top, right, bottom = work_area(self.engine.own_hwnd)
        frame = round(TITLE_BAR * self._get_window_scaling())
        scale = fit_scale(self.winfo_reqheight() + frame, bottom - top)
        if scale < 1:
            ctk.set_widget_scaling(scale)
            # Rescaling pins the window to CTk's 600x500 default; give the size back to the layout.
            self.wm_minsize(1, 1)
            self.wm_maxsize(self.winfo_screenwidth(), self.winfo_screenheight())
            self.wm_geometry("")
            self.update_idletasks()
        x = left + max(0, (right - left - self.winfo_reqwidth()) // 2)
        y = top + max(0, (bottom - top - self.winfo_reqheight() - frame) // 2)
        self.wm_geometry(f"+{x}+{y}")

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
        run_pending(self.ui_queue)
        counts = (self.engine.real_clicks, self.engine.generated)
        if counts != self._shown_counts:  # this runs 30 times a second, often over a game
            self._shown_counts = counts
            self.real_label.configure(text=f"{counts[0]:,}")
            self.gen_label.configure(text=f"{counts[1]:,}")
        if self.engine.enabled:  # breathing glow on the power ring
            self._phase += 0.12
            k = (math.sin(self._phase) + 1) / 2
            self.power.configure(border_color=blend("#2E8F1E", "#B4FF9E", k))
        self.after(33, self._tick)

    def _on_close(self):
        run_all([
            self.engine.stop,  # first: a running burst must not outlive the window
            self.hotkeys.stop,
            lambda: save_config(self.cfg),
            self.led.restore,
            self.api.stop,
        ])
        self.destroy()


def show_running_window():
    # Tk's window class narrows this down: a folder called ClickForge has the same title.
    hwnd = user32.FindWindowW("TkTopLevel", APP_NAME)
    if hwnd:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)


if __name__ == "__main__":
    release = claim_single_instance()
    if release is None:
        show_running_window()
        sys.exit(0)
    ctk.set_appearance_mode("dark")
    App().mainloop()
    release()
