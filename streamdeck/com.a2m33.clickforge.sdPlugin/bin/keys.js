// Key images in the A2M33 "trace" palette. The key screen is always dark, so only the dark tokens are used.
// The pad shows the state: a grey ring = off, filled cyan = multiplying, red = the app isn't running.

const C = {
  panel: "#111a2d",
  trace: "#3a4d70",
  ink: "#e6edf7",
  ink2: "#93a3bd",
  signal: "#2fcbea",
  fault: "#ff6b6b",
};
// Stream Deck draws SVG text with installed fonts only; Segoe UI ships with Windows 10 and 11.
const FONT = "Archivo, Segoe UI, sans-serif";

function text(x, y, size, fill, value, weight = 500) {
  return `<text x="${x}" y="${y}" font-family="${FONT}" font-size="${size}" font-weight="${weight}" fill="${fill}">${value}</text>`;
}

function pad(fill, ring) {
  const edge = ring ? ` stroke="${ring}" stroke-width="3"` : "";
  return `<circle cx="38" cy="40" r="12" fill="${fill}"${edge}/>`;
}

const sign = (kind, color) => text(22, 56, 48, color, kind === "plus" ? "+" : "−");

const reading = (fill, multiplier) => text(24, 118, 48, fill, `×${multiplier}`);

// kind: toggle | plus | minus. state: { enabled, multiplier }, or null while the app isn't running.
function keySvg(kind, state) {
  const wrap = (body) =>
    `<svg xmlns="http://www.w3.org/2000/svg" width="144" height="144" viewBox="0 0 144 144">` +
    `<rect width="144" height="144" fill="${C.panel}"/>${body}</svg>`;

  if (kind === "toggle") {
    if (!state) return wrap(pad(C.fault) + text(62, 50, 30, C.ink, "Start", 600) + text(24, 114, 20, C.ink2, "App closed"));
    const on = state.enabled;
    return wrap(
      pad(on ? C.signal : C.panel, on ? null : C.trace) +
        text(62, 50, 30, on ? C.ink : C.ink2, on ? "On" : "Off", 600) +
        reading(on ? C.ink : C.ink2, state.multiplier)
    );
  }
  if (!state) return wrap(sign(kind, C.ink2));
  return wrap(sign(kind, C.ink) + reading(C.ink, state.multiplier));
}

module.exports = { keySvg };
