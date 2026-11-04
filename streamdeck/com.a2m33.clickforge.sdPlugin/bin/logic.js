// The plugin's decisions, kept apart from Stream Deck and the network so `node --test` can run them.

// True only when nothing listens on the API port. A timeout means a slow app that is still running,
// and starting it again from there is how a second copy used to appear.
function appIsDown(err) {
  const cause = err && err.cause;
  if (!cause) return false;
  const codes = [cause.code, ...(cause.errors || []).map((e) => e.code)];
  return codes.includes("ECONNREFUSED");
}

// Runs one state read at a time. `invalidate` throws away a read already in flight, which matters
// after a key press: that older answer could otherwise overwrite the fresh one and flicker the key.
function createPoller(readState, onState, maxMisses = 3) {
  let busy = false;
  let misses = 0;
  let epoch = 0;
  return {
    async tick() {
      if (busy) return;
      busy = true;
      const started = epoch;
      try {
        const next = await readState();
        if (started === epoch) {
          misses = 0;
          onState(next);
        }
      } catch {
        // One slow answer shouldn't flip every key to START.
        if (started === epoch && ++misses >= maxMisses) onState(null);
      } finally {
        busy = false;
      }
    },
    invalidate() {
      epoch += 1;
      misses = 0;
    },
  };
}

module.exports = { appIsDown, createPoller };
