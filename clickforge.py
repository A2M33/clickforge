"""ClickForge - turn one real mouse click into many."""
import ctypes
import os
import queue
import sys
import threading
import winsound
from ctypes import wintypes

import customtkinter as ctk
from pynput import keyboard, mouse

from clickforge_core import (APP_NAME, ApiServer, ClickMultiplier, HotkeyState, LockLed, NumpadNames,
                             blend, claim_single_instance, display_combo, fit_scale, load_config, mouse_name,
                             run_all, run_pending, save_config, vk_name)

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

ASSETS = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets")

# A2M33 "trace" palette, (light, dark); CTk picks the one matching the system theme.
# Navy and grey are normal, cyan means the multiplier is live. Nothing else gets the signal colour.
GROUND = ("#f2f5fa", "#0b1222")
PANEL = ("#ffffff", "#111a2d")
INSET = ("#e4eaf3", "#18233a")
LINE = ("#cdd6e4", "#22304b")
TRACE = ("#9fb0c9", "#3a4d70")
INK = ("#0c1628", "#e6edf7")
INK_2 = ("#4a5a73", "#93a3bd")
SIGNAL = ("#0788ad", "#2fcbea")
LINK = ("#1f5bd8", "#6f9dff")


def mix(a, b, t):
    """Blend two (light, dark) colours, for hover states the palette has no token for."""
    return tuple(blend(x, y, t) for x, y in zip(a, b))


INSET_HOVER = mix(INSET, LINE, 0.45)
INK_HOVER = mix(INK, INK_2, 0.35)

# Archivo is bundled in assets/fonts and loaded privately at start (see load_fonts).
BODY = "Archivo"
MEDIUM = "Archivo Medium"
SEMIBOLD = "Archivo SemiBold"


def load_fonts():
    folder = os.path.join(ASSETS, "fonts")
    for name in sorted(os.listdir(folder)):
        if name.endswith(".ttf"):
            ctk.FontManager.load_font(os.path.join(folder, name))

MODIFIER_VKS = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "win": (0x5B, 0x5C)}

MODIFIERS = {
    keyboard.Key.ctrl: "ctrl", keyboard.Key.ctrl_l: "ctrl", keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.shift: "shift", keyboard.Key.shift_l: "shift", keyboard.Key.shift_r: "shift",
    keyboard.Key.alt: "alt", keyboard.Key.alt_l: "alt", keyboard.Key.alt_r: "alt",
    keyboard.Key.alt_gr: "alt",
    keyboard.Key.cmd: "win", keyboard.Key.cmd_l: "win", keyboard.Key.cmd_r: "win",
}

def key_name(key):
    if key is None:  # pynput sends None for a key it couldn't translate
        return None
    if isinstance(key, keyboard.Key):
        return key.name
    vk = getattr(key, "vk", None)
    if vk is not None:
        return vk_name(vk)
    return key.char.lower() if key.char else None


def key_vk(key):
    if isinstance(key, keyboard.Key):
        key = key.value
    return getattr(key, "vk", None)


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
        self._numpad = NumpadNames()
        self._listener = keyboard.Listener(
            on_press=self._press, on_release=self._release, win32_event_filter=self._filter)
        self._listener.start()

    def _filter(self, msg, data):
        if data.flags & LLKHF_INJECTED:
            return False
        self._numpad.seen(data.vkCode, data.flags)
        return True

    def _name(self, key):
        return self._numpad.name(key_vk(key), key_name(key))

    def _press(self, key):
        mod = MODIFIERS.get(key)
        if mod:
            self.mod_down(mod)
        else:
            self.key_down(self._name(key))

    def _release(self, key):
        mod = MODIFIERS.get(key)
        if mod:
            self.mod_up(mod)
        else:
            self.key_up(self._name(key))

    def stop(self):
        self._listener.stop()


class StatusPad(ctk.CTkFrame):
    """The status pad: a hollow grey ring when off, filled cyan while the multiplier is live."""

    def __init__(self, master, size=18, **kw):
        super().__init__(master, width=size, height=size, corner_radius=size // 2,
                         border_width=2, **kw)

    def show(self, live):
        self.configure(fg_color=SIGNAL if live else PANEL, border_color=SIGNAL if live else TRACE)


def rule(master):
    """A 1px divider between the rows of a panel."""
    # CTk draws nothing for a 1px-high fill, so the colour goes on the canvas background.
    return ctk.CTkFrame(master, height=1, corner_radius=0, fg_color=LINE, bg_color=LINE)


def section_label(master, text):
    return ctk.CTkLabel(master, text=text, text_color=INK_2, font=(MEDIUM, 14), height=20)


class Segmented(ctk.CTkFrame):
    """Option group in an inset well; the selected option is a raised panel."""

    def __init__(self, master, options, command):
        super().__init__(master, fg_color=INSET, corner_radius=4)
        self.buttons = {}
        for i, (key, text) in enumerate(options):
            self.grid_columnconfigure(i, weight=1, uniform="seg")
            b = ctk.CTkButton(self, text=text, height=32, width=20, corner_radius=3, border_width=1,
                              command=lambda k=key: command(k))
            b.grid(row=0, column=i, sticky="ew", padx=2, pady=2)
            self.buttons[key] = b

    def select(self, key):
        for k, b in self.buttons.items():
            active = k == key
            b.configure(fg_color=PANEL if active else INSET,
                        border_color=LINE if active else INSET,
                        hover_color=PANEL if active else INSET_HOVER,
                        text_color=INK if active else INK_2,
                        font=(MEDIUM if active else BODY, 15))


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
        self.configure(fg_color=GROUND)
        self.iconbitmap(os.path.join(ASSETS, "clickforge.ico"))

        self.ui_queue = queue.Queue()  # listener threads hand work to the Tk thread through this
        self.hotkeys = HotkeyManager(
            self.cfg["hotkey"],
            on_trigger=lambda: self.ui_queue.put(self.toggle),
            on_captured=lambda c: self.ui_queue.put(lambda: self._on_captured(c)))
        self.engine = ClickEngine(self.cfg, self.hotkeys)
        self._save_job = None
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
        pad = {"padx": 24}

        ctk.CTkLabel(self, text=APP_NAME, text_color=INK, font=(SEMIBOLD, 24), height=32
                     ).pack(anchor="w", pady=(18, 12), **pad)

        # On/off: the pad shows the state, the button switches it.
        status = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=8, border_width=1, border_color=LINE)
        status.pack(fill="x", **pad)
        status.grid_columnconfigure(1, weight=1)
        self.status_pad = StatusPad(status, size=22)
        self.status_pad.grid(row=0, column=0, rowspan=2, padx=(20, 14))
        self.state_label = ctk.CTkLabel(status, text="", text_color=INK, font=(SEMIBOLD, 31), height=36,
                                        anchor="w")
        self.state_label.grid(row=0, column=1, sticky="sw", pady=(16, 0))
        self.hint = ctk.CTkLabel(status, text="", text_color=INK_2, font=(BODY, 15), height=20, anchor="w")
        self.hint.grid(row=1, column=1, sticky="nw", pady=(0, 16))
        self.power = ctk.CTkButton(status, text="", width=120, height=44, corner_radius=4, border_width=1,
                                   font=(MEDIUM, 17), command=self.toggle)
        self.power.grid(row=0, column=2, rowspan=2, padx=(12, 20))

        panel = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=8, border_width=1, border_color=LINE)
        panel.pack(fill="x", pady=(12, 0), **pad)
        panel.grid_columnconfigure(0, weight=1)
        inner = {"padx": 20, "sticky": "ew"}

        # Multiplier
        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.grid(row=0, column=0, pady=(14, 0), **inner)
        self.mult_label = ctk.CTkLabel(row, text="", text_color=INK, font=(MEDIUM, 40), height=46)
        self.mult_label.pack(side="left")
        ctk.CTkLabel(row, text="clicks per click", text_color=INK_2, font=(BODY, 15), height=20
                     ).pack(side="left", padx=(10, 0), pady=(12, 0))
        for text, step in (("+", 1), ("−", -1)):
            ctk.CTkButton(row, text=text, width=36, height=36, corner_radius=4, border_width=1,
                          fg_color=PANEL, border_color=LINE, hover_color=INSET, text_color=INK,
                          font=(MEDIUM, 22),
                          command=lambda s=step: self._set_multiplier(self.engine.multiplier + s)
                          ).pack(side="right", padx=(8, 0))
        self.mult_slider = self._slider(panel, 1, 100, self._set_multiplier)
        self.mult_slider.grid(row=1, column=0, pady=(8, 18), padx=12, sticky="ew")  # knob overhangs the track
        rule(panel).grid(row=2, column=0, sticky="ew", padx=1)

        # Interval
        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.grid(row=3, column=0, pady=(12, 0), **inner)
        self.delay_label = ctk.CTkLabel(row, text="", text_color=INK, font=(MEDIUM, 31), height=38)
        self.delay_label.pack(side="left")
        ctk.CTkLabel(row, text="between clicks", text_color=INK_2, font=(BODY, 15), height=20
                     ).pack(side="left", padx=(10, 0), pady=(8, 0))
        self.cps_label = ctk.CTkLabel(row, text="", text_color=INK_2, font=(BODY, 15), height=20)
        self.cps_label.pack(side="right", pady=(8, 0))
        self.delay_slider = self._slider(panel, 1, 200, self._set_delay)
        self.delay_slider.grid(row=4, column=0, pady=(8, 18), padx=12, sticky="ew")
        rule(panel).grid(row=5, column=0, sticky="ew", padx=1)

        # Mouse button
        section_label(panel, "Mouse button").grid(row=6, column=0, padx=20, pady=(14, 6), sticky="w")
        self.button_seg = Segmented(panel, (("left", "Left"), ("right", "Right"), ("both", "Both")),
                                    self._set_buttons)
        self.button_seg.grid(row=7, column=0, pady=(0, 18), **inner)
        rule(panel).grid(row=8, column=0, sticky="ew", padx=1)

        # Keyboard LED: an on/off indicator that also works over exclusive-fullscreen games.
        section_label(panel, "Keyboard LED while on").grid(row=9, column=0, padx=20, pady=(14, 6), sticky="w")
        self.led_seg = Segmented(panel, (("off", "None"), ("scroll", "Scroll Lock"), ("num", "Num Lock")),
                                 self._set_led)
        self.led_seg.grid(row=10, column=0, pady=(0, 18), **inner)
        rule(panel).grid(row=11, column=0, sticky="ew", padx=1)

        # Shortcut
        section_label(panel, "Shortcut").grid(row=12, column=0, padx=20, pady=(14, 6), sticky="w")
        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.grid(row=13, column=0, pady=(0, 18), **inner)
        self.key_box = ctk.CTkFrame(row, height=40, corner_radius=4, fg_color=INSET, border_width=1,
                                    border_color=INSET)
        self.key_box.pack(side="left", fill="x", expand=True)
        self.key_box.pack_propagate(False)
        self.key_text = ctk.CTkLabel(self.key_box, text="", text_color=INK, font=(MEDIUM, 16), height=20)
        self.key_text.place(relx=0.5, rely=0.5, anchor="center")
        self.rebind_btn = ctk.CTkButton(row, text="Change", width=120, height=40, corner_radius=4,
                                        border_width=1, fg_color=PANEL, border_color=LINE, hover_color=INSET,
                                        text_color=INK, font=(MEDIUM, 15), command=self._start_rebind)
        self.rebind_btn.pack(side="right", padx=(10, 0))

        # Footer: live counters + sound
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", pady=(14, 20), **pad)
        ctk.CTkFrame(self, width=480, height=0, fg_color="transparent").pack()  # fixes window width
        self.real_label = self._stat(footer, "Real clicks")
        self.gen_label = self._stat(footer, "Generated")
        self.sound = ctk.CTkSwitch(footer, text="Sound", text_color=INK_2, font=(BODY, 15),
                                   fg_color=INSET, progress_color=INK, button_color=PANEL,
                                   button_hover_color=PANEL, border_width=0, switch_width=36, switch_height=18,
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
                             fg_color=INSET, progress_color=INK_2, button_color=INK,
                             button_hover_color=INK_HOVER, command=command)

    def _stat(self, master, name):
        box = ctk.CTkFrame(master, fg_color="transparent")
        box.pack(side="left", padx=(0, 28))
        value = ctk.CTkLabel(box, text="0", text_color=INK, font=(MEDIUM, 20), height=24)
        value.pack(anchor="w")
        ctk.CTkLabel(box, text=name, text_color=INK_2, font=(BODY, 13), height=16).pack(anchor="w")
        return value

    # ---------- state ----------
    def toggle(self):
        self.engine.enabled = not self.engine.enabled
        self._refresh_power()
        self.led.show(self.engine.enabled)
        if self.cfg["sound"]:
            freq = 1320 if self.engine.enabled else 660
            threading.Thread(target=winsound.Beep, args=(freq, 70), daemon=True).start()

    def _refresh_power(self):
        on = self.engine.enabled
        self.status_pad.show(on)
        self.state_label.configure(text="On" if on else "Off")
        if on:  # the pad is the one cyan thing; the button steps back
            self.power.configure(text="Turn off", fg_color=PANEL, hover_color=INSET, border_color=LINE,
                                 text_color=INK)
        else:  # primary action: ink, not cyan
            self.power.configure(text="Turn on", fg_color=INK, hover_color=INK_HOVER, border_color=INK,
                                 text_color=PANEL)

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
        self.delay_label.configure(text=f"{value} ms")
        self.cps_label.configure(text=f"about {1000 // value} a second")
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
        text = display_combo(self.cfg["hotkey"])
        self.key_text.configure(text=text)
        self.key_box.configure(border_color=INSET)
        self.hint.configure(text=f"Press {text} to toggle")
        self.rebind_btn.configure(text="Change")

    def _start_rebind(self):
        self.hotkeys.capturing = True
        self.key_text.configure(text="Press a key or mouse button")
        self.key_box.configure(border_color=LINK)  # focus colour: the box is waiting for input
        self.rebind_btn.configure(text="Esc to cancel")

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
    ctk.set_appearance_mode("system")  # light or dark, following Windows
    load_fonts()
    App().mainloop()
    release()
