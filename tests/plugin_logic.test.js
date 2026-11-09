// Run with: node --test tests/plugin_logic.test.js
const assert = require("node:assert");
const http = require("node:http");
const { test } = require("node:test");
const { appIsDown, createPoller } = require("../streamdeck/com.a2m33.clickforge.sdPlugin/bin/logic");

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

test("a closed port counts as the app being down", async () => {
  const server = http.createServer();
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  const { port } = server.address();
  await new Promise((done) => server.close(done));
  const err = await fetch(`http://127.0.0.1:${port}/state`).catch((e) => e);
  assert.strictEqual(appIsDown(err), true);
});

test("a timeout does not count as the app being down", async () => {
  const server = http.createServer(() => {}); // accepts and never answers, like a stalled app
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  const { port } = server.address();
  const err = await fetch(`http://127.0.0.1:${port}/state`, { signal: AbortSignal.timeout(50) }).catch((e) => e);
  server.closeAllConnections();
  await new Promise((done) => server.close(done));
  assert.strictEqual(appIsDown(err), false); // this used to start a second copy of the app
});

test("an HTTP error does not count as the app being down", () => {
  assert.strictEqual(appIsDown(new Error("HTTP 403")), false);
  assert.strictEqual(appIsDown(undefined), false);
});

test("only one read runs at a time", async () => {
  const reads = [];
  const poller = createPoller(
    () => {
      const d = deferred();
      reads.push(d);
      return d.promise;
    },
    () => {}
  );
  const first = poller.tick();
  await poller.tick(); // these used to pile up behind a slow app
  await poller.tick();
  assert.strictEqual(reads.length, 1);
  reads[0].resolve({});
  await first;
  poller.tick();
  assert.strictEqual(reads.length, 2);
  reads[1].resolve({});
});

test("a key press discards the read in flight", async () => {
  const states = [];
  const read = deferred();
  const poller = createPoller(() => read.promise, (s) => states.push(s));
  const tick = poller.tick();
  poller.invalidate(); // the key press got a newer answer from the app
  read.resolve({ enabled: false });
  await tick;
  assert.deepStrictEqual(states, []); // this old answer used to flip the key back
});

test("a single failed read keeps the last state", async () => {
  const states = [];
  let fail = true;
  const poller = createPoller(
    async () => {
      if (fail) throw new Error("slow");
      return { enabled: true };
    },
    (s) => states.push(s)
  );
  await poller.tick();
  await poller.tick();
  assert.deepStrictEqual(states, []);
  fail = false;
  await poller.tick();
  assert.deepStrictEqual(states, [{ enabled: true }]);
});

test("repeated failures report the app as gone", async () => {
  const states = [];
  const poller = createPoller(async () => {
    throw new Error("down");
  }, (s) => states.push(s));
  await poller.tick();
  await poller.tick();
  await poller.tick();
  assert.deepStrictEqual(states, [null]);
});

const { keySvg } = require("../streamdeck/com.a2m33.clickforge.sdPlugin/bin/keys");
const padFill = (svg) => svg.match(/<circle [^>]*fill="(#[0-9a-f]{6})"/)[1];

test("the toggle key's pad is cyan when on, a grey ring when off, red when the app is closed", () => {
  assert.strictEqual(padFill(keySvg("toggle", { enabled: true, multiplier: 12 })), "#2fcbea");
  const off = keySvg("toggle", { enabled: false, multiplier: 12 });
  assert.strictEqual(padFill(off), "#111a2d");
  assert.match(off, /<circle [^>]*stroke="#3a4d70"/);
  assert.strictEqual(padFill(keySvg("toggle", null)), "#ff6b6b");
});

test("plus and minus keys show the multiplier, and nothing while the app is closed", () => {
  assert.match(keySvg("plus", { enabled: false, multiplier: 7 }), />×7</);
  assert.doesNotMatch(keySvg("minus", null), /×/);
});
