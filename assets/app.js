/* Poker Help: web front end.
   All poker logic lives in the Python package `poker_bot`, which runs in a
   Web Worker through Pyodide.  This file renders state and forwards input. */
(() => {
  "use strict";

  const VERSION = "2.0.0";
  const RANKS = "23456789TJQKA";
  const SUITS = "cdhs";
  const SUIT_SYM = { c: "♣", d: "♦", h: "♥", s: "♠" };
  const STORE_KEY = "pokerhelp.session.v1";
  const PREF_KEY = "pokerhelp.prefs.v2";
  const STREET_NAMES = { preflop: "Pre-flop", flop: "Flop", turn: "Turn", river: "River", showdown: "Showdown" };
  const KIND_LABEL = {
    value: "Value bet", bluff: "Bluff", semibluff: "Semi-bluff", call: "Call", fold: "Fold", check: "Check",
    trap: "Check to trap", open: "Open raise", limp: "Limp",
  };
  const PROFILE_SHORT = { nit: "NIT", rock: "ROCK", tag: "TAG", lag: "LAG", maniac: "MANIAC", station: "STATION",
    loose_passive: "LOOSE", reg: "REG", reg_passive: "REG" };

  // ------------------------------------------------------------ storage
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); return true; } catch (e) { return false; } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };
  function safeJSON(t) { try { return t ? JSON.parse(t) : null; } catch (e) { return null; } }

  const S = {
    ready: false, state: null, advice: null, adviceKey: null, adviceBusy: false, busy: false,
    view: "table",
    prefs: Object.assign({ budget: 1.5, detail: false, hud: true, tip: true }, safeJSON(store.get(PREF_KEY)) || {}),
    setup: { players: 6, hero: 0, sb: 0.5, bb: 1, stack: 100, names: [], preset: "6max" },
    pick: [], pickText: "", raiseTo: null, raiseKey: null, potWinners: null, potKey: null,
    modal: null, stats: null, statsLoading: false, histOpen: new Set(),
  };
  function savePrefs() { store.set(PREF_KEY, JSON.stringify(S.prefs)); }

  // ------------------------------------------------------------- worker
  const params = new URLSearchParams(location.search);
  if (params.get("reset") === "1") store.del(STORE_KEY);
  const worker = new Worker("assets/worker.js?v=" + VERSION);
  let nextId = 1;
  const pending = new Map();
  worker.onmessage = (e) => {
    const m = e.data || {};
    if (m.type === "progress") {
      const t = document.getElementById("boot-text");
      if (t) t.textContent = m.text;
    } else if (m.type === "ready") {
      onReady();
    } else if (m.type === "fatal") {
      const t = document.getElementById("boot-text");
      if (t) t.textContent = "Could not start the Python engine: " + m.error + ". Check your connection and reload.";
    } else if (m.type === "result") {
      const fn = pending.get(m.id);
      pending.delete(m.id);
      if (fn) fn(m.result);
    }
  };
  worker.postMessage({ type: "init", opts: { pyodide: params.get("pyodide") || undefined, version: VERSION, root: "../" } });

  function py(method, args) {
    return new Promise((resolve) => {
      const id = nextId++;
      pending.set(id, resolve);
      worker.postMessage({ type: "call", id, method, args: args || {} });
    }).then((txt) => {
      const r = safeJSON(txt) || { ok: false, error: "Bad response from engine" };
      if (!r.ok && r.trace) console.error(r.trace);
      return r;
    });
  }

  let persistTimer = null;
  function persist() {
    clearTimeout(persistTimer);
    persistTimer = setTimeout(async () => {
      if (!S.state) { store.del(STORE_KEY); return; }
      const r = await py("export");
      if (r.ok) store.set(STORE_KEY, JSON.stringify(r.session));
    }, 300);
  }

  async function onReady() {
    S.ready = true;
    const saved = safeJSON(store.get(STORE_KEY));
    if (saved) {
      const r = await py("load", { session: saved });
      if (r.ok) S.state = r.state;
      else toast("Your saved session could not be loaded: " + r.error);
    }
    render();
    maybeAdvise();
  }

  async function act(method, args, opts) {
    if (S.busy) return null;
    S.busy = true;
    const r = await py(method, args);
    S.busy = false;
    if (!r.ok) { toast(r.error || "Something went wrong"); render(); return null; }
    if ("state" in r) S.state = r.state;
    if (!opts || !opts.keepPick) { S.pick = []; S.pickText = ""; }
    S.stats = null;
    persist();
    render();
    maybeAdvise();
    if (S.view === "stats") loadStats();
    return r;
  }

  // ------------------------------------------------------------ helpers
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        const v = attrs[k];
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") n.className = v;
        else if (k === "style" && typeof v === "object") Object.assign(n.style, v);
        else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
        else if (k === "html") n.innerHTML = v;
        else if (v === true) n.setAttribute(k, "");
        else n.setAttribute(k, v);
      }
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      n.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return n;
  }
  const ICONS = {
    table: '<ellipse cx="12" cy="12" rx="9" ry="6"/><path d="M12 6v12"/>',
    chart: '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
    history: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.8.4-1 1-1 1.7"/><path d="M12 17h.01"/>',
    settings: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-1"/>',
    zap: '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>',
    ff: '<path d="M13 19l9-7-9-7v14z"/><path d="M2 19l9-7-9-7v14z"/>',
    users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9"/><path d="M16 3.1a4 4 0 0 1 0 7.8"/>',
    x: '<path d="M18 6L6 18M6 6l12 12"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/>',
    upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/>',
    spark: '<path d="M12 3l2.5 5.5L20 11l-5.5 2.5L12 19l-2.5-5.5L4 11l5.5-2.5z"/>',
    trash: '<path d="M3 6h18"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M9 6V4h6v2"/>',
    chevron: '<path d="M6 9l6 6 6-6"/>',
    play: '<path d="M6 4l14 8-14 8z"/>',
  };
  function icon(name) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("class", "i");
    s.setAttribute("aria-hidden", "true");
    s.innerHTML = ICONS[name] || "";
    return s;
  }
  function fmt(x) {
    if (x === null || x === undefined || isNaN(x)) return "—";
    const v = Math.round(Number(x) * 100) / 100;
    return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/0$/, "");
  }
  function pct(x, d) { return (x === null || x === undefined || isNaN(x)) ? "—" : (100 * x).toFixed(d === undefined ? 0 : d) + "%"; }
  function signed(x, d) {
    if (x === null || x === undefined || isNaN(x)) return "—";
    const v = d === undefined ? fmt(x) : Number(x).toFixed(d);
    return (x > 0 ? "+" : "") + v;
  }
  function bbOf(chips) { const bb = (S.state && S.state.config.bb) || 1; return chips / bb; }
  function cardStr(c) { return RANKS[c >> 2] + SUITS[c & 3]; }
  function cardEl(c, cls) {
    if (c === null || c === undefined) return el("div", { class: "pcard empty " + (cls || "") });
    const s = SUITS[c & 3];
    const r = RANKS[c >> 2] === "T" ? "10" : RANKS[c >> 2];
    return el("div", { class: "pcard " + (cls || ""), title: cardStr(c) },
      el("span", { class: "r suit-" + s }, r), el("span", { class: "s suit-" + s }, SUIT_SYM[s]));
  }
  function prettyCards(text) {
    const wrap = el("span", { class: "inline-cards" });
    if (!text) return wrap;
    for (const ch of text) {
      const s = Object.keys(SUIT_SYM).find((k) => SUIT_SYM[k] === ch);
      wrap.appendChild(s ? el("span", { class: "suit-" + s }, ch) : document.createTextNode(ch));
    }
    return wrap;
  }
  function parseCards(text) {
    let t = (text || "").replace(/10/g, "T").replace(/[\s,]+/g, "");
    for (const k in SUIT_SYM) t = t.split(SUIT_SYM[k]).join(k);
    if (t.length % 2) return null;
    const out = [];
    for (let i = 0; i < t.length; i += 2) {
      const r = RANKS.indexOf(t[i].toUpperCase());
      const s = SUITS.indexOf(t[i + 1].toLowerCase());
      if (r < 0 || s < 0) return null;
      out.push((r << 2) | s);
    }
    return out;
  }
  let toastTimer = null;
  function toast(msg, ok) {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.className = "toast show" + (ok ? " ok" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = "toast"; }, 3600);
  }
  function seatInfo(seat) { return S.state && S.state.seats.find((s) => s.seat === seat); }
  function handPlayer(seat) { const h = S.state && S.state.hand; return h && h.players.find((p) => p.seat === seat); }
  function youName(name, isHero) { return isHero && !/^you$/i.test((name || "").trim()) ? name + " (you)" : name; }
  function initials(name) {
    const parts = (name || "?").trim().split(/\s+/);
    const a = parts[0] ? parts[0][0] : "?";
    const b = parts[1] ? parts[1][0] : (parts[0] && /\d/.test(parts[0].slice(-1)) ? parts[0].slice(-1) : "");
    return (a + b).toUpperCase();
  }
  function avatar(seat, cls) {
    const key = seat.profile ? seat.profile.key : "unknown";
    return el("div", { class: "avatar " + (seat.is_hero ? "hero" : "av-" + key) + (cls ? " " + cls : "") }, initials(seat.name));
  }
  function ptag(profile, long) {
    if (!profile || profile.key === "unknown") return el("span", { class: "ptag" }, long ? "No reads yet" : "NEW");
    return el("span", { class: "ptag " + profile.key, title: profile.label + " · " + profile.confidence + " confidence" },
      long ? profile.label : PROFILE_SHORT[profile.key] || profile.label);
  }
  function roundChip(x) {
    const unit = (S.state && S.state.config.sb) || 0.5;
    const step = unit >= 1 ? unit : (unit >= 0.5 ? 0.5 : unit);
    return Math.max(step, Math.round(x / step) * step);
  }

  // ------------------------------------------------------------ advice
  function adviceKeyFor(h) { return [S.state.hand_no, h.board.length, h.log.length, h.to_act].join(":"); }
  function currentAdvice() {
    const h = S.state && S.state.hand;
    if (!h || !S.advice || S.adviceKey !== adviceKeyFor(h) || S.advice.status !== "ok") return null;
    return S.advice;
  }
  async function maybeAdvise() {
    const st = S.state;
    const h = st && st.hand;
    if (!h || h.finished || h.awaiting_board || h.to_act !== st.hero_seat) return;
    const key = adviceKeyFor(h);
    if (S.adviceKey === key && (S.advice || S.adviceBusy)) return;
    S.adviceKey = key;
    S.adviceBusy = true;
    S.advice = null;
    render();
    const r = await py("advise", { time_budget: S.prefs.budget });
    if (S.adviceKey !== key) return;
    S.adviceBusy = false;
    if (r.ok) S.advice = r.advice;
    else toast(r.error);
    if (S.advice && S.advice.recommendation && S.advice.recommendation.to) {
      S.raiseTo = S.advice.recommendation.to;
      S.raiseKey = key;
    }
    render();
  }

  async function loadStats() {
    if (!S.state || S.statsLoading) return;
    S.statsLoading = true;
    const r = await py("stats");
    S.statsLoading = false;
    if (r.ok) { S.stats = r.stats; render(); }
  }

  // ------------------------------------------------------------- render
  function render() {
    renderTop();
    if (!S.ready) return;
    const app = document.getElementById("app");
    const scroll = window.scrollY;
    app.innerHTML = "";
    if (!S.state) app.appendChild(renderSetup());
    else if (S.view === "stats") app.appendChild(renderStatsView());
    else if (S.view === "history") app.appendChild(renderHistoryView());
    else app.appendChild(renderTableView());
    renderModal();
    window.scrollTo(0, scroll);
  }

  function setView(v) {
    S.view = v;
    window.scrollTo(0, 0);
    render();
    if (v === "stats" && !S.stats) loadStats();
  }

  function renderTop() {
    const bar = document.getElementById("topbar");
    bar.innerHTML = "";
    bar.appendChild(el("div", { class: "brand" }, el("div", { class: "brand-mark" }, "♠"), el("span", { class: "brand-name" }, "Poker Help")));
    const st = S.state;
    if (st) {
      const tabs = el("nav", { class: "tabs", "aria-label": "Sections" });
      [["table", "Table", "table"], ["stats", "Stats", "chart"], ["history", "History", "history"]].forEach(([v, label, ic]) => {
        tabs.appendChild(el("button", { class: "tab" + (S.view === v ? " on" : ""), onclick: () => setView(v), "aria-current": S.view === v ? "page" : null }, icon(ic), label));
      });
      bar.appendChild(tabs);
    }
    const right = el("div", { class: "top-right" });
    if (st) {
      const hero = st.hero || {};
      right.appendChild(el("span", { class: "session-pill", title: "Your result this session" },
        el("span", { class: "hide-sm" }, "Hands ", el("b", null, String(st.hand_no))),
        el("span", null, "You ", el("b", { class: hero.net_bb >= 0 ? "pos" : "neg" }, signed(hero.net_bb, 1) + " bb")),
        hero.bb100 !== null && hero.bb100 !== undefined && hero.hands >= 10 ? el("span", { class: "hide-sm" }, el("b", null, signed(hero.bb100, 0)), " bb/100") : null));
    }
    right.appendChild(el("button", { class: "icon-btn", title: "How it works", "aria-label": "Help", onclick: () => openModal("help") }, icon("help")));
    if (st) right.appendChild(el("button", { class: "icon-btn", title: "Settings", "aria-label": "Settings", onclick: () => openModal("settings") }, icon("settings")));
    bar.appendChild(right);
  }

  // -------------------------------------------------------------- setup
  const PRESETS = {
    "6max": { players: 6, label: "6-max", sub: "Six players, the most common online game" },
    "9max": { players: 9, label: "Full ring", sub: "Nine players, typical live table" },
    hu: { players: 2, label: "Heads-up", sub: "You against one opponent" },
  };
  function renderSetup() {
    const su = S.setup;
    const n = su.players;
    if (su.hero >= n) su.hero = 0;
    const wrap = el("div", { class: "setup" });
    wrap.appendChild(el("h1", { class: "hero-title" }, "The maths of every decision, ", el("span", null, "at your table.")));
    wrap.appendChild(el("p", { class: "lead" },
      "Enter what happens in each hand. When it's your turn the bot simulates thousands of run-outs against each opponent's likely hands and tells you the best play. It learns how everyone plays as the session goes on."));

    const presets = el("div", { class: "presets" });
    Object.entries(PRESETS).forEach(([k, p]) => presets.appendChild(el("button", {
      class: "preset" + (su.preset === k ? " on" : ""), onclick: () => { su.preset = k; su.players = p.players; render(); },
    }, el("b", null, p.label), el("span", null, p.sub))));
    const playersSeg = el("div", { class: "seg", role: "group", "aria-label": "Players" });
    for (let i = 2; i <= 10; i++) {
      playersSeg.appendChild(el("button", { class: i === n ? "on" : "", onclick: () => { su.players = i; su.preset = Object.keys(PRESETS).find((k) => PRESETS[k].players === i) || ""; render(); } }, String(i)));
    }
    // seat picker on a mini table
    const pickWrap = el("div", { class: "seat-pick" }, el("div", { class: "mini-felt" }));
    for (let i = 0; i < n; i++) {
      const ang = Math.PI / 2 + (i * 2 * Math.PI) / n;
      pickWrap.appendChild(el("button", {
        class: i === su.hero ? "on" : "", style: { left: (50 + 44 * Math.cos(ang)) + "%", top: (50 + 42 * Math.sin(ang)) + "%" },
        title: "Seat " + (i + 1), onclick: () => { su.hero = i; render(); },
      }, i === su.hero ? "YOU" : String(i + 1)));
    }
    const num = (k, label, step) => el("div", { class: "field" }, el("label", null, label),
      el("input", { class: "input", type: "number", step: step || "any", min: "0", value: su[k], oninput: (e) => { su[k] = Number(e.target.value); } }));

    wrap.appendChild(el("div", { class: "card" },
      el("h2", null, icon("users"), "Your table"),
      presets,
      el("div", { class: "row spread" }, el("span", { class: "label" }, "Players at the table"), playersSeg),
      el("div", { class: "section-title" }, "Click your seat"),
      pickWrap,
      el("div", { class: "grid3", style: { marginTop: "16px" } }, num("sb", "Small blind"), num("bb", "Big blind"), num("stack", "Starting stack"))));

    const names = el("div", { class: "names-grid" });
    for (let i = 0; i < n; i++) {
      const isHero = i === su.hero;
      names.appendChild(el("input", {
        class: "input" + (isHero ? " hero" : ""), placeholder: isHero ? "You" : "Seat " + (i + 1), value: su.names[i] || "",
        "aria-label": "Name for seat " + (i + 1), oninput: (e) => { su.names[i] = e.target.value; },
      }));
    }
    wrap.appendChild(el("div", { class: "card" },
      el("h2", null, "Player names ", el("span", { class: "muted small" }, "optional, but it makes the stats easier to follow")), names));

    const start = el("button", {
      class: "btn primary lg", onclick: async () => {
        if (!(su.bb > 0) || !(su.sb >= 0) || !(su.stack > 0)) { toast("Check the blinds and the stack size"); return; }
        S.view = "table";
        await act("new_session", {
          players: n, hero_seat: su.hero, sb: su.sb, bb: su.bb, start_stack: su.stack,
          names: Array.from({ length: n }, (_, i) => su.names[i] || ""),
        });
      },
    }, icon("play"), "Start session");
    const importBtn = el("label", { class: "btn ghost" }, icon("upload"), "Import a saved session",
      el("input", { type: "file", accept: "application/json,.json", class: "hidden", onchange: importFile }));
    wrap.appendChild(el("div", { class: "row spread", style: { marginTop: "16px" } }, importBtn, start));
    wrap.appendChild(el("div", { class: "steps" },
      el("div", { class: "step" }, el("div", { class: "n" }, "1"), el("b", null, "Enter the action"), "Pick your cards, then tap what each player does. “Folds to you” skips ahead."),
      el("div", { class: "step" }, el("div", { class: "n" }, "2"), el("b", null, "Get the best play"), "Equity, pot odds and the expected value of every option, plus one-tap “Follow advice”."),
      el("div", { class: "step" }, el("div", { class: "n" }, "3"), el("b", null, "Learn the table"), "Every player gets a stats HUD and a type (nit, station, maniac…). You get your own stats too.")));
    return wrap;
  }

  // ---------------------------------------------------------- table view
  function renderTableView() {
    const page = el("div", { class: "page" });
    if (S.prefs.tip) {
      page.appendChild(el("div", { class: "banner", style: { marginBottom: "14px" } }, icon("spark"),
        el("div", null, el("b", null, "Quick start: "), "pick your two cards and press Deal. Tap each player's action in turn (or ",
          el("b", null, "Folds to you"), " to skip). When it's your turn the advisor shows the best play: tap ", el("b", null, "Follow advice"),
          " to enter it. Tap any seat to see that player's stats."),
        el("button", { class: "x", "aria-label": "Dismiss tip", onclick: () => { S.prefs.tip = false; savePrefs(); render(); } }, "✕")));
    }
    const layout = el("div", { class: "layout" });
    const left = el("div", { class: "col" }, el("div", { class: "panel", style: { padding: "8px" } }, renderTable()), renderControl());
    const right = el("div", { class: "col" }, renderAdvisor(), renderAtTable());
    layout.appendChild(left);
    layout.appendChild(right);
    page.appendChild(layout);
    page.appendChild(el("footer", { class: "foot" }, "The advice engine is Python (poker_bot) running in your browser. Your session is saved only in this browser."));
    return page;
  }

  function hudFor(seat) {
    const r = seat.raw || {};
    const hands = r.hands || 0;
    const parts = [
      el("span", { title: "VPIP / PFR" }, el("b", null, hands ? Math.round(100 * r.vpip) + "/" + Math.round(100 * r.pfr) : "–/–")),
      el("span", { title: "Aggression factor" }, "AF ", el("b", null, r.af === null || r.af === undefined ? "–" : fmt(Math.round(r.af * 10) / 10))),
      el("span", { title: "Hands tracked" }, el("b", null, String(hands)), "h"),
    ];
    if (!seat.is_hero && seat.h2h && seat.h2h.hands) {
      const net = bbOf(seat.h2h.net);
      parts.push(el("span", { class: "h2h " + (net >= 0 ? "pos" : "neg"), title: "Your result in hands against " + seat.name }, "vs ", el("b", null, signed(net, 0))));
    }
    return el("div", { class: "hud" }, parts);
  }

  function renderTable() {
    const st = S.state;
    const h = st.hand;
    const seats = st.seats;
    const n = seats.length;
    const wrap = el("div", { class: "table-wrap" + (n >= 7 ? " compact" : "") });
    const felt = el("div", { class: "felt" });
    wrap.appendChild(felt);
    const center = el("div", { class: "felt-center" });
    if (h) {
      center.appendChild(el("div", { class: "street-label" }, STREET_NAMES[h.street] || h.street));
      const board = el("div", { class: "board" });
      for (let i = 0; i < 5; i++) board.appendChild(cardEl(h.board[i] !== undefined ? h.board[i] : null));
      center.appendChild(board);
      center.appendChild(el("div", { class: "pot-label" }, "Pot " + fmt(h.pot)));
    } else {
      center.appendChild(el("div", { class: "street-label" }, "Between hands"));
      center.appendChild(el("div", { class: "pot-label" }, "Hand #" + (st.hand_no + 1)));
    }
    felt.appendChild(center);
    const mobile = window.matchMedia("(max-width: 760px)").matches;
    seats.forEach((s) => {
      const k = (s.seat - st.hero_seat + n) % n;
      const ang = Math.PI / 2 + (k * 2 * Math.PI) / n;
      const [x, y] = seatPos(k, n, mobile);
      const hp = h && h.players.find((p) => p.seat === s.seat);
      const cls = ["seat"];
      if (s.is_hero) cls.push("hero");
      if (h && h.to_act === s.seat) cls.push("to-act");
      if (hp && hp.folded) cls.push("folded");
      if (s.sitting_out || (h && !hp)) cls.push("out");
      const position = hp ? hp.position : s.next_position;
      const stack = hp ? hp.stack : s.stack;
      const node = el("div", {
        class: cls.join(" "), style: { left: x + "%", top: y + "%" }, role: "button", tabindex: "0",
        title: "Tap for " + s.name + "'s stats", onclick: () => openModal("player", { seat: s.seat }),
        onkeydown: (e) => { if (e.key === "Enter") openModal("player", { seat: s.seat }); },
      },
        el("div", { class: "seat-top" }, avatar(s),
          el("div", { class: "seat-id" },
            el("div", { class: "seat-name" }, youName(s.name, s.is_hero)),
            el("div", { class: "seat-meta" },
              position ? el("span", { class: "badge" + (position === "BTN" ? " btn-pos" : "") }, position) : null,
              el("span", { class: "seat-stack" }, s.sitting_out ? "sitting out" : fmt(stack)),
              hp && hp.all_in ? el("span", { class: "badge allin" }, "ALL-IN") : null))),
        S.prefs.hud ? hudFor(s) : null);
      if (s.is_hero && h && h.hero_cards.length) node.appendChild(el("div", { class: "seat-cards" }, h.hero_cards.map((c) => cardEl(c, "sm"))));
      else if (hp && hp.shown_raw) node.appendChild(el("div", { class: "seat-cards" }, hp.shown_raw.map((c) => cardEl(c, "sm"))));
      wrap.appendChild(node);
      if (hp && hp.street_bet > 0) {
        const bx = 50 + (x - 50) * 0.6, by = 50 + (y - 50) * (k === 0 ? 0.5 : 0.58);
        wrap.appendChild(el("div", { class: "bet-chip", style: { left: bx + "%", top: by + "%" } }, fmt(hp.street_bet)));
      }
      if (h ? h.button === s.seat : st.button === s.seat) {
        const dx = 50 + (x - 50) * 0.76 - 6 * Math.sin(ang), dy = 50 + (y - 50) * 0.72 + 5 * Math.cos(ang);
        wrap.appendChild(el("div", { class: "dealer-btn", style: { left: dx + "%", top: dy + "%" } }, "D"));
      }
    });
    return wrap;
  }

  // Seats sit on a rounded rectangle (squircle) so they spread evenly even at
  // 9-10 players on a tall phone screen; your seat is always bottom centre.
  const MOBILE_TOP = { 1: 1, 2: 0, 3: 1, 4: 2, 5: 1, 6: 2, 7: 3, 8: 2, 9: 3 };
  function mobileSeatPos(k, n) {
    // phones: a column of seats on each side plus a row across the top
    const m = n - 1;
    const top = MOBILE_TOP[m] || 3;
    const sides = (m - top) / 2;
    const i = k - 1;
    const colY = (j) => (sides === 1 ? 50 : 74 - j * (48 / (sides - 1)));
    if (i < sides) return [13, colY(i)];
    if (i < sides + top) {
      const j = i - sides;
      return [top === 1 ? 50 : 22 + j * (56 / (top - 1)), 7];
    }
    const j = i - sides - top;
    return [87, colY(sides - 1 - j)];
  }
  function seatPos(k, n, mobile) {
    if (k === 0) return mobile ? [50, 93] : [50, 89];
    if (mobile) return mobileSeatPos(k, n);
    const ang = Math.PI / 2 + (k * 2 * Math.PI) / n;
    const p = mobile ? 0.6 : 0.8;
    const rx = n >= 8 ? 41 : 43, ry = 40;
    const c = Math.cos(ang), sn = Math.sin(ang);
    return [50 + rx * Math.sign(c) * Math.pow(Math.abs(c), p), 50 + ry * Math.sign(sn) * Math.pow(Math.abs(sn), p)];
  }

  // ---------------------------------------------------------- controls
  function renderControl() {
    const h = S.state.hand;
    if (!h) return renderDeal();
    if (h.finished) return renderResult();
    if (h.awaiting_board) return renderBoardInput();
    return renderActions();
  }

  function usedCards() {
    const h = S.state.hand;
    const used = new Set();
    if (!h) return used;
    h.hero_cards.forEach((c) => used.add(c));
    h.board.forEach((c) => used.add(c));
    h.players.forEach((p) => (p.shown_raw || []).forEach((c) => used.add(c)));
    return used;
  }

  function renderPicker(need, used, onComplete, rerender) {
    const box = el("div");
    const sel = el("div", { class: "picker-sel" });
    for (let i = 0; i < need; i++) sel.appendChild(cardEl(S.pick[i] !== undefined ? S.pick[i] : null, "sm"));
    const text = el("input", {
      class: "input mono", style: { width: "140px" }, placeholder: need === 1 ? "type e.g. 7d" : need === 2 ? "type e.g. AhKd" : "type e.g. Ks7d2c",
      value: S.pickText, "aria-label": "Type cards", autocomplete: "off",
      oninput: (e) => {
        S.pickText = e.target.value;
        const cards = parseCards(e.target.value);
        if (cards && cards.length <= need && cards.every((c) => !used.has(c)) && new Set(cards).size === cards.length) {
          S.pick = cards;
          rerender();
          const again = document.querySelector('input[aria-label="Type cards"]');
          if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
        }
      },
      onkeydown: (e) => { if (e.key === "Enter" && S.pick.length === need) onComplete(true); },
    });
    box.appendChild(el("div", { class: "picker-head" }, sel,
      el("div", { class: "row" }, text, el("button", { class: "btn sm ghost", onclick: () => { S.pick = []; S.pickText = ""; rerender(); } }, "Clear"))));
    const grid = el("div", { class: "picker", role: "group", "aria-label": "Card picker" });
    for (const s of [3, 2, 1, 0]) {
      for (let r = 12; r >= 0; r--) {
        const c = (r << 2) | s;
        const on = S.pick.includes(c);
        const suit = SUITS[s];
        grid.appendChild(el("button", {
          class: "pick" + (on ? " sel" : ""), disabled: used.has(c), title: cardStr(c), "aria-label": cardStr(c), "aria-pressed": on ? "true" : "false",
          onclick: () => {
            if (on) S.pick = S.pick.filter((x) => x !== c);
            else if (S.pick.length < need) S.pick = S.pick.concat([c]);
            else S.pick = S.pick.slice(0, need - 1).concat([c]);
            S.pickText = S.pick.map(cardStr).join("");
            rerender();
            if (S.pick.length === need) onComplete(false);
          },
        }, el("span", { class: "suit-" + suit }, RANKS[r] === "T" ? "10" : RANKS[r]), el("span", { class: "s suit-" + suit }, SUIT_SYM[suit])));
      }
    }
    box.appendChild(grid);
    return box;
  }

  function renderDeal() {
    const st = S.state;
    const hero = seatInfo(st.hero_seat);
    const panel = el("div", { class: "panel" });
    const btnSel = el("select", { class: "input", style: { width: "auto" }, "aria-label": "Dealer button",
      onchange: (e) => act("set_button", { seat: Number(e.target.value) }, { keepPick: true }) });
    st.seats.filter((s) => !s.sitting_out).forEach((s) => btnSel.appendChild(el("option", { value: s.seat, selected: s.seat === st.button }, s.name)));
    panel.appendChild(el("div", { class: "panel-head" },
      el("div", null, el("div", { class: "panel-title" }, "Deal hand #" + (st.hand_no + 1)),
        el("div", { class: "panel-sub" }, "You're in the ", el("b", null, hero.next_position || "?"), ". Pick your two hole cards.")),
      el("div", { class: "row" }, el("span", { class: "label" }, "Dealer button"), btnSel)));
    panel.appendChild(renderPicker(2, new Set(), (enter) => { if (enter) dealHand(); }, render));
    panel.appendChild(el("div", { class: "panel-foot" },
      el("span", { class: "muted small" }, "Tip: type your cards, e.g. ", el("span", { class: "mono" }, "AhKd"), ", then press Enter."),
      el("button", { class: "btn primary lg", disabled: S.pick.length !== 2, onclick: dealHand }, icon("play"), "Deal")));
    return panel;
  }
  async function dealHand() {
    if (S.pick.length !== 2) return;
    S.advice = null; S.adviceKey = null; S.potWinners = null;
    await act("start_hand", { hero_cards: S.pick.map(cardStr).join("") });
  }

  function renderBoardInput() {
    const h = S.state.hand;
    const need = h.awaiting_board;
    const name = h.board.length === 0 ? "flop" : h.board.length === 3 ? "turn" : "river";
    const panel = el("div", { class: "panel" });
    panel.appendChild(el("div", { class: "panel-head" },
      el("div", null, el("div", { class: "panel-title" }, "Deal the " + name),
        el("div", { class: "panel-sub" }, need === 3 ? "Pick the three flop cards." : "Pick the " + name + " card.")),
      el("button", { class: "btn sm ghost", onclick: () => act("undo") }, icon("undo"), "Undo")));
    panel.appendChild(renderPicker(need, usedCards(), (enter) => { if (enter || need === 1) submitBoard(); }, render));
    panel.appendChild(el("div", { class: "panel-foot" }, el("span"),
      el("button", { class: "btn primary lg", disabled: S.pick.length !== need, onclick: submitBoard }, "Confirm " + name)));
    panel.appendChild(renderLog());
    return panel;
  }
  async function submitBoard() {
    const h = S.state.hand;
    if (S.pick.length !== h.awaiting_board) return;
    await act("board", { cards: S.pick.map(cardStr).join("") });
  }

  function defaultRaise(h, L) {
    const key = adviceKeyFor(h);
    if (S.raiseKey === key && S.raiseTo) return S.raiseTo;
    let to;
    if (h.street === "preflop") {
      const limpers = h.log.filter((l) => l.street === "preflop" && l.action === "call").length;
      to = L.current_bet <= h.bb + 1e-9 ? h.bb * 2.5 + h.bb * limpers : L.current_bet * 3;
    } else {
      to = L.current_bet > 0 ? L.current_bet * 3 : L.pot * 0.66;
    }
    return clampTo(roundChip(to), L);
  }
  function clampTo(x, L) { return Math.min(L.max_to, Math.max(L.min_to, x)); }
  function sizeOptions(h, L) {
    const out = [];
    if (h.street === "preflop") {
      const unopened = L.current_bet <= h.bb + 1e-9;
      const base = unopened ? h.bb : L.current_bet;
      (unopened ? [2, 2.5, 3, 4] : [2.5, 3, 4]).forEach((m) => out.push([m + "x", roundChip(base * m)]));
    } else if (L.current_bet === 0) {
      [["⅓", 1 / 3], ["½", 0.5], ["⅔", 2 / 3], ["Pot", 1]].forEach(([n, f]) => out.push([n, roundChip(L.pot * f)]));
    } else {
      [["½ pot", 0.5], ["Pot", 1]].forEach(([n, f]) => out.push([n, roundChip(L.current_bet + f * (L.pot + L.to_call))]));
      out.push(["3x", roundChip(L.current_bet * 3)]);
    }
    out.unshift(["Min", L.min_to]);
    return out.map(([n, v]) => [n, clampTo(v, L)]);
  }

  function heroStillToAct(h) {
    const hero = handPlayer(S.state.hero_seat);
    return hero && !hero.folded && !hero.all_in;
  }

  function renderActions() {
    const st = S.state;
    const h = st.hand;
    const L = h.legal;
    const actor = handPlayer(L.seat);
    const isHero = L.seat === st.hero_seat;
    const key = adviceKeyFor(h);
    const adv = isHero ? currentAdvice() : null;
    const recAct = adv ? adv.recommendation.action : null;
    const panel = el("div", { class: "panel" });
    panel.appendChild(el("div", { class: "to-act" },
      avatar(seatInfo(L.seat)),
      el("span", { class: "who" }, isHero ? "Your turn" : actor.name),
      el("span", { class: "badge" + (actor.position === "BTN" ? " btn-pos" : "") }, actor.position),
      el("span", { class: "facing" }, L.to_call > 0 ? "to call " + fmt(L.to_call) + " · pot " + fmt(L.pot) : "pot " + fmt(L.pot)),
      el("span", { class: "muted small" }, "stack " + fmt(L.stack))));
    const acts = el("div", { class: "actions" });
    const has = (a) => L.actions.includes(a);
    const button = (label, sub, cls, rec, fn, kbd) => el("button", {
      class: "btn act " + cls + (rec ? " recommended" : ""), disabled: S.busy, onclick: fn, title: kbd ? "Shortcut: " + kbd : null,
    }, label, sub ? el("small", null, sub) : null);
    if (has("fold")) acts.appendChild(button("Fold", "F", "fold", recAct === "fold", () => doAction("fold"), "F"));
    if (has("check")) acts.appendChild(button("Check", "K", "call", recAct === "check", () => doAction("check"), "K"));
    if (has("call")) acts.appendChild(button("Call", fmt(L.to_call), "call", recAct === "call", () => doAction("call"), "C"));
    if (has("raise") || has("bet")) {
      const verb = has("bet") ? "Bet" : "Raise to";
      const val = defaultRaise(h, L);
      const rb = button(verb, fmt(val), "raise", recAct === "raise" || recAct === "bet", () => doRaise(), "R");
      const input = el("input", {
        class: "input", type: "number", step: "any", min: L.min_to, max: L.max_to, value: val, "aria-label": verb + " amount",
        oninput: (e) => { S.raiseTo = Number(e.target.value); S.raiseKey = key; rb.querySelector("small").textContent = fmt(S.raiseTo); },
        onkeydown: (e) => { if (e.key === "Enter") doRaise(); },
      });
      const sizes = el("div", { class: "sizes" });
      sizeOptions(h, L).forEach(([n, v]) => sizes.appendChild(el("button", {
        class: "chip-btn" + (Math.abs(v - val) < 1e-9 ? " on" : ""), title: fmt(v), onclick: () => { S.raiseTo = v; S.raiseKey = key; render(); },
      }, n)));
      acts.appendChild(el("div", { class: "raise-box" }, rb, input, sizes));
    }
    if (has("allin")) acts.appendChild(button("All-in", fmt(L.max_to), "allin", recAct === "allin", () => doAction("allin"), "A"));
    panel.appendChild(acts);
    // quick entry
    const quick = el("div", { class: "quick" });
    if (!isHero && heroStillToAct(h)) {
      if (L.to_call > 0 || h.street === "preflop") {
        quick.appendChild(el("button", { class: "btn sm", disabled: S.busy, onclick: () => quickAdvance("fold"), title: "Everyone between " + actor.name + " and you folds" }, icon("ff"), "Folds to you"));
      }
      if (L.current_bet === 0) {
        quick.appendChild(el("button", { class: "btn sm", disabled: S.busy, onclick: () => quickAdvance("check"), title: "Everyone between " + actor.name + " and you checks" }, icon("ff"), "Checks to you"));
      }
    }
    if (quick.childNodes.length) panel.appendChild(quick);
    panel.appendChild(el("div", { class: "panel-foot" },
      el("span", { class: "muted small" }, isHero ? "Enter what you actually did, or use Follow advice." : "Tap what " + actor.name + " did. Keys: F fold · K check · C call · R raise · A all-in · U undo"),
      el("div", { class: "row" },
        el("button", { class: "btn sm ghost", disabled: S.busy, onclick: () => act("undo") }, icon("undo"), "Undo"),
        el("button", { class: "btn sm ghost danger", onclick: () => { if (confirm("Discard this hand without saving it?")) act("abandon"); } }, icon("trash"), "Discard"))));
    panel.appendChild(renderLog());
    return panel;
  }

  async function quickAdvance(kind) {
    if (S.busy) return;
    S.busy = true;
    let r = null;
    for (let guard = 0; guard < 12; guard++) {
      const h = S.state.hand;
      if (!h || h.finished || h.awaiting_board || h.to_act === S.state.hero_seat || h.to_act === null) break;
      const L = h.legal;
      if (kind === "check" && L.to_call > 0) break;
      r = await py("act", { seat: h.to_act, action: kind === "check" ? "check" : (L.to_call > 0 ? "fold" : "check") });
      if (!r.ok) { toast(r.error); break; }
      S.state = r.state;
    }
    S.busy = false;
    S.stats = null;
    persist();
    render();
    maybeAdvise();
  }

  async function doAction(a, amount) {
    const h = S.state.hand;
    if (!h || h.to_act === null) return;
    const adv = h.to_act === S.state.hero_seat ? currentAdvice() : null;
    S.raiseTo = null; S.raiseKey = null;
    await act("act", { seat: h.to_act, action: a, amount: amount, advised: adv ? { action: adv.recommendation.action, to: adv.recommendation.to } : undefined });
  }
  function doRaise() {
    const h = S.state.hand;
    const L = h.legal;
    const v = Number(S.raiseKey === adviceKeyFor(h) && S.raiseTo ? S.raiseTo : defaultRaise(h, L));
    if (!(v > L.current_bet)) { toast("The amount must be more than the current bet (" + fmt(L.current_bet) + ")"); return; }
    doAction(L.actions.includes("bet") ? "bet" : "raise", v);
  }
  function followAdvice() {
    const adv = currentAdvice();
    if (!adv) return;
    const r = adv.recommendation;
    if (r.action === "bet" || r.action === "raise") doAction(r.action, r.to);
    else doAction(r.action);
  }

  function renderLog(hlog) {
    const log = hlog || (S.state.hand && S.state.hand.log) || [];
    const box = el("div", { class: "log" });
    if (!log.length) { box.appendChild(el("div", { class: "muted" }, "Blinds posted. No actions yet.")); return box; }
    let street = null;
    log.forEach((l) => {
      if (l.street !== street) { street = l.street; box.appendChild(el("div", { class: "log-street" }, STREET_NAMES[street] || street)); }
      box.appendChild(el("div", { class: "log-row" }, el("span", { class: "n" }, l.name + " (" + l.position + ")"), el("span", null, l.text)));
    });
    if (!hlog) setTimeout(() => { box.scrollTop = box.scrollHeight; }, 0);
    return box;
  }

  function renderResult() {
    const st = S.state;
    const h = st.hand;
    const panel = el("div", { class: "panel" });
    if (h.result.reason === "fold") {
      const w = handPlayer(h.result.winners[0]);
      panel.appendChild(el("div", { class: "panel-head" },
        el("div", null, el("div", { class: "panel-title" }, (w.seat === st.hero_seat ? "You win" : w.name + " wins") + " the pot of " + fmt(h.pot)),
          el("div", { class: "panel-sub" }, "Everyone else folded. Save the hand to update the stats."))));
      panel.appendChild(el("div", { class: "row", style: { justifyContent: "flex-end" } },
        el("button", { class: "btn ghost", onclick: () => act("undo") }, icon("undo"), "Undo"),
        el("button", { class: "btn primary lg", onclick: () => finishHand() }, "Save & next hand")));
      panel.appendChild(renderLog());
      return panel;
    }
    const potKey = st.hand_no + ":" + h.log.length + ":" + h.players.map((p) => p.shown || "").join("|");
    if (S.potKey !== potKey) {
      S.potKey = potKey;
      S.potWinners = h.auto_winners ? h.auto_winners.map((x) => x.slice()) : h.pots.map(() => []);
    }
    panel.appendChild(el("div", { class: "panel-head" },
      el("div", null, el("div", { class: "panel-title" }, "Showdown"),
        el("div", { class: "panel-sub" }, "Enter any cards that were shown (this teaches the bot how they play), then pick who won."))));
    h.players.filter((p) => !p.folded).forEach((p) => {
      const isHero = p.seat === st.hero_seat;
      panel.appendChild(el("div", { class: "showdown-opp" },
        el("span", { class: "row" }, avatar(seatInfo(p.seat)), el("b", null, youName(p.name, isHero)), el("span", { class: "badge" }, p.position)),
        isHero ? prettyCards(h.hero_pretty)
          : p.shown ? el("span", { class: "row" }, prettyCards(p.shown), el("button", { class: "btn sm ghost", onclick: () => openModal("show", { seat: p.seat }) }, "Change"))
            : el("button", { class: "btn sm", onclick: () => openModal("show", { seat: p.seat }) }, "Enter shown cards")));
    });
    const pots = el("div", { class: "pots" });
    h.pots.forEach((pot, i) => {
      const row = el("div", { class: "pot-row" },
        el("div", { class: "row spread", style: { marginBottom: "8px" } },
          el("b", null, (i === 0 ? "Main pot" : "Side pot " + i) + " · " + fmt(pot.amount)),
          el("span", { class: "muted small" }, pot.eligible.length > 1 ? "Who won? Pick two or more for a split." : "Goes back to the only player in it")));
      const seg = el("div", { class: "row" });
      pot.eligible.forEach((seat) => {
        const on = (S.potWinners[i] || []).includes(seat);
        seg.appendChild(el("button", { class: "chip-btn" + (on ? " on" : ""), onclick: () => {
          const cur = S.potWinners[i] || [];
          S.potWinners[i] = on ? cur.filter((x) => x !== seat) : cur.concat([seat]);
          render();
        } }, youName(handPlayer(seat).name, seat === st.hero_seat)));
      });
      row.appendChild(seg);
      pots.appendChild(row);
    });
    panel.appendChild(pots);
    if (h.auto_winners) panel.appendChild(el("div", { class: "muted small" }, "Winners were filled in from the shown cards."));
    const ready = h.pots.every((pot, i) => pot.eligible.length === 1 || (S.potWinners[i] || []).length > 0);
    panel.appendChild(el("div", { class: "row", style: { justifyContent: "flex-end", marginTop: "10px" } },
      el("button", { class: "btn ghost", onclick: () => act("undo") }, icon("undo"), "Undo"),
      el("button", { class: "btn primary lg", disabled: !ready, onclick: () => finishHand(S.potWinners) }, "Save & next hand")));
    panel.appendChild(renderLog());
    return panel;
  }
  async function finishHand(winners) {
    const h = S.state.hand;
    let w = winners;
    if (w && h.pots) w = h.pots.map((pot, i) => (pot.eligible.length === 1 ? pot.eligible : w[i]));
    const r = await act("finish", w ? { winners: w } : {});
    if (r) {
      S.advice = null; S.adviceKey = null; S.potWinners = null; S.potKey = null;
      const net = bbOf(r.result.hero_net);
      toast("Hand saved. " + (net > 0 ? "You won " + fmt(net) + " bb" : net < 0 ? "You lost " + fmt(-net) + " bb" : "You broke even") + ".", true);
      render();
    }
  }

  // ------------------------------------------------------------ advisor
  function renderAdvisor() {
    const st = S.state;
    const h = st.hand;
    const panel = el("div", { class: "panel" });
    const seg = el("div", { class: "seg", role: "group", "aria-label": "Advice detail" },
      el("button", { class: S.prefs.detail ? "" : "on", onclick: () => { S.prefs.detail = false; savePrefs(); render(); } }, "Simple"),
      el("button", { class: S.prefs.detail ? "on" : "", onclick: () => { S.prefs.detail = true; savePrefs(); render(); } }, "Detailed"));
    panel.appendChild(el("div", { class: "panel-head" }, el("div", { class: "panel-title" }, icon("spark"), "Advisor",
      h ? el("span", { class: "panel-sub" }, "· " + (STREET_NAMES[h.street] || "")) : null), seg));
    const empty = (big, text) => el("div", { class: "advisor-empty" }, big ? el("div", { class: "big" }, big) : null, el("div", null, text));
    if (!h) { panel.appendChild(empty("♠", "Deal a hand to get advice. When it's your turn the bot simulates thousands of run-outs against each opponent's likely hands.")); return panel; }
    if (h.finished) { panel.appendChild(empty(null, "Hand over. Save the result: everything you entered updates the player stats.")); return panel; }
    const hero = handPlayer(st.hero_seat);
    if (hero && hero.folded) { panel.appendChild(empty(null, "You folded. Keep entering the other players' actions (and any shown cards) so the bot learns how they play.")); return panel; }
    if (h.awaiting_board) {
      panel.appendChild(el("div", { class: "waiting" }, "Enter the board cards to continue."));
      if (h.hero_hand_desc) panel.appendChild(el("div", null, "You have ", el("b", null, h.hero_hand_desc), " with ", prettyCards(h.hero_pretty), "."));
      return panel;
    }
    if (h.to_act !== st.hero_seat) {
      const actor = handPlayer(h.to_act);
      panel.appendChild(el("div", { class: "waiting" }, el("span", { class: "spinner sm" }), "Waiting for " + actor.name + " (" + actor.position + ")…"));
      panel.appendChild(el("div", { class: "muted" }, "Your hand: ", prettyCards(h.hero_pretty), h.hero_hand_desc ? " · " + h.hero_hand_desc : ""));
      return panel;
    }
    if (S.adviceBusy || !S.advice) { panel.appendChild(el("div", { class: "waiting" }, el("span", { class: "spinner sm" }), "Simulating run-outs against your opponents' likely hands…")); return panel; }
    const a = S.advice;
    if (a.status !== "ok") { panel.appendChild(el("div", { class: "muted" }, "No advice available (" + a.status + ").")); return panel; }
    const rec = a.recommendation;
    const kind = rec.action === "fold" ? "fold" : (rec.action === "check" || rec.action === "call") ? "passive" : "";
    panel.appendChild(el("div", { class: "rec " + kind },
      el("div", { class: "rec-top" },
        el("div", null, el("div", { class: "rec-kind" }, KIND_LABEL[a.kind] || "Recommended"), el("div", { class: "rec-action" }, rec.label)),
        el("span", { class: "conf " + a.confidence }, a.confidence === "clear" ? "Clear" : a.confidence === "moderate" ? "Likely" : "Close call")),
      el("div", { class: "rec-summary" }, a.summary),
      el("div", { class: "rec-foot" },
        el("span", { class: "rec-ev" }, "EV " + signed(rec.ev_bb) + " bb" + (rec.action !== "fold" ? " vs folding" : "")),
        el("button", { class: "btn primary", disabled: S.busy, onclick: followAdvice, title: "Enter this action for you" }, icon("zap"), "Follow advice"))));
    // equity bar
    const bar = el("div", { class: "eqbar" });
    const track = el("div", { class: "eqbar-track" }, el("div", { class: "eqbar-fill", style: { width: Math.max(1, a.equity * 100) + "%" } }));
    if (a.to_call > 0) track.appendChild(el("div", { class: "eqbar-need", style: { left: (a.pot_odds * 100) + "%" }, "data-label": "need " + pct(a.pot_odds) }));
    bar.appendChild(track);
    bar.appendChild(el("div", { class: "eqbar-legend" },
      el("span", null, "Your equity ", el("b", { style: { color: "var(--green)" } }, pct(a.equity, 1))),
      el("span", null, "±" + pct(1.96 * a.equity_se, 1) + " · " + a.iterations.toLocaleString() + " sims")));
    panel.appendChild(bar);
    panel.appendChild(el("div", { class: "kpis" },
      kpi("Pot", fmt(a.pot)), kpi("To call", a.to_call > 0 ? fmt(a.to_call) : "—"),
      kpi("Need", a.to_call > 0 ? pct(a.pot_odds) : "—"),
      a.street === "preflop" ? kpi("Hand rank", "top " + pct(a.hand_percentile)) : kpi("SPR", fmt(a.spr))));
    if (a.hand_desc || a.draws.length) {
      panel.appendChild(el("div", { class: "hand-line" }, prettyCards(a.hero_cards), " · ", el("b", null, a.hand_desc || ""),
        a.draws.length ? el("span", { class: "muted" }, " + " + a.draws.join(", ") + (a.outs ? " (" + a.outs + " outs)" : "")) : null));
    }
    if (!S.prefs.detail) {
      const mini = el("div", { class: "opp-mini" });
      a.opponents.forEach((o) => mini.appendChild(el("div", { class: "opp-mini-row" },
        avatar(seatInfo(o.seat) || { name: o.name, profile: o.profile }), el("b", null, o.name), el("span", { class: "badge" }, o.position), ptag(o.profile),
        el("span", { class: "eq", title: "Your equity against this player's likely hands" }, pct(o.hu_equity)))));
      panel.appendChild(mini);
      panel.appendChild(el("div", { class: "row", style: { marginTop: "12px" } },
        el("button", { class: "btn sm ghost", onclick: () => { S.prefs.detail = true; savePrefs(); render(); } }, icon("chevron"), "Show the maths")));
      return panel;
    }
    panel.appendChild(el("div", { class: "section-title" }, "Expected value of each option"));
    const maxAbs = Math.max(0.01, ...a.options.map((o) => Math.abs(o.ev_bb)));
    const tb = el("tbody");
    a.options.forEach((o) => {
      const w = (Math.abs(o.ev_bb) / maxAbs) * 50;
      tb.appendChild(el("tr", { class: o.label === rec.label ? "best" : "" },
        el("td", null, o.label), el("td", { class: "num" }, signed(o.ev_bb)),
        el("td", null, el("div", { class: "evbar" }, el("div", { class: "mid" }), el("div", { class: "b " + (o.ev_bb >= 0 ? "p" : "n"), style: { width: w + "%" } }))),
        el("td", { class: "num" }, o.p_fold !== null && o.p_fold !== undefined ? pct(o.p_fold) : ""),
        el("td", { class: "num" }, o.action === "fold" ? "" : pct(o.equity_called))));
    });
    panel.appendChild(el("div", { style: { overflowX: "auto" } }, el("table", { class: "ev-table" },
      el("thead", null, el("tr", null, el("th", null, "Option"), el("th", { style: { textAlign: "right" } }, "EV (bb)"), el("th", null, ""),
        el("th", { style: { textAlign: "right" } }, "They fold"), el("th", { style: { textAlign: "right" } }, "Eq. if called"))), tb)));
    panel.appendChild(el("div", { class: "section-title" }, "Why"));
    panel.appendChild(el("ul", { class: "reasons" }, a.reasons.map((r) => el("li", null, r))));
    if (a.opponents.length) {
      panel.appendChild(el("div", { class: "section-title" }, "What they probably have"));
      const g = el("div", { class: "opp-grid" });
      a.opponents.forEach((o) => g.appendChild(renderOpp(o)));
      panel.appendChild(g);
    }
    return panel;
  }
  function kpi(k, v, s) { return el("div", { class: "kpi" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v), s ? el("div", { class: "s" }, s) : null); }

  function renderOpp(o) {
    const s = o.stats;
    const card = el("div", { class: "opp" },
      el("div", { class: "opp-head" },
        el("div", { style: { minWidth: 0 } }, el("div", { class: "opp-name" }, o.name), el("div", { class: "row", style: { gap: "4px", marginTop: "2px" } }, el("span", { class: "badge" }, o.position), ptag(o.profile))),
        el("div", null, el("div", { class: "opp-eq" }, pct(o.hu_equity)), el("div", { class: "faint small" }, "your equity"))));
    const grid = el("div", { class: "rgrid", "aria-label": "Likely hands for " + o.name });
    for (let r = 0; r < 13; r++) {
      for (let c = 0; c < 13; c++) {
        const v = o.grid[r][c];
        const hi = RANKS[12 - Math.min(r, c)], lo = RANKS[12 - Math.max(r, c)];
        const lab = r === c ? hi + lo : hi + lo + (r < c ? "s" : "o");
        grid.appendChild(el("div", {
          title: lab + " · " + Math.round(v * 100) + "% likely",
          style: { background: v > 0.01 ? "rgba(234,184,76," + (0.12 + 0.88 * v).toFixed(2) + ")" : "rgba(255,255,255,0.03)", color: v > 0.55 ? "#1d1608" : "" },
        }, lab));
      }
    }
    card.appendChild(grid);
    const top = o.top_classes.slice(0, 5).map((t) => t[0]).join(", ");
    card.appendChild(el("div", { class: "range-line" }, !o.acted
      ? "Hasn't acted yet: the simulation lets them continue with the hands they would play."
      : ["~" + pct(o.range_pct) + " of hands", o.range_text ? " · " + o.range_text : top ? " · e.g. " + top : ""]));
    card.appendChild(el("div", { class: "mini-stats" },
      stat("VPIP", s.vpip), stat("PFR", s.pfr), stat("AF", s.af, true), stat("Fold/bet", s.fold_to_bet)));
    return card;
  }
  function stat(name, v, raw) { return el("div", null, el("b", null, v === null || v === undefined ? "—" : raw ? fmt(v) : pct(v)), el("span", null, name)); }

  function renderAtTable() {
    const st = S.state;
    const panel = el("div", { class: "panel" });
    panel.appendChild(el("div", { class: "panel-head" },
      el("div", null, el("div", { class: "panel-title" }, icon("users"), "At this table"),
        el("div", { class: "panel-sub" }, "Everyone's stats this session, including yours. Tap a player for details.")),
      el("button", { class: "btn sm ghost", onclick: () => setView("stats") }, icon("chart"), "Full stats")));
    const list = el("div", { class: "tl" });
    const seats = st.seats.slice().sort((a, b) => (b.is_hero - a.is_hero) || (a.seat - b.seat));
    seats.forEach((s) => {
      const r = s.raw || {};
      const right = s.is_hero
        ? el("div", { class: "tl-right" }, el("b", { class: (st.hero.net_bb >= 0 ? "pos" : "neg") }, signed(st.hero.net_bb, 1) + " bb"), el("div", null, "this session"))
        : s.h2h && s.h2h.hands
          ? el("div", { class: "tl-right" }, el("b", { class: (s.h2h.net >= 0 ? "pos" : "neg") }, signed(bbOf(s.h2h.net), 1) + " bb"), el("div", null, "vs you · " + s.h2h.hands + " hands"))
          : el("div", { class: "tl-right" }, el("div", null, "no hands vs you yet"));
      list.appendChild(el("div", { class: "tl-row" + (s.is_hero ? " me" : ""), onclick: () => openModal("player", { seat: s.seat }), role: "button", tabindex: "0" },
        avatar(s),
        el("div", { style: { minWidth: 0 } },
          el("div", { class: "tl-name" }, youName(s.name, s.is_hero), s.is_hero ? null : ptag(s.profile), s.sitting_out ? el("span", { class: "faint small" }, "sitting out") : null),
          el("div", { class: "tl-stats" },
            el("span", null, "VPIP ", el("b", null, pct(r.vpip))), el("span", null, "PFR ", el("b", null, pct(r.pfr))),
            el("span", null, "AF ", el("b", null, r.af === null || r.af === undefined ? "—" : fmt(Math.round(r.af * 10) / 10))),
            el("span", null, el("b", null, String(r.hands || 0)), " hands"))),
        right));
    });
    panel.appendChild(list);
    return panel;
  }

  // ------------------------------------------------------------- stats
  function renderStatsView() {
    const page = el("div", { class: "page" });
    const S2 = S.stats;
    if (!S2) {
      page.appendChild(el("div", { class: "panel" }, el("div", { class: "waiting" }, el("span", { class: "spinner sm" }), "Crunching the numbers…")));
      if (!S.statsLoading) loadStats();
      return page;
    }
    const hero = S2.hero;
    const bb = S2.bb || 1;
    const grid = el("div", { class: "stats-grid" });
    // --- your session
    const you = el("div", { class: "panel" });
    you.appendChild(el("div", { class: "panel-head" }, el("div", { class: "panel-title" }, icon("chart"), "Your session"),
      el("span", { class: "panel-sub" }, S2.table_size + " players at the table")));
    you.appendChild(el("div", { class: "big-kpis" },
      bigKpi("Hands", String(hero.hands)),
      bigKpi("Result", signed(hero.net_bb, 1) + " bb", hero.net >= 0 ? "pos" : "neg", fmt(hero.net) + " in chips"),
      bigKpi("Win rate", hero.bb100 === null ? "—" : signed(hero.bb100, 0), hero.bb100 >= 0 ? "pos" : "neg", "bb per 100 hands"),
      bigKpi("Showdowns won", pct(hero.showdown_win_pct), null, hero.showdowns + " showdowns")));
    you.appendChild(el("div", { class: "section-title" }, "Profit over the session (bb)"));
    you.appendChild(profitChart(hero.curve.map((x) => x / bb)));
    you.appendChild(el("div", { class: "section-title" }, "Following the advice"));
    if (hero.decisions) {
      you.appendChild(el("div", { class: "big-kpis" },
        bigKpi("Advice followed", pct(hero.followed_pct), null, hero.decisions + " decisions"),
        bigKpi("When followed", hero.bb_per_hand_followed === null ? "—" : signed(hero.bb_per_hand_followed, 2), hero.bb_per_hand_followed >= 0 ? "pos" : "neg", "bb per hand · " + hero.hands_followed + " hands"),
        bigKpi("When not", hero.bb_per_hand_deviated === null ? "—" : signed(hero.bb_per_hand_deviated, 2), hero.bb_per_hand_deviated >= 0 ? "pos" : "neg", "bb per hand · " + hero.hands_deviated + " hands"),
        bigKpi("Biggest pot", signed(bbOf(hero.biggest_win), 0) + " bb", "pos", "worst " + signed(bbOf(hero.biggest_loss), 0) + " bb")));
      you.appendChild(el("div", { class: "faint small", style: { marginTop: "8px" } }, "Short-term results swing a lot: judge the advice over hundreds of hands, not a few."));
    } else {
      you.appendChild(el("div", { class: "muted small" }, "Once you play hands with the advisor on, this shows how often you followed it and how each choice worked out."));
    }
    const pos = hero.by_position || {};
    const order = ["UTG", "UTG+1", "UTG+2", "MP", "LJ", "HJ", "CO", "BTN", "SB", "BB"];
    const posRows = order.filter((p) => pos[p]).map((p) => el("tr", null, el("td", null, p), el("td", { class: "num" }, String(pos[p].hands)),
      el("td", { class: "num " + (pos[p].net >= 0 ? "pos" : "neg") }, signed(pos[p].net / bb, 1)),
      el("td", { class: "num" }, pos[p].hands ? signed(100 * pos[p].net / bb / pos[p].hands, 0) : "—")));
    if (posRows.length) {
      you.appendChild(el("div", { class: "section-title" }, "Results by position"));
      you.appendChild(el("table", { class: "data-table pos-table" }, el("thead", null, el("tr", null, el("th", null, "Position"), el("th", { class: "num" }, "Hands"), el("th", { class: "num" }, "Net (bb)"), el("th", { class: "num" }, "bb/100"))), el("tbody", null, posRows)));
    }
    grid.appendChild(you);
    // --- how you play
    const how = el("div", { class: "panel" });
    how.appendChild(el("div", { class: "panel-head" }, el("div", { class: "panel-title" }, icon("spark"), "How you play"),
      el("span", { class: "panel-sub" }, "Green band = typical winning player at this table size")));
    const bench = el("div", { class: "bench" });
    S2.benchmarks.forEach((b) => {
      const isAf = b.key === "af";
      const scale = isAf ? 5 : 1;
      const v = b.value;
      const left = (x) => Math.max(0, Math.min(100, 100 * x / scale)) + "%";
      const track = el("div", { class: "bench-track" }, el("div", { class: "bench-band", style: { left: left(b.lo), width: (100 * (b.hi - b.lo) / scale) + "%" } }));
      if (v !== null && v !== undefined) track.appendChild(el("div", { class: "bench-dot " + (b.verdict === "ok" ? "ok" : b.verdict ? "" : "na"), style: { left: left(v) } }));
      bench.appendChild(el("div", { class: "bench-row" },
        el("div", { class: "bench-name" }, b.label, el("small", null, (isAf ? fmt(b.lo) + "–" + fmt(b.hi) : pct(b.lo) + "–" + pct(b.hi)) + (b.verdict ? "" : " · need " + b.min_n + "+"))),
        track,
        el("div", { class: "bench-val" }, v === null || v === undefined ? "—" : isAf ? fmt(Math.round(v * 10) / 10) : pct(v))));
      if (b.tip) bench.appendChild(el("div", { class: "bench-row" }, el("div"), el("div", { class: "bench-tip" }, b.tip)));
    });
    how.appendChild(bench);
    how.appendChild(el("div", { class: "faint small", style: { marginTop: "12px" } }, "Grey dots need more hands before they mean much."));
    grid.appendChild(how);
    page.appendChild(grid);

    // --- you vs the table
    const cmp = el("div", { class: "panel", style: { marginTop: "16px" } });
    cmp.appendChild(el("div", { class: "panel-head" }, el("div", { class: "panel-title" }, icon("users"), "You vs the table"),
      el("span", { class: "panel-sub" }, "Head-to-head counts hands where you both played (your whole result in those pots)")));
    const players = S2.players.slice().sort((a, b) => (b.is_hero - a.is_hero) || ((a.seat === null) - (b.seat === null)) || ((a.seat || 0) - (b.seat || 0)));
    const head = ["Player", "Type", "Hands", "VPIP", "PFR", "3-bet", "AF", "Fold to bet", "C-bet", "WTSD", "W$SD", "Hands with you", "Your result", "Showdowns W–L"];
    const rows = players.map((p) => {
      const r = p.raw;
      const h2 = p.h2h;
      return el("tr", { class: p.is_hero ? "me" : "" },
        el("td", null, el("span", { class: "row", style: { gap: "8px", flexWrap: "nowrap" } }, avatar(Object.assign({ name: p.name, profile: p.profile, is_hero: p.is_hero })), youName(p.name, p.is_hero) + (p.seat === null ? " (left)" : ""))),
        el("td", null, p.is_hero ? el("span", { class: "faint" }, "—") : ptag(p.profile, true)),
        el("td", { class: "num" }, String(r.hands)),
        ...["vpip", "pfr", "threebet"].map((k) => el("td", { class: "num" }, pct(r[k]))),
        el("td", { class: "num" }, r.af === null ? "—" : fmt(Math.round(r.af * 10) / 10)),
        ...["fold_to_bet", "cbet", "wtsd", "wsd"].map((k) => el("td", { class: "num" }, pct(r[k]))),
        el("td", { class: "num" }, p.is_hero ? "—" : h2 ? String(h2.hands) : "0"),
        el("td", { class: "num " + (h2 && h2.net >= 0 ? "pos" : h2 ? "neg" : "") }, p.is_hero || !h2 ? "—" : signed(h2.net / bb, 1) + " bb"),
        el("td", { class: "num" }, p.is_hero || !h2 ? "—" : h2.hero_won + "–" + h2.opp_won));
    });
    cmp.appendChild(el("div", { style: { overflowX: "auto" } }, el("table", { class: "data-table" },
      el("thead", null, el("tr", null, head.map((t, i) => el("th", { class: i >= 2 ? "num" : "" }, t)))), el("tbody", null, rows))));
    page.appendChild(cmp);

    // --- opponent cards
    page.appendChild(el("div", { class: "section-title", style: { marginTop: "22px" } }, "Opponents"));
    const cards = el("div", { class: "player-cards" });
    players.filter((p) => !p.is_hero).forEach((p) => cards.appendChild(playerCard(p, bb)));
    page.appendChild(cards);
    page.appendChild(el("p", { class: "faint small", style: { marginTop: "18px" } },
      "VPIP: how often they put money in pre-flop. PFR: how often they raise pre-flop. AF: post-flop bets and raises divided by calls. WTSD: went to showdown after seeing the flop. W$SD: won at showdown. Open Help for the full glossary."));
    return page;
  }
  function bigKpi(k, v, cls, s) { return el("div", { class: "big-kpi" }, el("div", { class: "k" }, k), el("div", { class: "v " + (cls || "") }, v), s ? el("div", { class: "s" }, s) : null); }

  function profitChart(data) {
    const W = 600, H = 190, P = 28;
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("class", "chart");
    svg.setAttribute("role", "img");
    if (!data.length) {
      svg.innerHTML = '<text x="' + W / 2 + '" y="' + H / 2 + '" text-anchor="middle">Play some hands to see your profit line</text>';
      return svg;
    }
    const pts = [0].concat(data);
    const min = Math.min(0, ...pts), max = Math.max(0, ...pts);
    const span = (max - min) || 1;
    const x = (i) => P + (i / Math.max(1, pts.length - 1)) * (W - 2 * P);
    const y = (v) => H - P + 6 - ((v - min) / span) * (H - 2 * P);
    const d = pts.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
    const last = pts[pts.length - 1];
    const col = last >= 0 ? "#3ddc8f" : "#f26b6b";
    svg.innerHTML =
      '<defs><linearGradient id="pg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="' + col + '" stop-opacity=".35"/><stop offset="1" stop-color="' + col + '" stop-opacity="0"/></linearGradient></defs>' +
      '<line class="zero" x1="' + P + '" x2="' + (W - P) + '" y1="' + y(0) + '" y2="' + y(0) + '"/>' +
      '<path d="' + d + " L" + x(pts.length - 1) + " " + y(0) + " L" + x(0) + " " + y(0) + ' Z" fill="url(#pg)"/>' +
      '<path class="line" style="stroke:' + col + '" d="' + d + '"/>' +
      '<text x="' + (W - P) + '" y="' + (y(last) - 8) + '" text-anchor="end" style="fill:' + col + ';font-weight:700">' + (last >= 0 ? "+" : "") + fmt(Math.round(last * 10) / 10) + " bb</text>" +
      '<text x="' + P + '" y="' + (P - 8) + '">' + fmt(Math.round(max)) + '</text><text x="' + P + '" y="' + (H - 4) + '">' + fmt(Math.round(min)) + "</text>" +
      '<text x="' + (W - P) + '" y="' + (H - 4) + '" text-anchor="end">' + data.length + " hands</text>";
    return svg;
  }

  function playerCard(p, bb) {
    const r = p.raw;
    const box = el("div", { class: "pcard-box" });
    box.appendChild(el("div", { class: "pcard-head" }, avatar({ name: p.name, profile: p.profile, is_hero: false }),
      el("div", { style: { minWidth: 0 } }, el("div", { class: "opp-name" }, p.name + (p.seat === null ? " (left the table)" : "")),
        el("div", { class: "row", style: { gap: "6px", marginTop: "2px" } }, ptag(p.profile, true), el("span", { class: "faint small" }, r.hands + " hands · " + (p.profile.confidence || "low") + " confidence")))));
    const bars = el("div", { class: "statbars" });
    [["VPIP", r.vpip, 1], ["PFR", r.pfr, 1], ["3-bet", r.threebet, 0.4], ["Aggression", r.af, 5], ["Fold to bet", r.fold_to_bet, 1], ["C-bet", r.cbet, 1], ["Showdown", r.wtsd, 1]].forEach(([n, v, scale]) => {
      bars.appendChild(el("div", { class: "statbar" }, el("span", { class: "muted" }, n),
        el("div", { class: "t" }, el("div", { class: "f", style: { width: v === null || v === undefined ? "0" : Math.min(100, 100 * v / scale) + "%" } })),
        el("span", { class: "v" }, v === null || v === undefined ? "—" : n === "Aggression" ? fmt(Math.round(v * 10) / 10) : pct(v))));
    });
    box.appendChild(bars);
    if (p.note) box.appendChild(el("div", { class: "note" }, p.note));
    const h2 = p.h2h;
    box.appendChild(el("div", { class: "h2h-box" },
      el("span", null, "vs you: ", el("b", null, h2 ? h2.hands + " hands" : "no hands yet")),
      h2 ? el("span", { class: h2.net >= 0 ? "pos" : "neg" }, el("b", null, signed(h2.net / bb, 1) + " bb")) : null,
      h2 && h2.showdowns ? el("span", { class: "muted" }, "showdowns " + h2.hero_won + "–" + h2.opp_won) : null));
    if (p.shown && p.shown.length) {
      box.appendChild(el("div", { class: "section-title", style: { margin: "12px 0 4px" } }, "Hands they showed"));
      box.appendChild(el("ul", { class: "shown-list" }, p.shown.slice(0, 5).map((s) => el("li", null,
        prettyCards(s.cards), " (" + s.position + ") " + s.line + (s.made ? " · " + s.made : "") + (s.won ? " · won" : "")))));
    }
    return box;
  }

  // ------------------------------------------------------------ history
  function renderHistoryView() {
    const st = S.state;
    const page = el("div", { class: "page" });
    page.appendChild(el("div", { class: "panel-head" }, el("div", null, el("div", { class: "panel-title" }, icon("history"), "Hand history"),
      el("div", { class: "panel-sub" }, "Your last " + st.history.length + " hands. Tap a hand to see every action."))));
    if (!st.history.length) { page.appendChild(el("div", { class: "panel empty" }, "No hands saved yet. Finished hands show up here.")); return page; }
    const list = el("div", { class: "hist" });
    st.history.forEach((hh) => {
      const open = S.histOpen.has(hh.no);
      const net = bbOf(hh.hero_net);
      const item = el("div", { class: "hist-item" });
      item.appendChild(el("div", { class: "hist-head", onclick: () => { if (open) S.histOpen.delete(hh.no); else S.histOpen.add(hh.no); render(); }, role: "button", tabindex: "0", "aria-expanded": open ? "true" : "false" },
        el("span", { class: "hist-no" }, "#" + hh.no),
        el("div", { class: "hist-mid" }, prettyCards(hh.hero_cards), el("span", { class: "badge" }, hh.hero_position),
          hh.board ? el("span", { class: "muted" }, "board ", prettyCards(hh.board)) : el("span", { class: "muted" }, "no flop"),
          el("span", { class: "muted small" }, "pot " + fmt(hh.pot) + " → " + hh.winners.join(", "))),
        el("b", { class: "mono " + (net >= 0 ? "pos" : "neg") }, signed(net, 1) + " bb")));
      if (open) {
        const body = el("div", { class: "hist-body" });
        const shown = Object.entries(hh.shown || {});
        if (shown.length) body.appendChild(el("div", { class: "small", style: { marginTop: "8px" } }, "Shown: ", shown.map(([n, c], i) => [i ? " · " : "", n + " ", prettyCards(c)])));
        body.appendChild(renderLog(hh.log || []));
        item.appendChild(body);
      }
      list.appendChild(item);
    });
    page.appendChild(list);
    return page;
  }

  // ------------------------------------------------------------- modals
  function openModal(kind, data) {
    S.modal = { kind, data: data || {} };
    if (kind === "show") { S.pick = []; S.pickText = ""; }
    renderModal();
    if (kind === "player" && !S.stats) loadStats();
  }
  function closeModal() { S.modal = null; renderModal(); }
  function renderModal() {
    const root = document.getElementById("modal-root");
    root.innerHTML = "";
    if (!S.modal) return;
    const m = S.modal;
    let body = null;
    if (m.kind === "help") body = modalHelp();
    else if (!S.state) return;
    else if (m.kind === "settings") body = modalSettings();
    else if (m.kind === "player") body = modalPlayer(m.data.seat);
    else if (m.kind === "show") body = modalShow(m.data.seat);
    if (!body) return;
    const back = el("div", { class: "modal-backdrop", onclick: (e) => { if (e.target === back) closeModal(); } }, body);
    root.appendChild(back);
  }
  function modalShell(title, wide, ...content) {
    return el("div", { class: "modal" + (wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
      el("div", { class: "modal-head" }, el("h2", null, title), el("button", { class: "icon-btn", onclick: closeModal, "aria-label": "Close" }, icon("x"))),
      ...content);
  }

  function modalHelp() {
    const terms = [
      ["Equity", "Your share of the pot if all cards were dealt out, against the hands the bot thinks your opponents hold."],
      ["Pot odds / need", "The equity you need for a call to break even: call ÷ (pot + call)."],
      ["EV", "Expected value: how many big blinds an option wins on average compared with folding."],
      ["Fold equity", "How often a bet or raise makes everyone fold, so you win the pot right away."],
      ["SPR", "Stack-to-pot ratio. Low SPR means getting all-in is close."],
      ["VPIP", "How often a player voluntarily puts money in pre-flop. High means loose."],
      ["PFR", "How often a player raises pre-flop. Close to VPIP means aggressive."],
      ["3-bet", "Re-raising a pre-flop raise."],
      ["AF", "Aggression factor: (bets + raises) ÷ calls after the flop."],
      ["C-bet", "Betting the flop after raising pre-flop."],
      ["WTSD / W$SD", "Went to showdown after seeing a flop / won at showdown."],
      ["Range grid", "13×13 chart of starting hands. Brighter means more likely. Suited hands are top-right, offsuit bottom-left."],
    ];
    const types = [["Nit / Rock", "Plays very few hands. Their bets mean strength; steal from them."], ["TAG / Regular", "Solid. Play standard poker."],
      ["LAG / Maniac", "Plays many hands aggressively. Call down lighter, let them bluff."], ["Calling station", "Calls too much. Value-bet thinner, almost never bluff."]];
    return modalShell("How Poker Help works", true,
      el("div", { class: "section-title", style: { marginTop: 0 } }, "Each hand"),
      el("ol", { class: "reasons" },
        el("li", null, "Pick your two cards and press ", el("b", null, "Deal"), ". The dealer button moves automatically."),
        el("li", null, "Tap what each player does, in order. ", el("b", null, "Folds to you"), " and ", el("b", null, "Checks to you"), " skip straight to your turn."),
        el("li", null, "When it's your turn the advisor shows the best play. Tap ", el("b", null, "Follow advice"), " or enter what you did."),
        el("li", null, "Enter the flop, turn and river when asked. At showdown, enter any shown cards and pick the winner."),
        el("li", null, "Made a mistake? ", el("b", null, "Undo"), " (or the U key) steps back one action at a time.")),
      el("div", { class: "section-title" }, "How the bot decides"),
      el("p", { class: "muted", style: { margin: 0 } }, "It estimates each opponent's range from their actions and session stats, then runs Monte Carlo simulations of the rest of the hand for every option: who folds, who calls, who re-raises. It recommends the option with the highest expected value. With few hands on a player it assumes typical tendencies, and trusts their real numbers more as data comes in."),
      el("div", { class: "section-title" }, "Keyboard shortcuts"),
      el("div", { class: "keys" }, [["F", "Fold"], ["K", "Check"], ["C", "Call / check"], ["R", "Bet / raise"], ["A", "All-in"], ["U", "Undo"]].map(([k, d]) => el("div", null, el("span", { class: "kbd" }, k), d))),
      el("div", { class: "section-title" }, "Player types"),
      el("dl", { class: "help-grid" }, types.map(([t, d]) => [el("dt", null, t), el("dd", null, d)])),
      el("div", { class: "section-title" }, "Glossary"),
      el("dl", { class: "help-grid" }, terms.map(([t, d]) => [el("dt", null, t), el("dd", null, d)])),
      el("div", { class: "modal-foot" }, el("button", { class: "btn primary", onclick: closeModal }, "Got it")));
  }

  function modalSettings() {
    const cfg = S.state.config;
    const f = { sb: cfg.sb, bb: cfg.bb, ante: cfg.ante, start_stack: cfg.start_stack, auto_rebuy: cfg.auto_rebuy };
    const num = (k, label) => el("div", { class: "field" }, el("label", null, label),
      el("input", { class: "input", type: "number", step: "any", value: f[k], oninput: (e) => { f[k] = Number(e.target.value); } }));
    const seg = el("div", { class: "seg" });
    [[0.6, "Fast"], [1.5, "Balanced"], [3.5, "Precise"]].forEach(([v, n]) => seg.appendChild(el("button", {
      class: Math.abs(S.prefs.budget - v) < 1e-9 ? "on" : "", onclick: () => { S.prefs.budget = v; savePrefs(); renderModal(); },
    }, n)));
    return modalShell("Settings", false,
      el("div", { class: "grid2" }, num("sb", "Small blind"), num("bb", "Big blind"), num("ante", "Ante"), num("start_stack", "Starting stack")),
      el("label", { class: "check", style: { marginTop: "12px" } },
        el("input", { type: "checkbox", checked: f.auto_rebuy, onchange: (e) => { f.auto_rebuy = e.target.checked; } }), "Rebuy busted players to the starting stack automatically"),
      el("div", { class: "section-title" }, "Display"),
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: S.prefs.hud, onchange: (e) => { S.prefs.hud = e.target.checked; savePrefs(); render(); } }), "Show the stats HUD on every seat"),
      el("label", { class: "check", style: { marginTop: "8px" } }, el("input", { type: "checkbox", checked: S.prefs.detail, onchange: (e) => { S.prefs.detail = e.target.checked; savePrefs(); render(); } }), "Show detailed maths in the advisor by default"),
      el("label", { class: "check", style: { marginTop: "8px" } }, el("input", { type: "checkbox", checked: S.prefs.tip, onchange: (e) => { S.prefs.tip = e.target.checked; savePrefs(); render(); } }), "Show the quick-start tip"),
      el("div", { class: "field", style: { marginTop: "14px" } }, el("label", null, "Advice precision (simulation time)"), seg),
      el("div", { class: "section-title" }, "Session data"),
      el("div", { class: "row" },
        el("button", { class: "btn sm", onclick: exportFile }, icon("download"), "Export session"),
        el("label", { class: "btn sm" }, icon("upload"), "Import session", el("input", { type: "file", accept: "application/json,.json", class: "hidden", onchange: importFile })),
        el("button", { class: "btn sm danger", onclick: async () => {
          if (!confirm("End this session? All stats will be deleted (export first if you want to keep them).")) return;
          await py("close"); S.state = null; S.advice = null; S.stats = null; S.view = "table"; store.del(STORE_KEY); closeModal(); render();
        } }, icon("trash"), "End session")),
      el("div", { class: "modal-foot" },
        el("button", { class: "btn ghost", onclick: closeModal }, "Cancel"),
        el("button", { class: "btn primary", onclick: async () => { if (await act("set_config", f)) { closeModal(); toast("Settings saved", true); } } }, "Save")));
  }

  function modalPlayer(seat) {
    const s = seatInfo(seat);
    const st = S.state;
    const inHand = !!st.hand;
    const f = { name: s.name, stack: s.stack };
    const r = s.raw || {};
    const detail = S.stats && S.stats.players.find((p) => p.pid === s.pid);
    const content = [];
    content.push(el("div", { class: "pcard-head" }, avatar(s),
      el("div", null, el("div", { class: "opp-name" }, youName(s.name, s.is_hero)),
        el("div", { class: "row", style: { gap: "6px", marginTop: "2px" } }, s.is_hero ? null : ptag(s.profile, true), el("span", { class: "faint small" }, (r.hands || 0) + " hands tracked")))));
    content.push(el("div", { class: "kpis" },
      kpi("VPIP", pct(r.vpip)), kpi("PFR", pct(r.pfr)), kpi("3-bet", pct(r.threebet)), kpi("AF", r.af === null || r.af === undefined ? "—" : fmt(Math.round(r.af * 10) / 10)),
      kpi("Fold to bet", pct(r.fold_to_bet)), kpi("C-bet", pct(r.cbet)), kpi("WTSD", pct(r.wtsd)), kpi("W$SD", pct(r.wsd))));
    if (s.is_hero) {
      const hero = st.hero;
      content.push(el("div", { class: "h2h-box" }, el("span", null, "This session: ", el("b", { class: hero.net_bb >= 0 ? "pos" : "neg" }, signed(hero.net_bb, 1) + " bb")),
        el("span", { class: "muted" }, hero.hands + " hands"), hero.followed_pct !== null ? el("span", { class: "muted" }, "advice followed " + pct(hero.followed_pct)) : null));
    } else {
      if (s.note) content.push(el("div", { class: "note" }, s.note));
      const h2 = s.h2h;
      content.push(el("div", { class: "h2h-box" }, el("span", null, "vs you: ", el("b", null, h2 ? h2.hands + " hands" : "no hands yet")),
        h2 ? el("b", { class: h2.net >= 0 ? "pos" : "neg" }, signed(bbOf(h2.net), 1) + " bb") : null,
        h2 && h2.showdowns ? el("span", { class: "muted" }, "showdowns " + h2.hero_won + "–" + h2.opp_won) : null));
      if (detail && detail.shown && detail.shown.length) {
        content.push(el("div", { class: "section-title" }, "Hands they showed"));
        content.push(el("ul", { class: "shown-list" }, detail.shown.slice(0, 6).map((x) => el("li", null, prettyCards(x.cards), " (" + x.position + ") " + x.line + (x.made ? " · " + x.made : "")))));
      }
    }
    content.push(el("div", { class: "section-title" }, "Edit seat"));
    content.push(el("div", { class: "grid2" },
      el("div", { class: "field" }, el("label", null, "Name"), el("input", { class: "input", value: f.name, oninput: (e) => { f.name = e.target.value; } })),
      el("div", { class: "field" }, el("label", null, "Stack"), el("input", { class: "input", type: "number", step: "any", value: f.stack, disabled: inHand, oninput: (e) => { f.stack = Number(e.target.value); } }))));
    if (inHand) content.push(el("p", { class: "faint small" }, "Stacks and seat changes can be edited between hands."));
    if (!s.is_hero) {
      content.push(el("div", { class: "row", style: { marginTop: "12px" } },
        el("button", { class: "btn sm", disabled: inHand, onclick: async () => { if (await act("update_seat", { seat, sitting_out: !s.sitting_out })) closeModal(); } }, s.sitting_out ? "Sit back in" : "Sit out"),
        el("button", { class: "btn sm danger", disabled: inHand, onclick: async () => {
          const name = prompt("Name of the new player in seat " + (seat + 1) + ":", "Seat " + (seat + 1));
          if (name === null) return;
          if (await act("update_seat", { seat, new_player: true, name })) closeModal();
        } }, "New player sat down (fresh stats)")));
    }
    content.push(el("div", { class: "modal-foot" },
      el("button", { class: "btn ghost", onclick: closeModal }, "Close"),
      el("button", { class: "btn primary", onclick: async () => {
        const args = { seat, name: f.name };
        if (!inHand) args.stack = f.stack;
        if (await act("update_seat", args)) closeModal();
      } }, "Save")));
    return modalShell(s.is_hero ? "Your stats" : "Player", false, ...content);
  }

  function modalShow(seat) {
    const p = handPlayer(seat);
    const used = usedCards();
    (p.shown_raw || []).forEach((c) => used.delete(c));
    return modalShell(p.name + " showed…", false,
      renderPicker(2, used, (enter) => { if (enter) submitShow(seat); }, renderModal),
      el("div", { class: "modal-foot" }, el("button", { class: "btn ghost", onclick: closeModal }, "Cancel"),
        el("button", { class: "btn primary", disabled: S.pick.length !== 2, onclick: () => submitShow(seat) }, "Save")));
  }
  async function submitShow(seat) {
    if (S.pick.length !== 2) return;
    const r = await act("show", { seat, cards: S.pick.map(cardStr).join("") });
    if (r) closeModal();
  }

  async function exportFile() {
    const r = await py("export");
    if (!r.ok) { toast(r.error); return; }
    const blob = new Blob([JSON.stringify(r.session, null, 1)], { type: "application/json" });
    const a = el("a", { href: URL.createObjectURL(blob), download: "poker-session-" + new Date().toISOString().slice(0, 10) + ".json" });
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
  function importFile(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      const data = safeJSON(reader.result);
      if (!data) { toast("That file isn't a valid session"); return; }
      S.modal = null;
      S.view = "table";
      const r = await act("load", { session: data });
      if (r) toast("Session imported", true);
    };
    reader.readAsText(file);
  }

  // ------------------------------------------------------------ keyboard
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && S.modal) { closeModal(); return; }
    if (S.modal || !S.state || !S.state.hand || S.view !== "table") return;
    const tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || e.metaKey || e.ctrlKey || e.altKey) return;
    const h = S.state.hand;
    const k = e.key.toLowerCase();
    if (k === "u") { act("undo"); e.preventDefault(); return; }
    if (h.finished || h.awaiting_board || !h.legal) return;
    const L = h.legal;
    if (k === "f" && L.actions.includes("fold")) doAction("fold");
    else if (k === "k" && L.actions.includes("check")) doAction("check");
    else if (k === "c") doAction(L.actions.includes("call") ? "call" : "check");
    else if (k === "a") doAction("allin");
    else if (k === "r" && (L.actions.includes("raise") || L.actions.includes("bet"))) doRaise();
    else return;
    e.preventDefault();
  });

  let resizeTimer = null;
  let lastMobile = window.matchMedia("(max-width: 760px)").matches;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const m = window.matchMedia("(max-width: 760px)").matches;
      if (m !== lastMobile && S.state && !S.modal) { lastMobile = m; render(); }
    }, 200);
  });
  renderTop();
})();
