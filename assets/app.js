/* Poker Help — web front end.
   All poker logic lives in the Python package `poker_bot`, which runs in a
   Web Worker through Pyodide.  This file only renders state and forwards
   user input to Python. */
(() => {
  "use strict";

  const VERSION = "1.0.0";
  const RANKS = "23456789TJQKA";
  const SUITS = "cdhs";
  const SUIT_SYM = { c: "♣", d: "♦", h: "♥", s: "♠" };
  const STORE_KEY = "pokerhelp.session.v1";
  const PREF_KEY = "pokerhelp.prefs.v1";
  const STREET_NAMES = { preflop: "Pre-flop", flop: "Flop", turn: "Turn", river: "River", showdown: "Showdown" };

  // ------------------------------------------------------------ storage
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); return true; } catch (e) { return false; } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };

  const S = {
    ready: false,
    state: null,
    advice: null,
    adviceKey: null,
    adviceBusy: false,
    busy: false,
    prefs: Object.assign({ budget: 1.5 }, safeJSON(store.get(PREF_KEY)) || {}),
    setup: { players: 6, hero: 0, sb: 0.5, bb: 1, stack: 100, names: [] },
    pick: [],
    pickText: "",
    raiseTo: null,
    raiseKey: null,
    potWinners: null,
    potKey: null,
    modal: null,
  };

  function safeJSON(t) { try { return t ? JSON.parse(t) : null; } catch (e) { return null; } }
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
    }, 250);
  }

  async function onReady() {
    S.ready = true;
    const saved = safeJSON(store.get(STORE_KEY));
    if (saved) {
      const r = await py("load", { session: saved });
      if (r.ok) S.state = r.state;
      else toast("Saved session could not be loaded: " + r.error);
    }
    render();
    maybeAdvise();
  }

  // Call a state-changing Python method.
  async function act(method, args, opts) {
    if (S.busy) return null;
    S.busy = true;
    const r = await py(method, args);
    S.busy = false;
    if (!r.ok) {
      toast(r.error || "Something went wrong");
      return null;
    }
    if ("state" in r) S.state = r.state;
    if (!opts || !opts.keepPick) { S.pick = []; S.pickText = ""; }
    persist();
    render();
    maybeAdvise();
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
  function fmt(x) {
    if (x === null || x === undefined || isNaN(x)) return "—";
    const v = Math.round(Number(x) * 100) / 100;
    return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/0$/, "");
  }
  function pct(x, digits) {
    if (x === null || x === undefined || isNaN(x)) return "—";
    return (100 * x).toFixed(digits === undefined ? 0 : digits) + "%";
  }
  function signed(x) { return (x >= 0 ? "+" : "") + fmt(x); }
  function cardStr(c) { return RANKS[c >> 2] + SUITS[c & 3]; }
  function cardEl(c, cls) {
    if (c === null || c === undefined) return el("div", { class: "pcard empty " + (cls || "") });
    const s = SUITS[c & 3];
    return el("div", { class: "pcard " + (cls || ""), title: cardStr(c) },
      el("span", { class: "r suit-" + s }, RANKS[c >> 2] === "T" ? "10" : RANKS[c >> 2]),
      el("span", { class: "s suit-" + s }, SUIT_SYM[s]));
  }
  // Text like "A♥ K♦" -> coloured spans
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
  function seatInfo(seat) {
    const st = S.state;
    return st && st.seats.find((s) => s.seat === seat);
  }
  function handPlayer(seat) {
    const h = S.state && S.state.hand;
    return h && h.players.find((p) => p.seat === seat);
  }
  function roundChip(x) {
    const unit = (S.state && S.state.config.sb) || 0.5;
    const step = unit >= 1 ? unit : (unit >= 0.5 ? 0.5 : unit);
    return Math.max(step, Math.round(x / step) * step);
  }

  // ------------------------------------------------------------ advice
  function adviceKeyFor(h) {
    return [S.state.hand_no, h.board.length, h.log.length, h.to_act].join(":");
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
    if (S.adviceKey !== key) return;     // state moved on meanwhile
    S.adviceBusy = false;
    if (r.ok) S.advice = r.advice;
    else toast(r.error);
    if (S.advice && S.advice.recommendation && (S.advice.recommendation.to)) {
      S.raiseTo = S.advice.recommendation.to;
      S.raiseKey = key;
    }
    render();
  }

  // ------------------------------------------------------------- render
  function render() {
    renderTop();
    const app = document.getElementById("app");
    if (!S.ready) return;
    app.innerHTML = "";
    if (!S.state) app.appendChild(renderSetup());
    else app.appendChild(renderMain());
    renderModal();
  }

  function renderTop() {
    const nav = document.getElementById("top-actions");
    nav.innerHTML = "";
    const st = S.state;
    if (!st) return;
    nav.appendChild(el("span", { class: "session-pill" },
      "Hands ", el("b", null, String(st.hand_no)), " · You ",
      el("b", { class: st.hero_net >= 0 ? "pos" : "neg" }, signed(st.hero_net))));
    nav.appendChild(el("button", { class: "btn small", onclick: () => openModal("stats") }, "Opponent stats"));
    nav.appendChild(el("button", { class: "btn small", onclick: () => openModal("history") }, "History"));
    nav.appendChild(el("button", { class: "btn small ghost", onclick: () => openModal("settings") }, "Settings"));
  }

  // -------------------------------------------------------------- setup
  function renderSetup() {
    const su = S.setup;
    const n = su.players;
    if (su.hero >= n) su.hero = 0;
    const wrap = el("div", { class: "setup" });
    wrap.appendChild(el("h1", null, "Start a session"));
    wrap.appendChild(el("p", { class: "lead" },
      "Enter what happens at your table and the bot works out the mathematically best play at every street. It runs Monte Carlo simulations against each opponent's estimated range, and tracks how every opponent plays so its reads improve over the session."));

    const playersSeg = el("div", { class: "seg" });
    for (let i = 2; i <= 10; i++) {
      playersSeg.appendChild(el("button", { class: i === n ? "on" : "", onclick: () => { su.players = i; render(); } }, String(i)));
    }
    const heroSel = el("select", { class: "input", onchange: (e) => { su.hero = Number(e.target.value); render(); } });
    for (let i = 0; i < n; i++) heroSel.appendChild(el("option", { value: i, selected: i === su.hero }, "Seat " + (i + 1)));

    const c1 = el("div", { class: "card" },
      el("h2", null, "Table"),
      el("div", { class: "field" }, el("label", null, "Players at the table"), playersSeg),
      el("div", { class: "grid3", style: { marginTop: "14px" } },
        el("div", { class: "field" }, el("label", null, "Your seat"), heroSel),
        el("div", { class: "field" }, el("label", null, "Small blind"),
          el("input", { class: "input", type: "number", step: "any", min: "0", value: su.sb, oninput: (e) => { su.sb = Number(e.target.value); } })),
        el("div", { class: "field" }, el("label", null, "Big blind"),
          el("input", { class: "input", type: "number", step: "any", min: "0", value: su.bb, oninput: (e) => { su.bb = Number(e.target.value); } })),
        el("div", { class: "field" }, el("label", null, "Starting stack"),
          el("input", { class: "input", type: "number", step: "any", min: "1", value: su.stack, oninput: (e) => { su.stack = Number(e.target.value); } })),
      ));

    const names = el("div", { class: "names-grid" });
    for (let i = 0; i < n; i++) {
      const isHero = i === su.hero;
      names.appendChild(el("input", {
        class: "input" + (isHero ? " hero" : ""), placeholder: isHero ? "You" : "Seat " + (i + 1),
        value: su.names[i] || "", "aria-label": "Name for seat " + (i + 1),
        oninput: (e) => { su.names[i] = e.target.value; },
      }));
    }
    const c2 = el("div", { class: "card" },
      el("h2", null, "Player names ", el("span", { class: "muted small" }, "(optional: names help you keep track of who is who)")),
      names);

    const start = el("button", {
      class: "btn primary", onclick: async () => {
        if (!(su.bb > 0) || !(su.sb >= 0) || !(su.stack > 0)) { toast("Check the blinds and stack"); return; }
        await act("new_session", {
          players: n, hero_seat: su.hero, sb: su.sb, bb: su.bb, start_stack: su.stack,
          names: Array.from({ length: n }, (_, i) => su.names[i] || ""),
        });
      },
    }, "Start session");
    const importBtn = el("label", { class: "btn" }, "Import session…",
      el("input", { type: "file", accept: "application/json,.json", class: "hidden", onchange: importFile }));

    const how = el("div", { class: "how" },
      el("div", null, el("b", null, "1 · Enter the action"), "Pick your two cards, then click each player's decision in turn, and the flop, turn and river."),
      el("div", null, el("b", null, "2 · Get the maths"), "When it's your turn you get equity, pot odds, the EV of every option and a recommended play."),
      el("div", null, el("b", null, "3 · It learns the table"), "VPIP, PFR, 3-bet, aggression and fold-to-bet are tracked per player and feed into their estimated ranges."));

    wrap.appendChild(c1);
    wrap.appendChild(c2);
    wrap.appendChild(el("div", { class: "row", style: { marginTop: "16px", justifyContent: "space-between" } }, importBtn, start));
    wrap.appendChild(how);
    return wrap;
  }

  // --------------------------------------------------------------- main
  function renderMain() {
    const layout = el("div", { class: "layout" });
    const left = el("div", { class: "col" });
    const right = el("div", { class: "col" });
    left.appendChild(renderTable());
    left.appendChild(renderControl());
    right.appendChild(renderAdvisor());
    right.appendChild(renderReads());
    layout.appendChild(left);
    layout.appendChild(right);
    const frag = document.createDocumentFragment();
    frag.appendChild(layout);
    frag.appendChild(el("footer", { class: "foot" },
      "Poker Help · the advice engine is Python (poker_bot) running in your browser · your session is saved only in this browser"));
    return frag;
  }

  function renderTable() {
    const st = S.state;
    const h = st.hand;
    const seats = st.seats;
    const n = seats.length;
    const wrap = el("div", { class: "table-wrap" });
    const felt = el("div", { class: "felt" });
    wrap.appendChild(felt);
    const center = el("div", { class: "felt-center" });
    if (h) {
      center.appendChild(el("div", { class: "street-label" }, STREET_NAMES[h.street] || h.street));
      const board = el("div", { class: "board" });
      for (let i = 0; i < 5; i++) board.appendChild(h.board[i] !== undefined ? cardEl(h.board[i]) : cardEl(null));
      center.appendChild(board);
      center.appendChild(el("div", { class: "pot-label" }, "Pot " + fmt(h.pot)));
    } else {
      center.appendChild(el("div", { class: "street-label" }, "Between hands"));
      center.appendChild(el("div", { class: "pot-label" }, "Hand #" + (st.hand_no + 1)));
    }
    felt.appendChild(center);

    const mobile = window.matchMedia("(max-width: 640px)").matches;
    const rx = mobile ? 40 : 43, ry = mobile ? 42 : 41;
    seats.forEach((s) => {
      const k = (s.seat - st.hero_seat + n) % n;
      const ang = Math.PI / 2 + (k * 2 * Math.PI) / n;
      const x = 50 + rx * Math.cos(ang), y = 50 + ry * Math.sin(ang);
      const hp = h && h.players.find((p) => p.seat === s.seat);
      const inHand = !!hp;
      const cls = ["seat"];
      if (s.is_hero) cls.push("hero");
      if (h && h.to_act === s.seat) cls.push("to-act");
      if (hp && hp.folded) cls.push("folded");
      if (s.sitting_out || (h && !inHand)) cls.push("out");
      const position = hp ? hp.position : s.next_position;
      const stack = hp ? hp.stack : s.stack;
      const node = el("div", {
        class: cls.join(" "), style: { left: x + "%", top: y + "%" },
        title: "Click to edit this seat", onclick: () => openModal("seat", { seat: s.seat }),
      },
        el("div", { class: "seat-name" }, youName(s.name, s.is_hero)),
        el("div", { class: "seat-meta" },
          position ? el("span", { class: "badge" + (position === "BTN" ? " btn-pos" : "") }, position) : null,
          el("span", { class: "seat-stack" }, s.sitting_out ? "sitting out" : fmt(stack)),
          hp && hp.all_in ? el("span", { class: "badge allin" }, "ALL-IN") : null,
          !s.is_hero && s.profile && s.profile.key !== "unknown" ? el("span", { class: "ptag " + s.profile.key, title: s.profile.label }, shortProfile(s.profile.key)) : null),
      );
      if (s.is_hero && h && h.hero_cards.length) {
        node.appendChild(el("div", { class: "seat-cards" }, h.hero_cards.map((c) => cardEl(c, "sm"))));
      } else if (hp && hp.shown_raw) {
        node.appendChild(el("div", { class: "seat-cards" }, hp.shown_raw.map((c) => cardEl(c, "sm"))));
      }
      wrap.appendChild(node);
      if (hp && hp.street_bet > 0) {
        const bx = 50 + rx * 0.58 * Math.cos(ang), by = 50 + ry * 0.55 * Math.sin(ang);
        wrap.appendChild(el("div", { class: "bet-chip", style: { left: bx + "%", top: by + "%" } }, fmt(hp.street_bet)));
      }
      const isButton = h ? h.button === s.seat : st.button === s.seat;
      if (isButton) {
        const dx = 50 + rx * 0.74 * Math.cos(ang + 0.22), dy = 50 + ry * 0.7 * Math.sin(ang + 0.22);
        wrap.appendChild(el("div", { class: "dealer-btn", style: { left: dx + "%", top: dy + "%" } }, "D"));
      }
    });
    return wrap;
  }

  function youName(name, isHero) {
    return isHero && !/^you$/i.test(name.trim()) ? name + " (you)" : name;
  }

  function shortProfile(key) {
    return { nit: "NIT", rock: "ROCK", tag: "TAG", lag: "LAG", maniac: "MANIAC", station: "STATION",
      loose_passive: "LOOSE", reg: "REG", reg_passive: "REG-P" }[key] || "";
  }

  // ---------------------------------------------------------- control
  function renderControl() {
    const st = S.state;
    const h = st.hand;
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

  function renderPicker(need, used, onChange) {
    const box = el("div");
    const sel = el("div", { class: "picker-sel" });
    for (let i = 0; i < need; i++) sel.appendChild(cardEl(S.pick[i] !== undefined ? S.pick[i] : null, "sm"));
    const text = el("input", {
      class: "input mono", style: { width: "130px" }, placeholder: need === 1 ? "e.g. 7d" : need === 2 ? "e.g. AhKd" : "e.g. Ks7d2c",
      value: S.pickText, "aria-label": "Type cards",
      oninput: (e) => {
        S.pickText = e.target.value;
        const cards = parseCards(e.target.value);
        if (cards && cards.length <= need && cards.every((c) => !used.has(c)) && new Set(cards).size === cards.length) {
          S.pick = cards;
          refreshPicker();
        }
      },
      onkeydown: (e) => { if (e.key === "Enter" && S.pick.length === need && onChange) onChange(true); },
    });
    box.appendChild(el("div", { class: "picker-head" }, sel,
      el("div", { class: "row" }, text, el("button", { class: "btn small ghost", onclick: () => { S.pick = []; S.pickText = ""; refreshPicker(); } }, "Clear"))));
    const grid = el("div", { class: "picker", role: "group", "aria-label": "Card picker" });
    for (const s of [3, 2, 1, 0]) {
      for (let r = 12; r >= 0; r--) {
        const c = (r << 2) | s;
        const on = S.pick.includes(c);
        const suit = SUITS[s];
        grid.appendChild(el("button", {
          class: "pick" + (on ? " sel" : ""), disabled: used.has(c), title: cardStr(c),
          onclick: () => {
            if (on) S.pick = S.pick.filter((x) => x !== c);
            else if (S.pick.length < need) S.pick = S.pick.concat([c]);
            else S.pick = S.pick.slice(0, need - 1).concat([c]);
            S.pickText = S.pick.map(cardStr).join("");
            refreshPicker();
            if (S.pick.length === need && onChange) onChange(false);
          },
        }, el("span", { class: "suit-" + suit }, RANKS[r] === "T" ? "10" : RANKS[r]), el("span", { class: "s suit-" + suit }, SUIT_SYM[suit])));
      }
    }
    box.appendChild(grid);
    return box;
  }
  let refreshPicker = () => render();

  function renderDeal() {
    const st = S.state;
    const hero = seatInfo(st.hero_seat);
    const panel = el("div", { class: "panel" });
    const btnSel = el("select", { class: "input", style: { width: "auto" }, "aria-label": "Dealer button",
      onchange: (e) => act("set_button", { seat: Number(e.target.value) }, { keepPick: true }) });
    st.seats.filter((s) => !s.sitting_out).forEach((s) => btnSel.appendChild(el("option", { value: s.seat, selected: s.seat === st.button }, s.name)));
    panel.appendChild(el("div", { class: "panel-head" },
      el("div", null, el("div", { class: "panel-title" }, "Deal hand #" + (st.hand_no + 1)),
        el("div", { class: "panel-sub" }, "You are in the ", el("b", null, hero.next_position || "?"), ". Pick your two hole cards.")),
      el("div", { class: "row" }, el("span", { class: "label" }, "Button"), btnSel)));
    const deal = el("button", { class: "btn primary", disabled: S.pick.length !== 2, onclick: () => dealHand() }, "Deal");
    const go = (fromEnter) => { if (fromEnter) dealHand(); };
    panel.appendChild(renderPicker(2, new Set(), go));
    panel.appendChild(el("div", { class: "row", style: { marginTop: "12px", justifyContent: "flex-end" } }, deal));
    refreshPicker = () => render();
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
      el("div", null, el("div", { class: "panel-title" }, "Enter the " + name),
        el("div", { class: "panel-sub" }, need === 3 ? "Pick the three flop cards." : "Pick the " + name + " card.")),
      el("button", { class: "btn small ghost", onclick: () => act("undo") }, "Undo last action")));
    const confirm = el("button", { class: "btn primary", disabled: S.pick.length !== need, onclick: () => submitBoard() }, "Confirm " + name);
    panel.appendChild(renderPicker(need, usedCards(), (fromEnter) => { if (fromEnter || need === 1) submitBoard(); }));
    panel.appendChild(el("div", { class: "row", style: { marginTop: "12px", justifyContent: "flex-end" } }, confirm));
    panel.appendChild(renderLog());
    refreshPicker = () => render();
    return panel;
  }
  async function submitBoard() {
    const h = S.state.hand;
    if (S.pick.length !== h.awaiting_board) return;
    await act("board", { cards: S.pick.map(cardStr).join("") });
  }

  function defaultRaise(h, L) {
    const bb = h.bb;
    const key = adviceKeyFor(h);
    if (S.raiseKey === key && S.raiseTo) return S.raiseTo;
    let to;
    if (h.street === "preflop") {
      const limpers = h.log.filter((l) => l.street === "preflop" && l.action === "call").length;
      to = L.current_bet <= bb + 1e-9 ? bb * 2.5 + bb * limpers : L.current_bet * 3;
    } else {
      to = L.current_bet > 0 ? L.current_bet * 3 : L.pot * 0.66;
    }
    return clampTo(roundChip(to), L);
  }
  function clampTo(x, L) { return Math.min(L.max_to, Math.max(L.min_to, x)); }

  function sizeOptions(h, L) {
    const out = [];
    const bb = h.bb;
    if (h.street === "preflop") {
      const base = L.current_bet <= bb + 1e-9 ? bb : L.current_bet;
      const mults = L.current_bet <= bb + 1e-9 ? [2, 2.5, 3, 4] : [2.5, 3, 4];
      mults.forEach((m) => out.push([m + "x", roundChip(base * m)]));
    } else if (L.current_bet === 0) {
      [["1/3", 1 / 3], ["1/2", 0.5], ["2/3", 2 / 3], ["Pot", 1]].forEach(([n, f]) => out.push([n, roundChip(L.pot * f)]));
    } else {
      [["1/2 pot", 0.5], ["Pot", 1]].forEach(([n, f]) => out.push([n, roundChip(L.current_bet + f * (L.pot + L.to_call))]));
      out.push(["3x", roundChip(L.current_bet * 3)]);
    }
    out.unshift(["Min", L.min_to]);
    return out.map(([n, v]) => [n, clampTo(v, L)]);
  }

  function renderActions() {
    const st = S.state;
    const h = st.hand;
    const L = h.legal;
    const actor = handPlayer(L.seat);
    const isHero = L.seat === st.hero_seat;
    const panel = el("div", { class: "panel" });
    const key = adviceKeyFor(h);
    let recAct = null;
    if (isHero && S.advice && S.adviceKey === key && S.advice.recommendation) recAct = S.advice.recommendation.action;

    panel.appendChild(el("div", { class: "to-act-line" },
      el("span", { class: "who" }, youName(actor.name, isHero)),
      el("span", { class: "badge" + (actor.position === "BTN" ? " btn-pos" : "") }, actor.position),
      el("span", { class: "facing" }, L.to_call > 0 ? "to call " + fmt(L.to_call) + " · pot " + fmt(L.pot) : "pot " + fmt(L.pot)),
      el("span", { class: "muted small" }, "stack " + fmt(L.stack))));

    const acts = el("div", { class: "actions" });
    const has = (a) => L.actions.includes(a);
    const button = (label, kbd, cls, rec, fn) => el("button", { class: "btn " + cls + (rec ? " recommended" : ""), disabled: S.busy, onclick: fn },
      label, kbd ? el("span", { class: "kbd" }, kbd) : null);
    if (has("fold")) acts.appendChild(button("Fold", "F", "act-fold", recAct === "fold", () => doAction("fold")));
    if (has("check")) acts.appendChild(button("Check", "K", "act-call", recAct === "check", () => doAction("check")));
    if (has("call")) acts.appendChild(button("Call " + fmt(L.to_call), "C", "act-call", recAct === "call", () => doAction("call")));
    if (has("raise") || has("bet")) {
      const verb = has("bet") ? "Bet" : "Raise to";
      const val = defaultRaise(h, L);
      const input = el("input", { class: "input", type: "number", step: "any", min: L.min_to, max: L.max_to, value: val,
        "aria-label": verb + " amount",
        oninput: (e) => { S.raiseTo = Number(e.target.value); S.raiseKey = key; rb.firstChild.textContent = verb + " " + fmt(S.raiseTo) + " "; },
        onkeydown: (e) => { if (e.key === "Enter") doRaise(); } });
      const rb = button(verb + " " + fmt(val) + " ", "R", "act-raise", recAct === "raise" || recAct === "bet", () => doRaise());
      const sizes = el("div", { class: "sizes" });
      sizeOptions(h, L).forEach(([n, v]) => sizes.appendChild(el("button", {
        class: "chip-btn" + (Math.abs(v - val) < 1e-9 ? " on" : ""), title: fmt(v),
        onclick: () => { S.raiseTo = v; S.raiseKey = key; render(); },
      }, n)));
      acts.appendChild(el("div", { class: "raise-box" }, input, rb, sizes));
    }
    if (has("allin")) acts.appendChild(button("All-in " + fmt(L.max_to), "A", "act-allin", recAct === "allin", () => doAction("allin")));
    panel.appendChild(acts);
    panel.appendChild(el("div", { class: "row", style: { marginTop: "10px", justifyContent: "space-between" } },
      el("span", { class: "muted small" }, isHero ? "Enter what you actually did." : "Click what " + actor.name + " did. Keys: F fold · K check · C call · R raise · A all-in · U undo"),
      el("div", { class: "row" },
        el("button", { class: "btn small ghost", disabled: !h.can_undo && false, onclick: () => act("undo") }, "Undo"),
        el("button", { class: "btn small ghost danger", onclick: () => { if (confirm("Discard this hand without recording it?")) act("abandon"); } }, "Discard hand"))));
    panel.appendChild(renderLog());
    return panel;
  }

  async function doAction(a, amount) {
    const h = S.state.hand;
    if (!h || h.to_act === null) return;
    S.raiseTo = null; S.raiseKey = null;
    await act("act", { seat: h.to_act, action: a, amount: amount });
  }
  function doRaise() {
    const h = S.state.hand;
    const L = h.legal;
    const v = Number(S.raiseKey === adviceKeyFor(h) && S.raiseTo ? S.raiseTo : defaultRaise(h, L));
    if (!(v > L.current_bet)) { toast("Amount must be more than the current bet (" + fmt(L.current_bet) + ")"); return; }
    doAction(L.actions.includes("bet") ? "bet" : "raise", v);
  }

  function renderLog() {
    const h = S.state.hand;
    const box = el("div", { class: "log" });
    if (!h || !h.log.length) {
      box.appendChild(el("div", { class: "muted" }, "Blinds posted. No actions yet."));
      return box;
    }
    let street = null;
    h.log.forEach((l) => {
      if (l.street !== street) {
        street = l.street;
        box.appendChild(el("div", { class: "log-street" }, STREET_NAMES[street] || street));
      }
      box.appendChild(el("div", { class: "log-row" }, el("span", { class: "n" }, l.name + " (" + l.position + ")"), el("span", null, l.text)));
    });
    setTimeout(() => { box.scrollTop = box.scrollHeight; }, 0);
    return box;
  }

  function renderResult() {
    const st = S.state;
    const h = st.hand;
    const panel = el("div", { class: "panel" });
    if (h.result.reason === "fold") {
      const w = handPlayer(h.result.winners[0]);
      panel.appendChild(el("div", { class: "panel-head" },
        el("div", null, el("div", { class: "panel-title" }, w.name + " wins the pot of " + fmt(h.pot)),
          el("div", { class: "panel-sub" }, "Everyone else folded. Save the hand to update the stats."))));
      panel.appendChild(el("div", { class: "row", style: { justifyContent: "flex-end" } },
        el("button", { class: "btn ghost", onclick: () => act("undo") }, "Undo"),
        el("button", { class: "btn primary", onclick: () => finishHand() }, "Save & next hand")));
      panel.appendChild(renderLog());
      return panel;
    }
    // showdown
    const potKey = st.hand_no + ":" + h.log.length + ":" + h.players.map((p) => p.shown || "").join("|");
    if (S.potKey !== potKey) {
      S.potKey = potKey;
      S.potWinners = h.auto_winners ? h.auto_winners.map((x) => x.slice()) : h.pots.map(() => []);
    }
    panel.appendChild(el("div", { class: "panel-head" },
      el("div", null, el("div", { class: "panel-title" }, "Showdown"),
        el("div", { class: "panel-sub" }, "Enter any cards that were shown (this teaches the bot how they play), then pick who won."))));
    const live = h.players.filter((p) => !p.folded);
    const shownBox = el("div");
    live.forEach((p) => {
      const isHero = p.seat === st.hero_seat;
      shownBox.appendChild(el("div", { class: "showdown-opp" },
        el("span", null, el("b", null, p.name), " ", el("span", { class: "badge" }, p.position)),
        isHero ? prettyCards(h.hero_pretty)
          : p.shown ? el("span", { class: "row" }, prettyCards(p.shown), el("button", { class: "btn small ghost", onclick: () => openModal("show", { seat: p.seat }) }, "Change"))
            : el("button", { class: "btn small", onclick: () => openModal("show", { seat: p.seat }) }, "Enter shown cards")));
    });
    panel.appendChild(shownBox);
    const pots = el("div", { class: "pots" });
    h.pots.forEach((pot, i) => {
      const row = el("div", { class: "pot-row" },
        el("div", { class: "row", style: { justifyContent: "space-between", marginBottom: "8px" } },
          el("b", null, (i === 0 ? "Main pot" : "Side pot " + i) + " · " + fmt(pot.amount)),
          el("span", { class: "muted small" }, pot.eligible.length > 1 ? "Who won? (pick two or more for a split)" : "Returned to the only player in it")));
      const seg = el("div", { class: "row" });
      pot.eligible.forEach((seat) => {
        const on = (S.potWinners[i] || []).includes(seat);
        seg.appendChild(el("button", { class: "chip-btn" + (on ? " on" : ""), onclick: () => {
          const cur = S.potWinners[i] || [];
          S.potWinners[i] = on ? cur.filter((x) => x !== seat) : cur.concat([seat]);
          render();
        } }, handPlayer(seat).name));
      });
      row.appendChild(seg);
      pots.appendChild(row);
    });
    panel.appendChild(pots);
    if (h.auto_winners) panel.appendChild(el("div", { class: "muted small" }, "Winners were filled in from the shown cards."));
    const ready = h.pots.every((pot, i) => pot.eligible.length === 1 || (S.potWinners[i] || []).length > 0);
    panel.appendChild(el("div", { class: "row", style: { justifyContent: "flex-end", marginTop: "8px" } },
      el("button", { class: "btn ghost", onclick: () => act("undo") }, "Undo"),
      el("button", { class: "btn primary", disabled: !ready, onclick: () => finishHand(S.potWinners) }, "Save & next hand")));
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
      const net = r.result.hero_net;
      toast("Hand saved. " + (net > 0 ? "You won " + fmt(net) : net < 0 ? "You lost " + fmt(-net) : "You broke even") + ".", true);
      render();
    }
  }

  // ---------------------------------------------------------- advisor
  function renderAdvisor() {
    const st = S.state;
    const h = st.hand;
    const panel = el("div", { class: "panel advisor" });
    panel.appendChild(el("div", { class: "panel-head" },
      el("div", { class: "panel-title" }, "Advisor"),
      el("span", { class: "panel-sub" }, h ? (STREET_NAMES[h.street] || "") : "")));
    if (!h) {
      panel.appendChild(el("div", { class: "empty-state" }, el("div", { class: "big" }, "♠"),
        el("div", null, "Deal a hand to get advice. When it's your turn the bot simulates thousands of run-outs against each opponent's likely range.")));
      return panel;
    }
    if (h.finished) {
      panel.appendChild(el("div", { class: "empty-state" }, el("div", null, "Hand over. Save the result: every action you entered updates the opponent profiles.")));
      return panel;
    }
    const hero = handPlayer(st.hero_seat);
    if (hero && hero.folded) {
      panel.appendChild(el("div", { class: "empty-state" }, el("div", null, "You folded. Keep entering the other players' actions (and any shown cards) so the bot learns how they play.")));
      return panel;
    }
    if (h.awaiting_board) {
      panel.appendChild(el("div", { class: "waiting" }, "Enter the board cards to continue."));
      if (h.hero_hand_desc) panel.appendChild(el("div", null, "You have ", el("b", null, h.hero_hand_desc), " with ", prettyCards(h.hero_pretty), "."));
      return panel;
    }
    if (h.to_act !== st.hero_seat) {
      const actor = handPlayer(h.to_act);
      panel.appendChild(el("div", { class: "waiting" }, el("span", { class: "spinner sm" }), "Waiting for " + actor.name + " (" + actor.position + ") to act…"));
      panel.appendChild(el("div", { class: "muted" }, "Your hand: ", prettyCards(h.hero_pretty), h.hero_hand_desc ? " · " + h.hero_hand_desc : ""));
      return panel;
    }
    if (S.adviceBusy || !S.advice) {
      panel.appendChild(el("div", { class: "waiting" }, el("span", { class: "spinner sm" }), "Simulating run-outs against your opponents' ranges…"));
      return panel;
    }
    const a = S.advice;
    if (a.status !== "ok") {
      panel.appendChild(el("div", { class: "muted" }, "No advice available (" + a.status + ")."));
      return panel;
    }
    const rec = a.recommendation;
    const kind = rec.action === "fold" ? "fold" : (rec.action === "check" || rec.action === "call") ? "passive" : "";
    panel.appendChild(el("div", { class: "rec " + kind },
      el("div", null,
        el("div", { class: "rec-label" }, "Recommended"),
        el("div", { class: "rec-action" }, rec.label),
        el("div", { class: "rec-ev" }, "EV " + signed(rec.ev_bb) + " bb" + (rec.action !== "fold" ? " vs folding" : ""))),
      el("span", { class: "conf " + a.confidence }, a.confidence === "clear" ? "Clear" : a.confidence === "moderate" ? "Likely" : "Close call")));

    // equity bar
    const eq = a.equity;
    const bar = el("div", { class: "eqbar" });
    const track = el("div", { class: "eqbar-track" }, el("div", { class: "eqbar-fill", style: { width: Math.max(1, eq * 100) + "%" } }));
    if (a.to_call > 0) track.appendChild(el("div", { class: "eqbar-need", style: { left: (a.pot_odds * 100) + "%" }, "data-label": "need " + pct(a.pot_odds) }));
    bar.appendChild(track);
    bar.appendChild(el("div", { class: "eqbar-legend" },
      el("span", null, "Your equity ", el("b", { style: { color: "var(--green)" } }, pct(eq, 1))),
      el("span", null, "±" + pct(1.96 * a.equity_se, 1) + " · " + a.iterations.toLocaleString() + " sims")));
    panel.appendChild(bar);

    const kpis = el("div", { class: "kpis" },
      kpi("Pot", fmt(a.pot)),
      kpi("To call", a.to_call > 0 ? fmt(a.to_call) : "—"),
      kpi("Pot odds", a.to_call > 0 ? pct(a.pot_odds) : "—"),
      a.street === "preflop" ? kpi("Hand rank", "top " + pct(a.hand_percentile)) : kpi("SPR", fmt(a.spr)));
    panel.appendChild(kpis);

    if (a.hand_desc || a.draws.length) {
      panel.appendChild(el("div", { style: { marginTop: "10px" } },
        prettyCards(a.hero_cards), " · ", el("b", null, a.hand_desc || ""),
        a.draws.length ? el("span", { class: "muted" }, " + " + a.draws.join(", ") + (a.outs ? " (" + a.outs + " outs)" : "")) : null));
    }

    // EV table
    panel.appendChild(el("div", { class: "section-title" }, "Expected value of each option"));
    const maxAbs = Math.max(0.01, ...a.options.map((o) => Math.abs(o.ev_bb)));
    const tbl = el("table", { class: "ev-table" },
      el("thead", null, el("tr", null, el("th", null, "Option"), el("th", { style: { textAlign: "right" } }, "EV (bb)"), el("th", null, ""),
        el("th", { style: { textAlign: "right" } }, "Folds"), el("th", { style: { textAlign: "right" } }, "Eq. if called"))));
    const tb = el("tbody");
    a.options.forEach((o) => {
      const w = (Math.abs(o.ev_bb) / maxAbs) * 50;
      const isBest = o.label === rec.label;
      tb.appendChild(el("tr", { class: isBest ? "best" : "" },
        el("td", null, o.label),
        el("td", { class: "num" }, signed(o.ev_bb)),
        el("td", null, el("div", { class: "evbar" }, el("div", { class: "mid" }),
          el("div", { class: "b " + (o.ev_bb >= 0 ? "p" : "n"), style: { width: w + "%" } }))),
        el("td", { class: "num" }, o.p_fold !== null && o.p_fold !== undefined ? pct(o.p_fold) : ""),
        el("td", { class: "num" }, o.action === "fold" ? "" : pct(o.equity_called))));
    });
    tbl.appendChild(tb);
    panel.appendChild(el("div", { class: "table-scroll" }, tbl));

    panel.appendChild(el("div", { class: "section-title" }, "Why"));
    panel.appendChild(el("ul", { class: "reasons" }, a.reasons.map((r) => el("li", null, r))));

    if (a.opponents.length) {
      panel.appendChild(el("div", { class: "section-title" }, "Estimated opponent ranges"));
      const g = el("div", { class: "opp-grid" });
      a.opponents.forEach((o) => g.appendChild(renderOpp(o)));
      panel.appendChild(g);
    }
    return panel;
  }
  function kpi(k, v) { return el("div", { class: "kpi" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v)); }

  function renderOpp(o) {
    const s = o.stats;
    const card = el("div", { class: "opp" },
      el("div", { class: "opp-head" },
        el("div", { style: { minWidth: 0 } }, el("div", { class: "opp-name" }, o.name), el("span", { class: "badge" }, o.position), " ",
          o.profile.key !== "unknown" ? el("span", { class: "ptag " + o.profile.key }, o.profile.label) : el("span", { class: "ptag" }, "No reads yet")),
        el("div", { style: { textAlign: "right" } }, el("div", { class: "opp-eq" }, pct(o.hu_equity)), el("div", { class: "faint small" }, "your equity"))));
    const grid = el("div", { class: "rgrid", "aria-label": "Range grid for " + o.name });
    for (let r = 0; r < 13; r++) {
      for (let c = 0; c < 13; c++) {
        const v = o.grid[r][c];
        const hi = RANKS[12 - Math.min(r, c)], lo = RANKS[12 - Math.max(r, c)];
        const lab = r === c ? hi + lo : hi + lo + (r < c ? "s" : "o");
        grid.appendChild(el("div", {
          title: lab + " · " + Math.round(v * 100) + "% weight",
          style: { background: v > 0.01 ? "rgba(232,184,74," + (0.12 + 0.88 * v).toFixed(2) + ")" : "rgba(255,255,255,0.03)", color: v > 0.55 ? "#1d1608" : "" },
        }, lab));
      }
    }
    card.appendChild(grid);
    const top = o.top_classes.slice(0, 5).map((t) => t[0]).join(", ");
    card.appendChild(el("div", { class: "range-line" }, !o.acted
      ? "Hasn't acted yet: the simulation lets them continue with the hands they would play."
      : ["~" + pct(o.range_pct) + " of hands", o.range_text ? " · " + o.range_text : top ? " · likely: " + top : ""]));
    card.appendChild(el("div", { class: "opp-stats" },
      statCell("VPIP", s.vpip), statCell("PFR", s.pfr), statCell("AF", s.af, true), statCell("Fold/bet", s.fold_to_bet)));
    card.appendChild(el("div", { class: "faint small", style: { marginTop: "4px" } }, s.hands + " hands tracked"));
    return card;
  }
  function statCell(name, v, raw) {
    return el("div", null, el("b", null, v === null || v === undefined ? "—" : raw ? fmt(v) : pct(v)), el("span", null, name));
  }

  function renderReads() {
    const st = S.state;
    const panel = el("div", { class: "panel" });
    panel.appendChild(el("div", { class: "panel-head" }, el("div", { class: "panel-title" }, "Table reads"),
      el("button", { class: "btn small ghost", onclick: () => openModal("stats") }, "All stats")));
    const tbl = el("table", { class: "stats-table" },
      el("thead", null, el("tr", null, ["Player", "Type", "Hands", "VPIP", "PFR", "AF", "Fold/bet"].map((t) => el("th", null, t)))));
    const tb = el("tbody");
    st.seats.filter((s) => !s.is_hero).forEach((s) => {
      const r = s.raw;
      tb.appendChild(el("tr", null,
        el("td", null, s.name),
        el("td", null, s.profile.key === "unknown" ? el("span", { class: "faint" }, "—") : el("span", { class: "ptag " + s.profile.key, title: s.note || "" }, s.profile.label)),
        el("td", { class: "mono" }, String(r.hands)),
        el("td", { class: "mono" }, pct(r.vpip)), el("td", { class: "mono" }, pct(r.pfr)),
        el("td", { class: "mono" }, r.af === null ? "—" : fmt(r.af)), el("td", { class: "mono" }, pct(r.fold_to_bet))));
    });
    tbl.appendChild(tb);
    panel.appendChild(el("div", { class: "table-scroll" }, tbl));
    panel.appendChild(el("div", { class: "faint small", style: { marginTop: "8px" } },
      "With few hands the bot leans on typical player tendencies and moves toward each player's real numbers as data comes in."));
    return panel;
  }

  // ------------------------------------------------------------ modals
  function openModal(kind, data) { S.modal = { kind, data: data || {} }; if (kind === "show") { S.pick = []; S.pickText = ""; } renderModal(); if (kind === "stats") loadStats(); }
  function closeModal() { S.modal = null; renderModal(); }
  let statsCache = null;
  async function loadStats() {
    const r = await py("stats");
    if (r.ok) { statsCache = r.stats; renderModal(); }
  }

  function renderModal() {
    const root = document.getElementById("modal-root");
    root.innerHTML = "";
    if (!S.modal || !S.state) return;
    const m = S.modal;
    let body;
    if (m.kind === "stats") body = modalStats();
    else if (m.kind === "history") body = modalHistory();
    else if (m.kind === "settings") body = modalSettings();
    else if (m.kind === "seat") body = modalSeat(m.data.seat);
    else if (m.kind === "show") body = modalShow(m.data.seat);
    if (!body) return;
    const back = el("div", { class: "modal-backdrop", onclick: (e) => { if (e.target === back) closeModal(); } }, body);
    root.appendChild(back);
  }
  function modalShell(title, wide, ...content) {
    return el("div", { class: "modal" + (wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
      el("div", { class: "modal-head" }, el("h2", null, title), el("button", { class: "btn small ghost", onclick: closeModal, "aria-label": "Close" }, "✕")),
      ...content);
  }

  function modalStats() {
    const rows = statsCache || [];
    const tbl = el("table", { class: "stats-table" },
      el("thead", null, el("tr", null,
        ["Player", "Type", "Hands", "VPIP", "PFR", "3-bet", "Fold to 3-bet", "C-bet", "Fold to c-bet", "Fold to bet", "AF", "WTSD", "W$SD"].map((t) => el("th", null, t)))));
    const tb = el("tbody");
    rows.forEach((d) => {
      const r = d.raw;
      tb.appendChild(el("tr", null,
        el("td", null, youName(d.name, d.is_hero) + (d.seat === null ? " (left)" : "")),
        el("td", null, d.profile.key === "unknown" ? el("span", { class: "faint" }, "—") : el("span", { class: "ptag " + d.profile.key }, d.profile.label)),
        el("td", { class: "mono" }, String(r.hands)),
        ...["vpip", "pfr", "threebet", "fold_to_3bet", "cbet", "fold_to_cbet", "fold_to_bet"].map((k) => el("td", { class: "mono" }, pct(r[k]))),
        el("td", { class: "mono" }, r.af === null ? "—" : fmt(r.af)),
        el("td", { class: "mono" }, pct(r.wtsd)), el("td", { class: "mono" }, pct(r.wsd))));
    });
    tbl.appendChild(tb);
    const shown = el("div");
    rows.filter((d) => d.shown && d.shown.length).forEach((d) => {
      shown.appendChild(el("div", { style: { marginTop: "12px" } }, el("b", null, d.name + " showed:"),
        el("ul", { class: "shown-list" }, d.shown.slice(0, 10).map((s) => el("li", null,
          prettyCards(s.cards), " (" + s.position + ") · " + s.line + (s.board ? " · board " : ""), s.board ? prettyCards(s.board) : null,
          s.made ? " · " + s.made : "", s.won ? " · won" : "")))));
    });
    const notes = el("div");
    rows.filter((d) => d.note && !d.is_hero && d.profile.key !== "unknown").forEach((d) => {
      notes.appendChild(el("div", { class: "small", style: { marginTop: "6px" } }, el("b", null, d.name + ": "), d.note));
    });
    return modalShell("Opponent statistics", true,
      statsCache ? el("div", { class: "table-scroll" }, tbl) : el("div", { class: "waiting" }, el("span", { class: "spinner sm" }), "Loading…"),
      notes.childNodes.length ? el("div", null, el("div", { class: "section-title" }, "How to play against them"), notes) : null,
      shown.childNodes.length ? el("div", null, el("div", { class: "section-title" }, "Shown hands"), shown) : null,
      el("p", { class: "faint small" },
        "VPIP = voluntarily put money in pre-flop · PFR = pre-flop raise · AF = aggression factor (bets+raises ÷ calls) · WTSD = went to showdown after seeing the flop · W$SD = won at showdown."));
  }

  function modalHistory() {
    const st = S.state;
    const list = el("div");
    if (!st.history.length) list.appendChild(el("div", { class: "muted" }, "No hands saved yet."));
    st.history.forEach((hh) => {
      list.appendChild(el("div", { class: "history-item" },
        el("span", { class: "no" }, "#" + hh.no),
        el("span", null, prettyCards(hh.hero_cards), " ", el("span", { class: "badge" }, hh.hero_position)),
        hh.board ? el("span", { class: "muted" }, "board ", prettyCards(hh.board)) : el("span", { class: "muted" }, "no flop"),
        el("span", { class: "muted" }, "pot " + fmt(hh.pot) + " → " + hh.winners.join(", ")),
        el("span", { class: "mono " + (hh.hero_net >= 0 ? "pos" : "neg"), style: { marginLeft: "auto" } }, signed(hh.hero_net))));
    });
    return modalShell("Hand history", false, list);
  }

  function modalSettings() {
    const st = S.state;
    const cfg = st.config;
    const f = { sb: cfg.sb, bb: cfg.bb, ante: cfg.ante, start_stack: cfg.start_stack, auto_rebuy: cfg.auto_rebuy };
    const num = (k, label) => el("div", { class: "field" }, el("label", null, label),
      el("input", { class: "input", type: "number", step: "any", value: f[k], oninput: (e) => { f[k] = Number(e.target.value); } }));
    const budgets = [[0.6, "Fast"], [1.5, "Balanced"], [3.5, "Precise"]];
    const seg = el("div", { class: "seg" });
    budgets.forEach(([v, n]) => seg.appendChild(el("button", { class: Math.abs(S.prefs.budget - v) < 1e-9 ? "on" : "", onclick: () => { S.prefs.budget = v; savePrefs(); renderModal(); } }, n)));
    return modalShell("Settings", false,
      el("div", { class: "grid2" }, num("sb", "Small blind"), num("bb", "Big blind"), num("ante", "Ante"), num("start_stack", "Starting stack")),
      el("label", { class: "row", style: { marginTop: "12px" } },
        el("input", { type: "checkbox", checked: f.auto_rebuy, onchange: (e) => { f.auto_rebuy = e.target.checked; } }),
        "Automatically rebuy busted players to the starting stack"),
      el("div", { class: "field", style: { marginTop: "14px" } }, el("label", null, "Advice precision (simulation time)"), seg),
      el("div", { class: "section-title" }, "Session data"),
      el("div", { class: "row" },
        el("button", { class: "btn small", onclick: exportFile }, "Export session (.json)"),
        el("label", { class: "btn small" }, "Import session…", el("input", { type: "file", accept: "application/json,.json", class: "hidden", onchange: importFile })),
        el("button", { class: "btn small danger", onclick: async () => {
          if (!confirm("End this session? Stats will be deleted (export first if you want to keep them).")) return;
          await py("close"); S.state = null; S.advice = null; store.del(STORE_KEY); closeModal(); render();
        } }, "End session")),
      el("div", { class: "modal-foot" },
        el("button", { class: "btn ghost", onclick: closeModal }, "Cancel"),
        el("button", { class: "btn primary", onclick: async () => { if (await act("set_config", f)) closeModal(); } }, "Save")));
  }

  function modalSeat(seat) {
    const s = seatInfo(seat);
    const st = S.state;
    const inHand = !!st.hand;
    const f = { name: s.name, stack: s.stack };
    return modalShell("Seat " + (seat + 1) + (s.is_hero ? " (you)" : ""), false,
      el("div", { class: "grid2" },
        el("div", { class: "field" }, el("label", null, "Name"), el("input", { class: "input", value: f.name, oninput: (e) => { f.name = e.target.value; } })),
        el("div", { class: "field" }, el("label", null, "Stack"), el("input", { class: "input", type: "number", step: "any", value: f.stack, disabled: inHand, oninput: (e) => { f.stack = Number(e.target.value); } }))),
      inHand ? el("p", { class: "faint small" }, "Stacks and seat changes can be edited between hands.") : null,
      !s.is_hero ? el("div", { class: "row", style: { marginTop: "12px" } },
        el("button", { class: "btn small", disabled: inHand, onclick: async () => { if (await act("update_seat", { seat, sitting_out: !s.sitting_out })) closeModal(); } },
          s.sitting_out ? "Sit back in" : "Sit out"),
        el("button", { class: "btn small danger", disabled: inHand, onclick: async () => {
          const name = prompt("Name of the new player in seat " + (seat + 1) + ":", "Seat " + (seat + 1));
          if (name === null) return;
          if (await act("update_seat", { seat, new_player: true, name })) closeModal();
        } }, "New player sat down (fresh stats)")) : null,
      el("div", { class: "modal-foot" },
        el("button", { class: "btn ghost", onclick: closeModal }, "Cancel"),
        el("button", { class: "btn primary", onclick: async () => {
          const args = { seat, name: f.name };
          if (!inHand) args.stack = f.stack;
          if (await act("update_seat", args)) closeModal();
        } }, "Save")));
  }

  function modalShow(seat) {
    const p = handPlayer(seat);
    const used = usedCards();
    (p.shown_raw || []).forEach((c) => used.delete(c));
    const save = el("button", { class: "btn primary", disabled: S.pick.length !== 2, onclick: () => submitShow(seat) }, "Save");
    const picker = renderPicker(2, used, (fromEnter) => { if (fromEnter) submitShow(seat); });
    refreshPicker = () => renderModal();
    return modalShell(p.name + " showed…", false, picker,
      el("div", { class: "modal-foot" }, el("button", { class: "btn ghost", onclick: () => { refreshPicker = () => render(); closeModal(); } }, "Cancel"), save));
  }
  async function submitShow(seat) {
    if (S.pick.length !== 2) return;
    refreshPicker = () => render();
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
      const r = await act("load", { session: data });
      if (r) toast("Session imported", true);
    };
    reader.readAsText(file);
  }

  // --------------------------------------------------------- keyboard
  document.addEventListener("keydown", (e) => {
    if (S.modal || !S.state || !S.state.hand) return;
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
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (S.state && !S.modal) render(); }, 200); });
})();
