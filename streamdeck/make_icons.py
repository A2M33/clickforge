"""Generates the PNG icons the Stream Deck manifest points to (run once after editing)."""
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(__file__), "com.a2m33.clickforge.sdPlugin", "imgs")
GREEN = (68, 214, 44, 255)
WHITE = (237, 237, 237, 255)
BG = (10, 10, 10, 255)
FONT = "C:/Windows/Fonts/bahnschrift.ttf"


def save(name, size, draw_fn, bg=None):
    for scale, suffix in ((1, ""), (2, "@2x")):
        s = size * scale
        big = s * 4  # supersample for smooth edges
        img = Image.new("RGBA", (big, big), bg or (0, 0, 0, 0))
        draw_fn(ImageDraw.Draw(img), big)
        img.resize((s, s), Image.LANCZOS).save(os.path.join(OUT, f"{name}{suffix}.png"))


def power(color, width_ratio=0.09):
    def fn(d, s):
        w = int(s * width_ratio)
        m = int(s * 0.18)
        d.arc((m, m, s - m, s - m), start=-60, end=240, fill=color, width=w)
        d.line((s // 2, int(s * 0.12), s // 2, s // 2), fill=color, width=w)
    return fn


def glyph(char, color, size_ratio=0.9):
    def fn(d, s):
        font = ImageFont.truetype(FONT, int(s * size_ratio))
        d.text((s / 2, s / 2), char, font=font, fill=color, anchor="mm")
    return fn


os.makedirs(OUT, exist_ok=True)
save("plugin", 256, power(GREEN), bg=BG)
save("category", 28, power(WHITE, 0.12))
save("action-toggle", 20, power(WHITE, 0.12))
save("action-plus", 20, glyph("+", WHITE))
save("action-minus", 20, glyph("−", WHITE))
save("key-toggle", 72, power(GREEN), bg=BG)
save("key-plus", 72, glyph("+", GREEN, 0.7), bg=BG)
save("key-minus", 72, glyph("−", GREEN, 0.7), bg=BG)
print("icons written to", OUT)
