"""Renders the Stream Deck PNGs and docs/streamdeck.png (run after editing an icon or bin/keys.js).

Needs Node, Pillow and Playwright with Microsoft Edge: pip install pillow playwright
The glyph is the clickforge one from the A2M33 icon family (a cursor with click echoes).
"""
import json
import pathlib
import subprocess
import tempfile

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
PLUGIN = HERE / "com.a2m33.clickforge.sdPlugin"
IMGS = PLUGIN / "imgs"
DOCS = HERE.parent / "docs"

TILE, EDGE, INK, AMBER = "#242b27", "#3d4741", "#e2e6e0", "#f0ab2a"
STROKE = f'fill="none" stroke="{INK}" stroke-width="30" stroke-linecap="round" stroke-linejoin="round"'
CURSOR = (f'<path d="M168 150 L 168 370 L 222 318 L 262 400 L 300 382 L 260 302 L 334 298 Z" {STROKE}/>'
          f'<path d="M150 112 L 128 90 M 196 104 V 74 M 112 158 H 82" {STROKE}/>')


def svg(view_box, body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" width="512" height="512">{body}</svg>'


# The plugin icon is the full app icon; list icons are the bare glyph, light on transparent.
TILE_ICON = svg("0 0 512 512", f'<rect x="8" y="8" width="496" height="496" rx="112" fill="{TILE}" '
                               f'stroke="{EDGE}" stroke-width="8"/>{CURSOR}'
                               f'<circle cx="400" cy="112" r="34" fill="{AMBER}"/>')
GLYPH_BOX = "38 66 340 340"
ICONS = {  # name: (svg, size at 1x)
    "plugin": (TILE_ICON, 256),
    "category": (svg(GLYPH_BOX, CURSOR), 28),
    "action-toggle": (svg(GLYPH_BOX, CURSOR), 20),
    "action-plus": (svg("86 86 340 340", f'<path d="M136 256 H 376 M 256 136 V 376" {STROKE}/>'), 20),
    "action-minus": (svg("86 86 340 340", f'<path d="M136 256 H 376" {STROKE}/>'), 20),
}

KEYS_JS = """
const { keySvg } = require(process.argv[1]);
const s = (enabled) => ({ enabled, multiplier: 12 });
console.log(JSON.stringify({
  defaults: { toggle: keySvg("toggle", null), plus: keySvg("plus", null), minus: keySvg("minus", null) },
  docs: [keySvg("toggle", s(true)), keySvg("toggle", s(false)), keySvg("toggle", null),
         keySvg("plus", s(true)), keySvg("minus", s(true))],
}));
"""


def key_svgs():
    out = subprocess.run(["node", "-e", KEYS_JS, str(PLUGIN / "bin" / "keys.js")],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


def shoot(page, html, path, size, transparent=True):
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(f'<meta charset="utf-8"><style>html,body{{margin:0;background:transparent}}'
                f'svg{{display:block}}</style>{html}')
    page.goto(pathlib.Path(f.name).as_uri())
    page.locator("body > :not(meta,style)").first.screenshot(path=str(path), omit_background=transparent)
    pathlib.Path(f.name).unlink()
    if size:
        Image.open(path).convert("RGBA").resize(size, Image.LANCZOS).save(path)


def main():
    keys = key_svgs()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1024, "height": 512})
        for name, (body, size) in ICONS.items():
            for scale, suffix in ((1, ""), (2, "@2x")):
                shoot(page, body, IMGS / f"{name}{suffix}.png", (size * scale, size * scale))
        for kind, body in keys["defaults"].items():
            for scale, suffix in ((1, ""), (2, "@2x")):
                shoot(page, body, IMGS / f"key-{kind}{suffix}.png", (72 * scale, 72 * scale))
        # README strip: the live keys as Stream Deck draws them, framed like the other A2M33 images.
        cells = "".join(f'<div style="width:144px;height:144px;border-radius:18px;overflow:hidden">{k}</div>'
                        for k in keys["docs"])
        strip = (f'<div style="display:inline-flex;gap:16px;padding:24px;background:{TILE};'
                 f'border:1px solid {EDGE};border-radius:6px">{cells}</div>')
        shoot(page, strip, DOCS / "streamdeck.png", None)
        browser.close()
    print("icons written to", IMGS, "and", DOCS / "streamdeck.png")


if __name__ == "__main__":
    main()
