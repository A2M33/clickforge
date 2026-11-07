// ClickForge Stream Deck plugin: talks to the app's localhost API and draws live key images.
const WebSocket = require("ws");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { appIsDown, createPoller } = require("./logic");
const { keySvg } = require("./keys");

const API = process.env.CLICKFORGE_API || "http://127.0.0.1:47811"; // tests point it at a fake app
const CONFIG = path.join(process.env.APPDATA || "", "ClickForge", "config.json");
const ROUTES = { toggle: "/toggle", plus: "/multiplier/up", minus: "/multiplier/down" };

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
  const svg = keySvg(contexts.get(context), state);
  send({
    event: "setImage",
    context,
    payload: { image: "data:image/svg+xml;charset=utf8," + encodeURIComponent(svg), target: 0 },
  });
}
