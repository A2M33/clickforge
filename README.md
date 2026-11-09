![ClickForge banner: the real window in dark mode, switched on at 12 clicks per click with a cyan status pad. One callout says each real click sends 12; another points at the Mouse 4 shortcut, which you can also set to a key or toggle from the Stream Deck.](docs/banner.png)

<p>
  <a href="https://github.com/A2M33/clickforge/actions/workflows/test.yml"><img alt="tests" src="https://img.shields.io/github/actions/workflow/status/A2M33/clickforge/test.yml?label=tests&style=flat-square&labelColor=111a2d&color=22304b"></a>
  <img alt="Windows 10 and 11" src="https://img.shields.io/badge/windows-10%20%7C%2011-22304b?style=flat-square&labelColor=111a2d&color=22304b">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-22304b?style=flat-square&labelColor=111a2d&color=22304b">
  <a href="https://github.com/A2M33/clickforge/blob/main/LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-22304b?style=flat-square&labelColor=111a2d&color=22304b"></a>
</p>

ClickForge turns one mouse click into many. Set it to 12, click once, and Windows receives 12 clicks. You switch it on and off with a key, a mouse side button, or a Stream Deck key.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/app-dark.png">
    <img src="docs/app.png" width="360" alt="ClickForge window switched on: the status pad is cyan, 12 clicks per click, 10 ms between clicks, left button, Scroll Lock as the LED, Mouse 4 as the shortcut">
  </picture>
</p>

## Features

- **Click multiplier** from ×1 to ×100, with an adjustable gap between clicks (1 to 200 ms).
- **Left, right, or both** mouse buttons.
- **Rebindable toggle**: any key, a key combo like `Ctrl + G`, or a mouse button (middle, Mouse 4, Mouse 5). A bound mouse button stops doing its normal job, so Mouse 4 won't trigger "Back" in your browser.
- **Works mid-game**: the shortcut fires while you hold Shift, W, or any other key.
- **Keyboard LED indicator**: Scroll Lock (or Num Lock) lights up while the multiplier is on. You can see it even in exclusive-fullscreen games, where no on-screen overlay can draw.
- **Stream Deck plugin** with live keys (below).
- **Sound cue** on toggle, and a live counter of real and generated clicks.
- **Light and dark**: the window follows your Windows theme.

Clicks on the ClickForge window itself stay single, so the Turn on button doesn't fire twelve times. A burst also stops when your cursor reaches the window, so you can get to Turn off during a long one.

## Stream Deck

<p align="center">
  <img src="docs/streamdeck.png" width="560" alt="Stream Deck keys on navy tiles: On ×12 with a cyan pad, Off ×12 with a grey ring, Start with a red pad, then plus and minus at ×12">
</p>

The plugin adds three actions: **Toggle**, **Clicks +1** and **Clicks -1**. The keys show the current state and multiplier, and they update when you toggle from the keyboard or the app too. If ClickForge isn't running, the Toggle key shows Start with a red pad, and pressing it launches the app.

## Install

**App:** download `ClickForge.exe` from [Releases](../../releases) and run it. Settings live in `%APPDATA%\ClickForge\config.json`. If you start it twice, the second start brings the open window forward.

**Stream Deck plugin:** download `com.a2m33.clickforge.streamDeckPlugin` from Releases and double-click it. Then drag the ClickForge actions onto your keys. Run `ClickForge.exe` once first so the START key knows where the app lives.

## Build from source

Requires Windows 10/11 and Python 3.10+.

```bat
pip install -r requirements.txt
python clickforge.py
```

`build.bat` packs a single `dist\ClickForge.exe` with PyInstaller.

For the plugin, you need Node.js. `streamdeck\install.ps1` installs its one dependency (`ws`), copies it into Stream Deck and restarts the Stream Deck app.

`clickforge_core.py` holds the logic with no hooks or UI, so most tests run on any OS. The tests for the hook glue in `clickforge.py` run on Windows only. `plugin_host.test.js` starts the real plugin against a fake Stream Deck and a fake app, so it needs `ws` installed first:

```bat
pip install -r requirements-dev.txt
python -m pytest
npm ci --prefix streamdeck\com.a2m33.clickforge.sdPlugin
node --test tests/plugin_logic.test.js tests/plugin_host.test.js
```

The README images come from the code too. `python tools/shoot_app.py` opens the real window with stand-in hooks and saves its own pixels, `python streamdeck/make_icons.py` draws the keys, and `python tools/banner/render.py` builds the banner (Pillow, Playwright and Microsoft Edge).

## How it works

- A low-level Windows mouse hook (`pynput`) watches real clicks. On release, a worker thread sends the extra clicks with `SendInput`.
- Windows marks synthetic input with the `LLMHF_INJECTED` flag. The hook drops flagged events, so generated clicks never trigger more clicks.
- The UI uses `customtkinter`, with the Archivo font bundled in `assets/fonts`. Hook threads pass work to the Tk thread through a queue, since Tk isn't thread-safe.
- The app serves a small HTTP API on `127.0.0.1:47811` (`GET /state`, `POST /toggle`, `POST /multiplier/up|down`). POST requests need an `X-ClickForge` header. A browser can't add that header cross-origin without a CORS preflight, and the app never answers one, so a web page can't toggle the clicker. The app also refuses any `Host` other than `127.0.0.1` and `localhost`, which stops a site from pointing its own domain at your machine to get around that.
- The Stream Deck plugin (Node.js) polls that API and draws each key as an SVG.

## Notes

- Games with anti-cheat may detect or block synthetic input. Check a game's rules before you use ClickForge in it.
- To reach apps that run as administrator, run ClickForge as administrator too.
- If you pick the Num Lock indicator, ClickForge turns Num Lock off while the multiplier is off, and the number pad sends arrows and Home/End instead of digits. A shortcut bound to a number pad key keeps working.

## License

[MIT](LICENSE). Archivo is under the [SIL Open Font License](assets/fonts/OFL.txt).
