// ClickForge Stream Deck plugin: talks to the app's localhost API and draws live key images.
const WebSocket = require("ws");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { appIsDown, createPoller } = require("./logic");

const API = process.env.CLICKFORGE_API || "http://127.0.0.1:47811"; // tests point it at a fake app
const CONFIG = path.join(process.env.APPDATA || "", "ClickForge", "config.json");
const ROUTES = { toggle: "/toggle", plus: "/multiplier/up", minus: "/multiplier/down" };

const GREEN = "#44D62C";
const RED = "#FF3B3B";
const MUTED = "#7A7A7A";

// Stream Deck launches us with: -port P -pluginUUID U -registerEvent E -info JSON
const args = {};
for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^-/, "")] = process.argv[i + 1];

const contexts = new Map(); // key context -> action kind (toggle | plus | minus)
let state = null; // { enabled, multiplier } or null when the app is not running
let launchedAt = 0;

const ws = new WebSocket(`ws://127.0.0.1:${args.port}`);
const send = (msg) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify(msg));

ws.on("open", () => send({ event: args.registerEvent, uuid: args.pluginUUID }));
ws.on("close", () => process.exit(0));
ws.on("error", (err) => console.error("Stream Deck connection failed:", err.message)); // "close" follows
ws.on("message", (raw) => {
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch {
    return;
  }
  const kind = msg.action && msg.action.split(".").pop();
  if (msg.event === "willAppear") {
    contexts.set(msg.context, kind);
    render(msg.context);
    poller.tick(); // the state may be old (keys were hidden) or unknown (just started)
  } else if (msg.event === "willDisappear") {
    contexts.delete(msg.context);
  } else if (msg.event === "keyDown") {
    onKey(msg.context, kind);
  }
});

async function call(method, route) {
  const res = await fetch(API + route, {
    method,
    headers: { "X-ClickForge": "1" },
    signal: AbortSignal.timeout(800),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function onKey(context, kind) {
  poller.invalidate();
  try {
    const next = await call("POST", ROUTES[kind]);
    poller.invalidate();
    setState(next);
  } catch (err) {
    if (kind === "toggle" && appIsDown(err) && launchApp()) {
      send({ event: "showOk", context });
    } else {
      send({ event: "showAlert", context });
    }
  }
}

function launchApp() {
  if (Date.now() - launchedAt < 10000) return true; // already starting
  try {
    const exe = JSON.parse(fs.readFileSync(CONFIG, "utf8")).exe_path;
    if (!exe || !fs.existsSync(exe)) return false;
    const child = spawn(exe, [], { detached: true, stdio: "ignore", cwd: path.dirname(exe) });
    child.on("error", () => {}); // a failed start arrives here, not in the catch below
    child.unref();
    launchedAt = Date.now();
    return true;
  } catch {
    return false;
  }
}

function setState(next) {
  if (JSON.stringify(next) === JSON.stringify(state)) return;
  state = next;
  for (const context of contexts.keys()) render(context);
}

// Poll so the keys follow toggles made by hotkey, mouse button or the app window.
const poller = createPoller(() => call("GET", "/state"), setState);
setInterval(() => {
  if (contexts.size) poller.tick();
}, 300);

function render(context) {
  const svg = keySvg(contexts.get(context));
  send({
    event: "setImage",
    context,
    payload: { image: "data:image/svg+xml;charset=utf8," + encodeURIComponent(svg), target: 0 },
  });
}

function text(x, y, size, fill, value, weight = 600) {
  return `<text x="${x}" y="${y}" font-family="Bahnschrift, Segoe UI, sans-serif" font-size="${size}" font-weight="${weight}" fill="${fill}" text-anchor="middle">${value}</text>`;
}

function keySvg(kind) {
  const bg = `<rect width="144" height="144" fill="#0A0A0A"/>`;
  const wrap = (body) => `<svg xmlns="http://www.w3.org/2000/svg" width="144" height="144" viewBox="0 0 144 144">${bg}${body}</svg>`;

  if (!state) {
    if (kind !== "toggle") return wrap(text(72, 92, 64, "#333", kind === "plus" ? "+" : "−"));
    return wrap(
      `<circle cx="72" cy="62" r="40" fill="none" stroke="#333" stroke-width="5"/>` +
        `<path d="M72 38 v22" stroke="${MUTED}" stroke-width="6" stroke-linecap="round"/>` +
        `<path d="M56 48 a24 24 0 1 0 32 0" fill="none" stroke="${MUTED}" stroke-width="6" stroke-linecap="round"/>` +
        text(72, 126, 18, MUTED, "START")
    );
  }

  const mult = `×${state.multiplier}`;
  if (kind === "toggle") {
    const on = state.enabled;
    const color = on ? GREEN : RED;
    const glow = on
      ? `<defs><radialGradient id="g"><stop offset="0.55" stop-color="${GREEN}" stop-opacity="0.35"/><stop offset="1" stop-color="${GREEN}" stop-opacity="0"/></radialGradient></defs><circle cx="72" cy="60" r="62" fill="url(#g)"/>`
      : "";
    return wrap(
      glow +
        `<circle cx="72" cy="60" r="44" fill="${on ? GREEN : "#141414"}" stroke="${color}" stroke-width="5"/>` +
        text(72, 74, 38, on ? "#0A0A0A" : color, on ? "ON" : "OFF", 700) +
        text(72, 130, 22, on ? GREEN : MUTED, mult)
    );
  }
  return wrap(
    `<rect x="10" y="10" width="124" height="124" rx="22" fill="#141414" stroke="#2A2A2A" stroke-width="3"/>` +
      text(72, 84, 72, GREEN, kind === "plus" ? "+" : "−", 700) +
      text(72, 122, 22, "#EDEDED", mult)
  );
}
