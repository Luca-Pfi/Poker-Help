"""A playing session: the table, the current hand, opponent statistics and
a short hand history.  Fully JSON-serialisable so the web page can save it
in the browser between visits."""

import time

from .game import Hand, HandError, position_names
from .tracker import Tracker, benchmarks
from .advisor import advise
from .cards import pretty_cards

VERSION = 2


def new_hero_record():
    return {"hands": 0, "net": 0.0, "curve": [], "biggest_win": 0.0, "biggest_loss": 0.0,
            "decisions": 0, "followed": 0, "hands_followed": 0, "net_followed": 0.0,
            "hands_deviated": 0, "net_deviated": 0.0, "showdowns": 0, "showdowns_won": 0,
            "by_position": {}}


def _cat(action):
    return "aggr" if action in ("bet", "raise", "allin") else action


class Session:
    def __init__(self, config=None, data=None):
        if data:
            self._load(data)
            return
        cfg = dict(config or {})
        n = int(cfg.get("players", 6))
        if not 2 <= n <= 10:
            raise HandError("Number of players must be between 2 and 10")
        self.config = {
            "players": n,
            "sb": float(cfg.get("sb", 0.5)),
            "bb": float(cfg.get("bb", 1.0)),
            "ante": float(cfg.get("ante", 0) or 0),
            "start_stack": float(cfg.get("start_stack", 100)),
            "auto_rebuy": bool(cfg.get("auto_rebuy", True)),
        }
        names = cfg.get("names") or []
        self.hero_seat = int(cfg.get("hero_seat", 0))
        self.pid_counter = 0
        self.seats = []
        for i in range(n):
            name = names[i] if i < len(names) and names[i] else ("You" if i == self.hero_seat else "Seat %d" % (i + 1))
            self.seats.append({"seat": i, "name": name, "pid": self._new_pid(), "stack": self.config["start_stack"],
                               "sitting_out": False})
        self.tracker = Tracker()
        self._sync_table_size()
        self.button = int(cfg.get("button", (self.hero_seat + n - 1) % n))
        self.hand_setup = None
        self.hand_events = []
        self.hand_no = 0
        self.history = []
        self.hero_record = new_hero_record()
        self.h2h = {}
        self.created = time.time()
        self._hand = None

    def _new_pid(self):
        self.pid_counter += 1
        return "p%d" % self.pid_counter

    # ------------------------------------------------------------- json
    def to_json(self):
        return {
            "version": VERSION,
            "config": self.config,
            "hero_seat": self.hero_seat,
            "pid_counter": self.pid_counter,
            "seats": self.seats,
            "tracker": self.tracker.to_json(),
            "button": self.button,
            "hand_setup": self.hand_setup,
            "hand_events": self.hand_events,
            "hand_no": self.hand_no,
            "history": self.history,
            "hero_record": self.hero_record,
            "h2h": self.h2h,
            "created": self.created,
        }

    def _load(self, d):
        self.config = d["config"]
        self.hero_seat = d["hero_seat"]
        self.pid_counter = d.get("pid_counter", 0)
        self.seats = d["seats"]
        self.tracker = Tracker(d.get("tracker"))
        self.button = d.get("button", 0)
        self.hand_setup = d.get("hand_setup")
        self.hand_events = d.get("hand_events") or []
        self.hand_no = d.get("hand_no", 0)
        self.history = d.get("history") or []
        self.hero_record = dict(new_hero_record(), **(d.get("hero_record") or {}))
        if not d.get("hero_record") and self.history:
            # sessions saved before v2: rebuild what we can from the history
            hr = self.hero_record
            for hh in reversed(self.history):
                hr["hands"] += 1
                hr["net"] = round(hr["net"] + hh["hero_net"], 2)
                hr["curve"].append(hr["net"])
        self.h2h = d.get("h2h") or {}
        self.created = d.get("created", time.time())
        self._hand = None
        self._sync_table_size()
        if self.hand_setup:
            self._hand = Hand(self.hand_setup, self.hand_events)

    # ------------------------------------------------------------ seats
    def _sync_table_size(self):
        self.tracker.table_size = max(2, len([s for s in self.seats if not s["sitting_out"]]))

    def active_seats(self):
        return [s for s in self.seats if not s["sitting_out"]]

    def seat(self, i):
        return self.seats[int(i)]

    def update_seat(self, seat, name=None, stack=None, new_player=False, sitting_out=None):
        if self._hand is not None and (sitting_out is not None or new_player):
            raise HandError("Finish the current hand first")
        st = self.seat(seat)
        if new_player:
            st["pid"] = self._new_pid()
            st["name"] = name or "Seat %d" % (st["seat"] + 1)
        elif name:
            st["name"] = name
            self.tracker.get(st["pid"], name)
        if stack is not None:
            st["stack"] = float(stack)
        if sitting_out is not None:
            if st["seat"] == self.hero_seat and sitting_out:
                raise HandError("You can't sit yourself out")
            st["sitting_out"] = bool(sitting_out)
            self._sync_table_size()

    def set_config(self, **kw):
        for k in ("sb", "bb", "ante", "start_stack"):
            if k in kw and kw[k] is not None:
                self.config[k] = float(kw[k])
        if "auto_rebuy" in kw:
            self.config["auto_rebuy"] = bool(kw["auto_rebuy"])

    def set_button(self, seat):
        if self._hand is not None:
            raise HandError("Finish the current hand first")
        if self.seat(seat)["sitting_out"]:
            raise HandError("That seat is sitting out")
        self.button = int(seat)

    def _next_active(self, seat):
        n = len(self.seats)
        for k in range(1, n + 1):
            s = self.seats[(seat + k) % n]
            if not s["sitting_out"]:
                return s["seat"]
        return seat

    # ------------------------------------------------------------- hand
    @property
    def hand(self):
        return self._hand

    def start_hand(self, hero_cards, button=None):
        if self._hand is not None:
            raise HandError("A hand is already in progress")
        if button is not None:
            self.set_button(button)
        if self.seat(self.button)["sitting_out"]:
            self.button = self._next_active(self.button)
        players = []
        for s in self.active_seats():
            if s["stack"] <= 0 and self.config.get("auto_rebuy", True):
                s["stack"] = self.config["start_stack"]
            if s["stack"] <= 0:
                continue
            players.append({"seat": s["seat"], "name": s["name"], "pid": s["pid"], "stack": s["stack"]})
        if len(players) < 2:
            raise HandError("Need at least 2 players with chips")
        if self.hero_seat not in [p["seat"] for p in players]:
            raise HandError("You need chips to play a hand")
        setup = {
            "players": players,
            "button": self.button,
            "sb": self.config["sb"],
            "bb": self.config["bb"],
            "ante": self.config.get("ante", 0),
            "hero_seat": self.hero_seat,
            "hero_cards": hero_cards,
        }
        hand = Hand(setup)    # validates
        self.hand_setup = setup
        self.hand_events = []
        self._hand = hand

    def _require_hand(self):
        if self._hand is None:
            raise HandError("No hand in progress")
        return self._hand

    def _push(self, ev):
        h = self._require_hand()
        h.apply(ev)      # raises on invalid -> event not stored
        self.hand_events.append(ev)

    def act(self, seat, action, amount=None, advised=None):
        ev = {"type": "action", "seat": int(seat), "action": action}
        if amount is not None and amount != "":
            ev["amount"] = float(amount)
        if advised and int(seat) == self.hero_seat:
            ev["advised"] = {"action": advised.get("action"), "to": advised.get("to")}
        self._push(ev)

    def board(self, cards):
        self._push({"type": "board", "cards": cards})

    def show(self, seat, cards):
        self._push({"type": "show", "seat": int(seat), "cards": cards})

    def undo(self):
        self._require_hand()
        if not self.hand_events:
            # undoing the deal itself: back to "no hand"
            self.hand_setup = None
            self._hand = None
            return
        self.hand_events.pop()
        self._hand = Hand(self.hand_setup, self.hand_events)

    def abandon(self):
        self.hand_setup = None
        self.hand_events = []
        self._hand = None

    def finish(self, winners=None):
        h = self._require_hand()
        if not h.finished:
            raise HandError("The hand isn't over yet")
        if h.result["reason"] == "fold":
            winners = [list(h.result["winners"])]
        else:
            if not winners:
                winners = h.auto_pot_winners()
            if not winners:
                raise HandError("Select who won the pot")
            if not isinstance(winners[0], (list, tuple)):
                winners = [winners]
            winners = [[int(w) for w in pw] for pw in winners]
            live = h.live_seats()
            if any(w not in live for pw in winners for w in pw):
                raise HandError("Winners must be players still in the hand")
            if len(winners) == 1 and len(h.pots()) > 1:
                winners = winners * len(h.pots())
        payouts = h.payouts(winners)
        all_winners = sorted({w for pw in winners for w in pw if payouts.get(w, 0) > 0})
        self.tracker.record_hand(h, all_winners)
        winners = all_winners
        hero_net = 0.0
        for s, pl in h.p.items():
            seat = self.seat(s)
            seat["stack"] = round(pl["stack"] + payouts.get(s, 0.0), 2)
            if s == self.hero_seat:
                hero_net = round(payouts.get(s, 0.0) - pl["total"], 2)
        self._record_hero(h, hero_net, winners)
        self.hand_no += 1
        self.history.insert(0, {
            "no": self.hand_no,
            "hero_cards": pretty_cards(h.hero_cards),
            "hero_position": h.p[self.hero_seat]["position"],
            "board": pretty_cards(h.board),
            "pot": h.pot(),
            "winners": [h.p[w]["name"] for w in winners],
            "hero_net": hero_net,
            "reason": h.result["reason"],
            "shown": {h.p[s]["name"]: pretty_cards(pl["shown"]) for s, pl in h.p.items() if pl["shown"]},
            "log": h.state()["log"],
        })
        self.history = self.history[:200]
        self.button = self._next_active(h.order[0])
        self.hand_setup = None
        self.hand_events = []
        self._hand = None
        return {"winners": winners, "payouts": payouts, "hero_net": hero_net}

    def _record_hero(self, h, hero_net, winners):
        hr = self.hero_record
        hero = self.hero_seat
        hr["hands"] += 1
        hr["net"] = round(hr["net"] + hero_net, 2)
        hr["curve"].append(hr["net"])
        if len(hr["curve"]) > 3000:
            hr["curve"] = hr["curve"][-3000:]
        hr["biggest_win"] = max(hr["biggest_win"], hero_net)
        hr["biggest_loss"] = min(hr["biggest_loss"], hero_net)
        pos = h.p[hero]["position"]
        bp = hr["by_position"].setdefault(pos, {"hands": 0, "net": 0.0})
        bp["hands"] += 1
        bp["net"] = round(bp["net"] + hero_net, 2)
        # did you follow the advice?
        advised = [ev for ev in self.hand_events
                   if ev.get("type") == "action" and ev.get("seat") == hero and ev.get("advised")]
        if advised:
            followed = [_cat(ev["action"]) == _cat(ev["advised"]["action"]) for ev in advised]
            hr["decisions"] += len(followed)
            hr["followed"] += sum(followed)
            if all(followed):
                hr["hands_followed"] += 1
                hr["net_followed"] = round(hr["net_followed"] + hero_net, 2)
            else:
                hr["hands_deviated"] += 1
                hr["net_deviated"] = round(hr["net_deviated"] + hero_net, 2)
        showdown = h.result["reason"] == "showdown" and not h.p[hero]["folded"]
        if showdown:
            hr["showdowns"] += 1
            if hero in winners:
                hr["showdowns_won"] += 1
        # head-to-head against each opponent
        flop = len(h.board) >= 3

        def involved(seat):
            pre = [a for a in h.actions if a["seat"] == seat and a["street"] == 0]
            vpip = any(a["action"] in ("call", "bet", "raise") for a in pre)
            folded_pre = any(a["action"] == "fold" for a in pre)
            return vpip, (flop and not folded_pre)

        hv, hf = involved(hero)
        for s, pl in h.p.items():
            if s == hero:
                continue
            ov, of = involved(s)
            if not ((hv and ov) or (hf and of)):
                continue
            rec = self.h2h.setdefault(pl["pid"], {"name": pl["name"], "hands": 0, "net": 0.0,
                                                  "showdowns": 0, "hero_won": 0, "opp_won": 0})
            rec["name"] = pl["name"]
            rec["hands"] += 1
            rec["net"] = round(rec["net"] + hero_net, 2)
            if showdown and not pl["folded"]:
                rec["showdowns"] += 1
                if hero in winners:
                    rec["hero_won"] += 1
                if s in winners:
                    rec["opp_won"] += 1

    def advise(self, **kw):
        h = self._require_hand()
        return advise(h, self.tracker, **kw)

    def hero_summary(self):
        hr = self.hero_record
        bb = self.config["bb"] or 1.0
        n = hr["hands"]
        return {
            "hands": n,
            "net": hr["net"],
            "net_bb": round(hr["net"] / bb, 1),
            "bb100": round(100.0 * hr["net"] / bb / n, 1) if n else None,
            "biggest_win": hr["biggest_win"],
            "biggest_loss": hr["biggest_loss"],
            "curve": hr["curve"],
            "decisions": hr["decisions"],
            "followed_pct": round(hr["followed"] / hr["decisions"], 3) if hr["decisions"] else None,
            "hands_followed": hr["hands_followed"],
            "hands_deviated": hr["hands_deviated"],
            "bb_per_hand_followed": round(hr["net_followed"] / bb / hr["hands_followed"], 2) if hr["hands_followed"] else None,
            "bb_per_hand_deviated": round(hr["net_deviated"] / bb / hr["hands_deviated"], 2) if hr["hands_deviated"] else None,
            "showdowns": hr["showdowns"],
            "showdown_win_pct": round(hr["showdowns_won"] / hr["showdowns"], 3) if hr["showdowns"] else None,
            "by_position": hr["by_position"],
        }

    # ------------------------------------------------------------- view
    def state(self):
        n_active = len(self.active_seats())
        pos_by_seat = {}
        if self._hand is None and n_active >= 2:
            act = [s["seat"] for s in self.active_seats()]
            b = self.button if self.button in act else act[0]
            i = act.index(b)
            ordered = act[i:] + act[:i]
            names = position_names(len(ordered))
            pos_by_seat = {s: names[k] for k, s in enumerate(ordered)}
        seats = []
        for s in self.seats:
            summ = self.tracker.summary(s["pid"])
            seats.append({
                "seat": s["seat"],
                "name": s["name"],
                "pid": s["pid"],
                "stack": s["stack"],
                "sitting_out": s["sitting_out"],
                "is_hero": s["seat"] == self.hero_seat,
                "next_position": pos_by_seat.get(s["seat"]),
                "profile": summ["profile"],
                "raw": summ["raw"],
                "note": summ["note"],
                "h2h": self.h2h.get(s["pid"]),
            })
        hero = self.hero_summary()
        hero.pop("curve")
        return {
            "config": self.config,
            "hero_seat": self.hero_seat,
            "button": self.button,
            "hand_no": self.hand_no,
            "seats": seats,
            "hand": self._hand.state() if self._hand else None,
            "history": self.history[:60],
            "hero_net": self.hero_record["net"],
            "hero": hero,
        }

    def stats(self):
        players = []
        for s in self.seats:
            d = self.tracker.summary(s["pid"])
            d["seat"] = s["seat"]
            d["is_hero"] = s["seat"] == self.hero_seat
            d["sitting_out"] = s["sitting_out"]
            d["h2h"] = self.h2h.get(s["pid"])
            players.append(d)
        # players no longer seated
        seated = {s["pid"] for s in self.seats}
        for pid in self.tracker.stats:
            if pid not in seated:
                d = self.tracker.summary(pid)
                d["seat"] = None
                d["is_hero"] = False
                d["sitting_out"] = False
                d["h2h"] = self.h2h.get(pid)
                players.append(d)
        hero_pid = self.seat(self.hero_seat)["pid"]
        raw = self.tracker.raw(hero_pid)
        samples = dict(raw["samples"], vpip=raw["hands"], pfr=raw["hands"],
                       af=raw["samples"]["post_actions"], wsd=raw["samples"]["saw_flop"])
        bench = []
        for b in benchmarks(self.tracker.table_size):
            v = raw.get(b["key"])
            n = samples.get(b["key"], raw["hands"])
            if b["key"] == "wsd":
                n = self.hero_record["showdowns"]
            verdict = None
            if v is not None and n >= b["min_n"]:
                verdict = "low" if v < b["lo"] else "high" if v > b["hi"] else "ok"
            bench.append(dict(b, value=v, n=n, verdict=verdict,
                              tip=b.get(verdict) if verdict in ("low", "high") else None))
        return {"players": players, "hero": self.hero_summary(), "benchmarks": bench,
                "table_size": self.tracker.table_size, "bb": self.config["bb"]}
