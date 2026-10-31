"""No-limit hold'em hand state machine.

A Hand is created from a setup dict and a list of events; every change is
an appended event, so undo is just "replay all events but the last".

Amounts for bets / raises are "raise TO" totals for the current street.
"""

from .cards import parse_cards, pretty_cards
from .evaluator import evaluate, describe_score

STREETS = ["preflop", "flop", "turn", "river", "showdown"]
BOARD_SIZE = {1: 3, 2: 1, 3: 1}


def position_names(n):
    """Position labels in seat order starting from the button."""
    if n == 2:
        return ["BTN", "BB"]
    after_bb = {
        0: [],
        1: ["UTG"],
        2: ["UTG", "CO"],
        3: ["UTG", "HJ", "CO"],
        4: ["UTG", "LJ", "HJ", "CO"],
        5: ["UTG", "MP", "LJ", "HJ", "CO"],
        6: ["UTG", "UTG+1", "MP", "LJ", "HJ", "CO"],
        7: ["UTG", "UTG+1", "UTG+2", "MP", "LJ", "HJ", "CO"],
    }[n - 3]
    return ["BTN", "SB", "BB"] + after_bb


class HandError(ValueError):
    pass


def _r(x):
    return round(float(x), 2)


class Hand:
    def __init__(self, setup, events=None):
        """setup = {
            players: [{seat, name, pid, stack}],   # dealt-in players, any order
            button: seat, sb: float, bb: float, ante: float,
            hero_seat: seat, hero_cards: "AhKd",
        }"""
        self.setup = setup
        self.events = []
        self._init()
        for ev in events or []:
            self.apply(ev)

    # ------------------------------------------------------------ setup
    def _init(self):
        st = self.setup
        players = sorted(st["players"], key=lambda p: p["seat"])
        if len(players) < 2:
            raise HandError("Need at least 2 players")
        seats = [p["seat"] for p in players]
        if st["button"] not in seats:
            raise HandError("Button must be on an active seat")
        b = seats.index(st["button"])
        self.order = seats[b:] + seats[:b]          # button first
        self.n = len(self.order)
        names = position_names(self.n)
        self.sb_amt = float(st.get("sb", 0.5))
        self.bb_amt = float(st.get("bb", 1.0))
        self.ante = float(st.get("ante", 0) or 0)
        self.hero_seat = st.get("hero_seat")
        self.hero_cards = parse_cards(st["hero_cards"]) if st.get("hero_cards") else []
        self.p = {}
        for pl in players:
            self.p[pl["seat"]] = {
                "seat": pl["seat"],
                "name": pl.get("name") or "Seat %d" % (pl["seat"] + 1),
                "pid": pl.get("pid") or pl.get("name") or str(pl["seat"]),
                "start_stack": float(pl.get("stack", 100.0)),
                "stack": float(pl.get("stack", 100.0)),
                "street_bet": 0.0,
                "total": 0.0,
                "folded": False,
                "all_in": False,
                "acted": False,
                "position": names[self.order.index(pl["seat"])],
                "is_hero": pl["seat"] == self.hero_seat,
                "shown": None,
            }
        self.street = 0
        self.board = []
        self.actions = []        # list of action dicts (see _record)
        self.current_bet = 0.0
        self.min_raise = self.bb_amt
        self.raises_this_street = 0
        self.last_aggressor = None
        self.preflop_aggressor = None
        self.to_act = None
        self.awaiting_board = 0
        self.finished = False
        self.result = None
        self.dead_pot = 0.0

        # antes and blinds
        if self.ante > 0:
            for s in self.order:
                pl = self.p[s]
                a = min(self.ante, pl["stack"])
                pl["stack"] -= a
                pl["total"] += a
                self.dead_pot += a
                if pl["stack"] <= 0:
                    pl["all_in"] = True
        if self.n == 2:
            sb_seat, bb_seat = self.order[0], self.order[1]
        else:
            sb_seat, bb_seat = self.order[1], self.order[2]
        self.sb_seat, self.bb_seat = sb_seat, bb_seat
        self._post(sb_seat, self.sb_amt)
        self._post(bb_seat, self.bb_amt)
        self.current_bet = max(self.p[sb_seat]["street_bet"], self.p[bb_seat]["street_bet"], self.bb_amt)
        first = self._next_seat(bb_seat, need_action=True)
        self.to_act = first
        self._check_round_end()

    def _post(self, seat, amt):
        pl = self.p[seat]
        a = min(amt, pl["stack"])
        pl["stack"] -= a
        pl["street_bet"] += a
        pl["total"] += a
        if pl["stack"] <= 1e-9:
            pl["stack"] = 0.0
            pl["all_in"] = True

    # ------------------------------------------------------------ helpers
    def _next_seat(self, seat, need_action=True):
        """Next seat clockwise after `seat` that can act."""
        i = self.order.index(seat)
        for k in range(1, self.n + 1):
            s = self.order[(i + k) % self.n]
            pl = self.p[s]
            if pl["folded"]:
                continue
            if need_action and pl["all_in"]:
                continue
            return s
        return None

    def live_seats(self):
        return [s for s in self.order if not self.p[s]["folded"]]

    def pot(self):
        return _r(sum(pl["total"] for pl in self.p.values()))

    def to_call(self, seat):
        pl = self.p[seat]
        return _r(min(self.current_bet - pl["street_bet"], pl["stack"]))

    def postflop_order(self):
        """Seats in post-flop acting order (SB first, button last)."""
        if self.n == 2:
            return [self.order[1], self.order[0]]
        return self.order[1:] + self.order[:1]

    def players_to_act_after(self, seat):
        """Live, non-all-in seats that still act after `seat` in this betting round."""
        out = []
        s = seat
        for _ in range(self.n):
            s = self._next_seat(s, need_action=True)
            if s is None or s == seat:
                break
            pl = self.p[s]
            if not pl["acted"] or pl["street_bet"] < self.current_bet:
                out.append(s)
        return out

    def legal(self):
        if self.to_act is None:
            return None
        pl = self.p[self.to_act]
        to_call = self.to_call(self.to_act)
        can_raise = pl["stack"] > to_call + 1e-9
        min_to = self.current_bet + max(self.min_raise, self.bb_amt) if self.current_bet > 0 else self.bb_amt
        max_to = pl["street_bet"] + pl["stack"]
        acts = []
        if to_call > 0:
            acts.append("fold")
            acts.append("call")
        else:
            acts.append("check")
        if can_raise:
            acts.append("raise" if self.current_bet > 0 else "bet")
        acts.append("allin")
        return {
            "seat": self.to_act,
            "actions": acts,
            "to_call": to_call,
            "current_bet": _r(self.current_bet),
            "min_to": _r(min(min_to, max_to)),
            "max_to": _r(max_to),
            "street_bet": _r(pl["street_bet"]),
            "stack": _r(pl["stack"]),
            "pot": self.pot(),
        }

    # ------------------------------------------------------------ events
    def apply(self, ev):
        kind = ev.get("type")
        if kind == "action":
            self._act(ev["seat"], ev["action"], ev.get("amount"))
        elif kind == "board":
            self._board(ev["cards"])
        elif kind == "show":
            self._show(ev["seat"], ev["cards"])
        else:
            raise HandError("unknown event %r" % kind)
        self.events.append(ev)

    def _act(self, seat, action, amount=None):
        if self.finished:
            raise HandError("Hand is over")
        if self.awaiting_board:
            raise HandError("Enter the board cards first")
        if seat != self.to_act:
            raise HandError("It is %s's turn" % self.p[self.to_act]["name"])
        pl = self.p[seat]
        to_call = self.to_call(seat)
        pot_before = self.pot()
        bet_before = self.current_bet
        raises_before = self.raises_this_street
        street_bet_before = pl["street_bet"]
        facing = to_call > 0
        action = action.lower()
        if action == "allin":
            target = pl["street_bet"] + pl["stack"]
            if target > self.current_bet + 1e-9:
                action, amount = ("raise" if self.current_bet > 0 else "bet"), target
            else:
                action = "call"
        if action == "bet" and self.current_bet > 0:
            action = "raise"
        if action == "raise" and self.current_bet == 0:
            action = "bet"

        if action == "fold":
            if to_call <= 0:
                # folding when you could check is legal but silly; record as fold
                pass
            pl["folded"] = True
        elif action == "check":
            if to_call > 0:
                raise HandError("Cannot check facing a bet of %s" % to_call)
        elif action == "call":
            if to_call <= 0:
                action = "check"
            else:
                self._put(pl, to_call)
        elif action in ("bet", "raise"):
            if amount is None:
                raise HandError("Amount required")
            target = float(amount)
            max_to = pl["street_bet"] + pl["stack"]
            if target >= max_to - 1e-9:
                target = max_to
            if target <= self.current_bet + 1e-9:
                raise HandError("Raise must be more than the current bet (%s)" % _r(self.current_bet))
            self._put(pl, target - pl["street_bet"])
            inc = target - self.current_bet
            if inc >= self.min_raise:
                self.min_raise = inc
            self.current_bet = target
            self.raises_this_street += 1
            self.last_aggressor = seat
            if self.street == 0:
                self.preflop_aggressor = seat
            for s, other in self.p.items():
                if s != seat:
                    other["acted"] = False
        else:
            raise HandError("Unknown action %r" % action)
        pl["acted"] = True
        self._record(seat, action, pl, pot_before, to_call, bet_before, raises_before,
                     street_bet_before, facing)
        self.to_act = self._next_seat(seat, need_action=True)
        self._check_round_end()

    def _put(self, pl, amt):
        amt = min(amt, pl["stack"])
        pl["stack"] -= amt
        pl["street_bet"] += amt
        pl["total"] += amt
        if pl["stack"] <= 1e-9:
            pl["stack"] = 0.0
            pl["all_in"] = True

    def _record(self, seat, action, pl, pot_before, to_call, bet_before, raises_before, sb_before, facing):
        self.actions.append({
            "street": self.street,
            "seat": seat,
            "action": action,
            "to": _r(pl["street_bet"]),
            "added": _r(pl["street_bet"] - sb_before),
            "pot_before": _r(pot_before),
            "to_call": _r(to_call),
            "bet_before": _r(bet_before),
            "raises_before": raises_before,
            "facing_bet": facing,
            "all_in": pl["all_in"],
            "board": list(self.board),
        })

    def _check_round_end(self):
        live = self.live_seats()
        if len(live) == 1:
            self._finish(winners=live, reason="fold")
            return
        can_act = [s for s in live if not self.p[s]["all_in"]]
        pending = [s for s in can_act
                   if not self.p[s]["acted"] or self.p[s]["street_bet"] < self.current_bet - 1e-9]
        if pending and not (len(can_act) == 1 and self.p[can_act[0]]["street_bet"] >= self.current_bet - 1e-9):
            if self.to_act not in pending:
                start = self.order.index(self.to_act) if self.to_act in self.order else 0
                for k in range(self.n):
                    s = self.order[(start + k) % self.n]
                    if s in pending:
                        self.to_act = s
                        break
            return
        # betting round complete
        self.to_act = None
        if self.street >= 3:
            self.street = 4
            self._finish(winners=None, reason="showdown")
            return
        self.awaiting_board = BOARD_SIZE[self.street + 1]

    def _board(self, cards):
        if not self.awaiting_board:
            raise HandError("Not waiting for board cards")
        cards = parse_cards(cards) if isinstance(cards, str) else [int(c) for c in cards]
        if len(cards) != self.awaiting_board:
            raise HandError("Need exactly %d card(s)" % self.awaiting_board)
        used = set(self.board) | set(self.hero_cards)
        for pl in self.p.values():
            if pl["shown"]:
                used |= set(pl["shown"])
        for c in cards:
            if c in used:
                raise HandError("Card %s is already used" % pretty_cards([c]))
        self.board.extend(cards)
        self.street += 1
        self.awaiting_board = 0
        for pl in self.p.values():
            pl["street_bet"] = 0.0
            pl["acted"] = False
        self.current_bet = 0.0
        self.min_raise = self.bb_amt
        self.raises_this_street = 0
        self.last_aggressor = None
        first = self.postflop_order()
        self.to_act = None
        for s in first:
            if not self.p[s]["folded"] and not self.p[s]["all_in"]:
                self.to_act = s
                break
        self._check_round_end()

    def _show(self, seat, cards):
        cards = parse_cards(cards) if isinstance(cards, str) else [int(c) for c in cards]
        if len(cards) != 2:
            raise HandError("Shown hand must be 2 cards")
        used = set(self.board) | set(self.hero_cards)
        for s, pl in self.p.items():
            if s != seat and pl["shown"]:
                used |= set(pl["shown"])
        if any(c in used for c in cards):
            raise HandError("Card already used")
        self.p[seat]["shown"] = cards

    # ------------------------------------------------------------ finish
    def _finish(self, winners, reason):
        self.finished = True
        self.to_act = None
        self.awaiting_board = 0
        self.result = {"reason": reason, "winners": winners, "awarded": False}

    def known_cards(self, seat):
        if seat == self.hero_seat:
            return self.hero_cards
        return self.p[seat]["shown"]

    def pots(self):
        """Main pot and side pots: [{"amount", "eligible": [seats]}]."""
        contrib = {s: pl["total"] for s, pl in self.p.items()}
        live = self.live_seats()
        levels = sorted(set(contrib[s] for s in live if contrib[s] > 0))
        pots = []
        prev = 0.0
        for lvl in levels:
            amt = sum(min(c, lvl) - min(c, prev) for c in contrib.values())
            eligible = [s for s in live if contrib[s] >= lvl - 1e-9]
            if amt > 1e-9:
                if pots and pots[-1]["eligible"] == eligible:
                    pots[-1]["amount"] += amt
                else:
                    pots.append({"amount": amt, "eligible": eligible})
            prev = lvl
        rest = sum(contrib.values()) - sum(p["amount"] for p in pots)
        if pots and rest > 1e-9:
            pots[0]["amount"] += rest
        for p in pots:
            p["amount"] = _r(p["amount"])
        return pots

    def payouts(self, winners):
        """Split the pot(s).

        winners: list of seats sharing the best hand (applied to every pot they
        are eligible for), or a list with one list of seats per pot."""
        pots = self.pots()
        if winners and isinstance(winners[0], (list, tuple)):
            per_pot = [list(w) for w in winners]
        else:
            per_pot = [list(winners or []) for _ in pots]
        payout = {s: 0.0 for s in self.p}
        for i, pot in enumerate(pots):
            ws = [s for s in (per_pot[i] if i < len(per_pot) else []) if s in pot["eligible"]]
            if not ws:
                if len(pot["eligible"]) == 1:
                    ws = list(pot["eligible"])     # uncalled bet goes back
                else:
                    raise HandError("Select the winner of the %s" % ("main pot" if i == 0 else "side pot %d" % i))
            share = pot["amount"] / len(ws)
            for s in ws:
                payout[s] += share
        return {s: _r(v) for s, v in payout.items()}

    def auto_pot_winners(self):
        """Per-pot winners if every live player's cards are known, else None."""
        if len(self.board) < 5:
            return None
        scores = {}
        for s in self.live_seats():
            c = self.known_cards(s)
            if not c:
                return None
            scores[s] = evaluate(list(c) + self.board)
        out = []
        for pot in self.pots():
            best = max(scores[s] for s in pot["eligible"])
            out.append([s for s in pot["eligible"] if scores[s] == best])
        return out

    # ------------------------------------------------------------ views
    def hero_position_in_postflop(self):
        """True if hero acts last among live players after the flop."""
        order = [s for s in self.postflop_order() if not self.p[s]["folded"]]
        return bool(order) and order[-1] == self.hero_seat

    def is_unopened(self):
        return self.street == 0 and self.raises_this_street == 0

    def state(self):
        pls = []
        for s in self.order:
            pl = self.p[s]
            pls.append({
                "seat": s,
                "name": pl["name"],
                "pid": pl["pid"],
                "position": pl["position"],
                "stack": _r(pl["stack"]),
                "street_bet": _r(pl["street_bet"]),
                "total": _r(pl["total"]),
                "folded": pl["folded"],
                "all_in": pl["all_in"],
                "is_hero": pl["is_hero"],
                "shown": pretty_cards(pl["shown"]) if pl["shown"] else None,
                "shown_raw": [int(c) for c in pl["shown"]] if pl["shown"] else None,
            })
        log = []
        for a in self.actions:
            pl = self.p[a["seat"]]
            if a["action"] in ("fold", "check"):
                txt = a["action"].capitalize() + "s"
            elif a["action"] == "call":
                txt = "Calls %s" % _fmt(a["added"])
            elif a["action"] == "bet":
                txt = "Bets %s" % _fmt(a["to"])
            else:
                txt = "Raises to %s" % _fmt(a["to"])
            if a["all_in"] and a["action"] in ("call", "bet", "raise"):
                txt += " (all-in)"
            log.append({"street": STREETS[a["street"]], "seat": a["seat"], "name": pl["name"],
                        "position": pl["position"], "text": txt, "action": a["action"]})
        hero_desc = None
        if self.hero_cards and len(self.board) >= 3:
            hero_desc = describe_score(evaluate(self.hero_cards + self.board))
        return {
            "street": STREETS[self.street],
            "street_index": self.street,
            "board": [int(c) for c in self.board],
            "board_pretty": pretty_cards(self.board),
            "hero_seat": self.hero_seat,
            "hero_cards": [int(c) for c in self.hero_cards],
            "hero_pretty": pretty_cards(self.hero_cards),
            "hero_hand_desc": hero_desc,
            "button": self.order[0],
            "sb_seat": self.sb_seat,
            "bb_seat": self.bb_seat,
            "sb": self.sb_amt,
            "bb": self.bb_amt,
            "players": pls,
            "pot": self.pot(),
            "current_bet": _r(self.current_bet),
            "to_act": self.to_act,
            "legal": self.legal(),
            "awaiting_board": self.awaiting_board,
            "finished": self.finished,
            "result": self.result,
            "log": log,
            "can_undo": len(self.events) > 0,
            "auto_winners": self.auto_pot_winners() if self.finished and self.result and self.result["reason"] == "showdown" else None,
            "pots": self.pots() if self.finished else None,
        }


def _fmt(x):
    x = float(x)
    return ("%d" % x) if abs(x - round(x)) < 1e-9 else ("%.2f" % x).rstrip("0").rstrip(".")
