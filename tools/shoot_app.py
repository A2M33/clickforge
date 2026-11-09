"""Render docs/app.png and docs/app-dark.png from the real ClickForge window (Windows only).

The window is clickforge.App with the mouse/keyboard hooks, the HTTP API and the lock-key LED
swapped for stand-ins, so nothing on the machine changes while it runs. The picture is the
window's own pixels via PrintWindow, never a grab of the screen: a screen grab picks up
whatever covers the window, which can be private.

    pip install pillow
    python tools/shoot_app.py
"""
import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
sys.path.insert(0, str(ROOT))

PW_RENDERFULLCONTENT = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
CONFIG = {"multiplier": 12, "delay_ms": 10, "buttons": "left", "hotkey": ["mouse4"], "sound": True,
          "led": "scroll"}


class FakeEngine:
    def __init__(self, cfg, hotkeys=None):
        self.enabled = False
        self.multiplier = cfg["multiplier"]
        self.delay_ms = cfg["delay_ms"]
        self.buttons = cfg["buttons"]
        self.real_clicks = self.generated = 0
        self.own_hwnd = None

    def stop(self):
        pass


class FakeHotkeys:
    def __init__(self, *args, **kwargs):
        self.capturing = False

    def stop(self):
        pass


class FakeLed:
    def show(self, on): pass
    def use(self, mode): pass
    def restore(self): pass


class FakeApi:
    def __init__(self, *args, **kwargs): pass
    def stop(self): pass


def window_pixels(hwnd):
    """The visible window (frame included) as a PIL image, read from the window itself."""
    from PIL import Image

    user32, gdi32, dwm = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.dwmapi
    outer = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    visible = wintypes.RECT()
    dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(visible), ctypes.sizeof(visible))
    w, h = outer.right - outer.left, outer.bottom - outer.top

    win_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(win_dc)
    bmp = gdi32.CreateCompatibleBitmap(win_dc, w, h)
    gdi32.SelectObject(mem_dc, bmp)
    if not user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT):
        raise RuntimeError("PrintWindow failed")

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    info = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1,
                            biBitCount=32)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, win_dc)

    shot = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    # GetWindowRect includes the invisible resize border; keep only what Windows draws.
    return shot.crop((visible.left - outer.left, visible.top - outer.top,
                      visible.right - outer.left, visible.bottom - outer.top))


def shoot(theme, out):
    import customtkinter as ctk

    import clickforge

    clickforge.load_config = lambda: dict(CONFIG)
    clickforge.save_config = lambda cfg: True
    clickforge.ClickEngine = FakeEngine
    clickforge.HotkeyManager = FakeHotkeys
    clickforge.ApiServer = FakeApi
    clickforge.make_led = FakeLed

    ctk.set_appearance_mode(theme)
    clickforge.load_fonts()
    app = clickforge.App()
    app.engine.enabled = True  # the live state: cyan pad, "Turn off" (toggle() would also beep)
    app._refresh_power()
    app.attributes("-topmost", True)
    app.update()
    hwnd = clickforge.user32.GetAncestor(app.winfo_id(), clickforge.GA_ROOT)

    def capture():
        app.update()
        window_pixels(hwnd).save(out)
        print(out)
        app.destroy()

    app.after(800, capture)  # let CTk finish drawing and recolour the title bar
    app.mainloop()


def main():
    if len(sys.argv) == 3:
        shoot(sys.argv[1], Path(sys.argv[2]))
        return
    # One process per theme: CTk keeps appearance state per process.
    for theme, name in (("light", "app.png"), ("dark", "app-dark.png")):
        subprocess.run([sys.executable, __file__, theme, str(DOCS / name)], check=True)


if __name__ == "__main__":
    main()
