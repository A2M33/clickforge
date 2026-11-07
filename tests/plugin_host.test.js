// Runs the real plugin against a fake Stream Deck and a fake ClickForge app.
// Needs the plugin's dependency: npm ci --prefix streamdeck/com.a2m33.clickforge.sdPlugin
// Run with: node --test tests/plugin_host.test.js
const assert = require("node:assert");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { after, before, describe, it } = require("node:test");

const PLUGIN_DIR = path.join(__dirname, "..", "streamdeck", "com.a2m33.clickforge.sdPlugin");
const { WebSocketServer } = require(path.join(PLUGIN_DIR, "node_modules", "ws"));

const ACTION = "com.a2m33.clickforge";
const UUID = "fake-plugin-uuid";

// Stands in for the Stream Deck app: the plugin connects to it and registers.
class FakeStreamDeck {
  constructor() {
    this.messages = [];
    this.waiters = new Set();
  }

  async start() {
    this.server = new WebSocketServer({ host: "127.0.0.1", port: 0 });
    await new Promise((done) => this.server.once("listening", done));
    this.connected = new Promise((done) => {
      this.server.once("connection", (socket) => {
        this.socket = socket;
        socket.on("message", (raw) => {
          const msg = JSON.parse(raw);
          this.messages.push(msg);
          for (const check of this.waiters) check(msg);
        });
        done();
      });
    });
    return this.server.address().port;
  }

  send(event, context, action, extra = {}) {
    this.socket.send(JSON.stringify({ event, context, action: `${ACTION}.${action}`, ...extra }));
  }

  mark() {
    return this.messages.length;
  }

  since(mark) {
    return this.messages.slice(mark);
  }

  waitFor(predicate, since = 0, ms = 4000) {
    const hit = this.messages.slice(since).find(predicate);
    if (hit) return Promise.resolve(hit);
    return new Promise((resolve, reject) => {
      const check = (msg) => {
        if (!predicate(msg)) return;
        clearTimeout(timer);
        this.waiters.delete(check);
        resolve(msg);
      };
      const timer = setTimeout(() => {
        this.waiters.delete(check);
        reject(new Error(`no matching message within ${ms} ms`));
      }, ms);
      this.waiters.add(check);
    });
  }
}

// Stands in for ClickForge's localhost API.
class FakeApp {
  constructor() {
    this.state = { enabled: false, multiplier: 12 };
    this.posts = [];
  }

  listen(port = 0) {
    this.server = http.createServer((req, res) => {
      if (req.method === "POST") {
        this.posts.push({ url: req.url, header: req.headers["x-clickforge"] });
        if (req.url === "/toggle") this.state.enabled = !this.state.enabled;
        if (req.url === "/multiplier/up") this.state.multiplier += 1;
        if (req.url === "/multiplier/down") this.state.multiplier -= 1;
      }
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify(this.state));
    });
    return new Promise((done) => this.server.listen(port, "127.0.0.1", () => done(this.server.address().port)));
  }

  close() {
    this.server.closeAllConnections();
    return new Promise((done) => this.server.close(done));
  }
}

function svg(msg) {
  const image = msg.payload.image;
  return decodeURIComponent(image.slice(image.indexOf(",") + 1));
}

const imageFor = (context, ...words) => (msg) =>
  msg.event === "setImage" && msg.context === context && words.every((w) => svg(msg).includes(`>${w}<`));

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

describe("plugin against a fake Stream Deck", () => {
  const deck = new FakeStreamDeck();
  const app = new FakeApp();
  let appPort;
  let plugin;
  let exited;
  let appData;
  let stderr = "";

  before(async () => {
    appData = fs.mkdtempSync(path.join(os.tmpdir(), "clickforge-plugin-"));
    const deckPort = await deck.start();
    appPort = await app.listen();
    plugin = spawn(
      process.execPath,
      [path.join(PLUGIN_DIR, "bin", "plugin.js"), "-port", String(deckPort), "-pluginUUID", UUID,
        "-registerEvent", "registerPlugin", "-info", "{}"],
      { env: { ...process.env, CLICKFORGE_API: `http://127.0.0.1:${appPort}`, APPDATA: appData } }
    );
    plugin.stderr.on("data", (chunk) => (stderr += chunk));
    exited = new Promise((done) => plugin.once("exit", (code) => done(code)));
    await deck.connected;
  });

  after(async () => {
    plugin.kill();
    deck.server.close();
    if (app.server.listening) await app.close();
    fs.rmSync(appData, { recursive: true, force: true });
  });

  it("registers with the event and UUID it was given", async () => {
    const msg = await deck.waitFor((m) => m.event === "registerPlugin");
    assert.deepStrictEqual(msg, { event: "registerPlugin", uuid: UUID });
  });

  it("draws every key that appears, with the app's state", async () => {
    deck.send("willAppear", "t", "toggle");
    deck.send("willAppear", "p", "plus");
    deck.send("willAppear", "m", "minus");
    await deck.waitFor(imageFor("t", "Off", "×12"));
    await deck.waitFor(imageFor("p", "+", "×12"));
    await deck.waitFor(imageFor("m", "−", "×12"));
  });

  it("toggles the app on keyDown and shows On", async () => {
    const mark = deck.mark();
    deck.send("keyDown", "t", "toggle");
    await deck.waitFor(imageFor("t", "On", "×12"), mark);
    assert.deepStrictEqual(app.posts.at(-1), { url: "/toggle", header: "1" });
  });

  it("ignores keyUp", async () => {
    const mark = deck.mark();
    const posts = app.posts.length;
    deck.send("keyUp", "t", "toggle");
    await sleep(400);
    assert.strictEqual(app.posts.length, posts);
    assert.ok(!deck.since(mark).some((m) => m.event === "showAlert"));
  });

  it("changes the multiplier with the plus and minus keys", async () => {
    let mark = deck.mark();
    deck.send("keyDown", "p", "plus");
    await deck.waitFor(imageFor("p", "+", "×13"), mark);
    await deck.waitFor(imageFor("t", "On", "×13"), mark);
    mark = deck.mark();
    deck.send("keyDown", "m", "minus");
    await deck.waitFor(imageFor("m", "−", "×12"), mark);
  });

  it("follows a toggle made in the app itself", async () => {
    const mark = deck.mark();
    app.state.enabled = false; // the hotkey, a mouse button or the window
    await deck.waitFor(imageFor("t", "Off"), mark);
  });

  it("stops drawing a key once it disappears", async () => {
    deck.send("willDisappear", "m", "minus");
    await sleep(100);
    const mark = deck.mark();
    app.state.multiplier = 20;
    await deck.waitFor(imageFor("t", "Off", "×20"), mark);
    assert.ok(!deck.since(mark).some((m) => m.context === "m"));
  });

  it("shows Start once the app is gone", async () => {
    await app.close();
    await deck.waitFor(imageFor("t", "Start"), deck.mark());
  });

  it("alerts on toggle when it doesn't know where the app is", async () => {
    const mark = deck.mark();
    deck.send("keyDown", "t", "toggle");
    await deck.waitFor((m) => m.event === "showAlert" && m.context === "t", mark);
  });

  it("starts the app on toggle once the app has saved its path", async () => {
    fs.mkdirSync(path.join(appData, "ClickForge"));
    // Node with no script and no input exits at once, so it stands in for ClickForge.exe.
    fs.writeFileSync(path.join(appData, "ClickForge", "config.json"), JSON.stringify({ exe_path: process.execPath }));
    const mark = deck.mark();
    deck.send("keyDown", "t", "toggle");
    await deck.waitFor((m) => m.event === "showOk" && m.context === "t", mark);
  });

  it("alerts instead of starting a second copy when the app is only slow", async () => {
    const stalled = http.createServer(() => {}); // takes the connection, never answers
    await new Promise((done) => stalled.listen(appPort, "127.0.0.1", done));
    const mark = deck.mark();
    deck.send("keyDown", "t", "toggle");
    await deck.waitFor((m) => m.event === "showAlert" && m.context === "t", mark);
    assert.ok(!deck.since(mark).some((m) => m.event === "showOk"));
    stalled.closeAllConnections();
    await new Promise((done) => stalled.close(done));
  });

  it("picks the app back up when it starts again", async () => {
    await app.listen(appPort);
    await deck.waitFor(imageFor("t", "Off", "×20"), deck.mark());
  });

  it("exits when Stream Deck closes the connection", async () => {
    deck.socket.close();
    const code = await Promise.race([exited, sleep(3000).then(() => "still running")]);
    assert.strictEqual(code, 0);
    assert.strictEqual(stderr, "");
  });
});
