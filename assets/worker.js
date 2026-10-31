/* Web Worker: runs the Python poker engine (poker_bot) inside Pyodide so the
   page stays responsive while the Monte Carlo simulation runs. */

const PYODIDE_SOURCES = [
  "https://cdn.jsdelivr.net/npm/pyodide@0.28.3/",
  "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/",
];
const PY_FILES = [
  "__init__.py", "cards.py", "evaluator.py", "preflop_data.py", "ranges.py",
  "equity.py", "game.py", "tracker.py", "advisor.py", "session.py", "api.py",
];

let api = null;
let readyPromise = null;

async function init(opts) {
  const sources = opts && opts.pyodide ? [opts.pyodide] : PYODIDE_SOURCES;
  const version = (opts && opts.version) || "dev";
  postMessage({ type: "progress", text: "Downloading the Python runtime…" });
  let pyodide = null;
  let lastErr = null;
  for (const base of sources) {
    try {
      importScripts(base + "pyodide.js");
      pyodide = await loadPyodide({ indexURL: base });
      break;
    } catch (err) {
      lastErr = err;
    }
  }
  if (!pyodide) throw lastErr || new Error("Pyodide could not be loaded");
  postMessage({ type: "progress", text: "Loading the poker engine…" });
  pyodide.FS.mkdirTree("/home/pyodide/poker_bot");
  const root = (opts && opts.root) || "../";
  await Promise.all(PY_FILES.map(async (f) => {
    const res = await fetch(root + "poker_bot/" + f + "?v=" + version);
    if (!res.ok) throw new Error("Could not load poker_bot/" + f + " (" + res.status + ")");
    pyodide.FS.writeFile("/home/pyodide/poker_bot/" + f, await res.text());
  }));
  pyodide.runPython("import sys\nif '/home/pyodide' not in sys.path: sys.path.insert(0, '/home/pyodide')");
  postMessage({ type: "progress", text: "Warming up…" });
  api = pyodide.pyimport("poker_bot.api");
  return true;
}

self.onmessage = async (e) => {
  const msg = e.data || {};
  if (msg.type === "init") {
    readyPromise = init(msg.opts).then(
      () => postMessage({ type: "ready" }),
      (err) => postMessage({ type: "fatal", error: String(err && err.message || err) })
    );
    return;
  }
  if (msg.type === "call") {
    try {
      await readyPromise;
      const out = api.call(msg.method, JSON.stringify(msg.args || {}));
      postMessage({ type: "result", id: msg.id, result: out });
    } catch (err) {
      postMessage({ type: "result", id: msg.id, result: JSON.stringify({ ok: false, error: String(err && err.message || err) }) });
    }
  }
};
