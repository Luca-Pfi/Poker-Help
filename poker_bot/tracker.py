"""Opponent tracking: per-player statistics accumulated over a session,
Bayesian-shrunk estimates used by the advisor, and player-type profiles."""

from .cards import pretty_cards, hand_class
from .evaluator import evaluate, describe_score
from .ranges import expected_vpip_pfr

# Population priors (typical low-stakes / live cash game) and their weight
# in pseudo-observations: est = (count + prior * k) / (opportunities + k)
PRIORS = {
    "vpip": (0.28, 8),      # vpip / pfr / 3-bet priors are rescaled for the table size
    "pfr": (0.14, 8),
    "threebet": (0.06, 10),
    "fold_to_3bet": (0.50, 6),
    "limp": (0.10, 10),
    "cbet": (0.60, 6),
    "fold_to_cbet": (0.45, 6),
    "fold_to_bet": (0.45, 10),
    "afq": (0.38, 15),
    "wtsd": (0.28, 10),
}

STAT_FIELDS = [
    "hands", "vpip", "pfr", "limp", "threebet_opp", "threebet", "f3b_opp", "f3b",
    "saw_flop", "cbet_opp", "cbet", "fcbet_opp", "fcbet",
    "post_aggr", "post_calls", "post_checks", "post_folds",
    "faced_bet", "folded_to_bet", "raised_vs_bet", "wtsd", "wsd",
]


def new_stats(name):
    d = {f: 0 for f in STAT_FIELDS}
    d["name"] = name
    d["shown"] = []
    return d


def _pct(a, b):
    return (a / b) if b else None


def describe_line(actions):
    """Short text of a player's actions in a hand, e.g. 'Raise pre / Bet flop / Call turn'."""
    names = ["pre", "flop", "turn", "river"]
    parts = []
    for street in range(4):
        acts = [a["action"] for a in actions if a["street"] == street]
        if acts:
            parts.append("%s %s" % ("-".join(x.capitalize() for x in acts), names[street]))
    return " / ".join(parts)


class Tracker:
    def __init__(self, data=None, table_size=6):
        self.stats = {}
        self.table_size = table_size
        if data:
            for pid, st in data.items():
                base = new_stats(st.get("name", pid))
                base.update(st)
                self.stats[pid] = base

    def to_json(self):
        return self.stats

    def get(self, pid, name=None):
        if pid not in self.stats:
            self.stats[pid] = new_stats(name or pid)
        elif name:
            self.stats[pid]["name"] = name
        return self.stats[pid]

    # ------------------------------------------------------------ record
    def record_hand(self, hand, winners=None):
        acts = hand.actions
        saw_flop_possible = len(hand.board) >= 3
        pfa = hand.preflop_aggressor
        # first flop bet (for c-bet / fold-to-c-bet)
        flop_acts = [a for a in acts if a["street"] == 1]
        first_flop_bet = next((a for a in flop_acts if a["action"] == "bet"), None)
        cbet_made = first_flop_bet is not None and first_flop_bet["seat"] == pfa
        showdown = hand.result and hand.result.get("reason") == "showdown"
        winners = winners or []

        for seat, pl in hand.p.items():
            pid = pl["pid"]
            st = self.get(pid, pl["name"])
            st["hands"] += 1
            mine = [a for a in acts if a["seat"] == seat]
            pre = [a for a in mine if a["street"] == 0]
            if any(a["action"] in ("call", "bet", "raise") for a in pre):
                st["vpip"] += 1
            if any(a["action"] in ("bet", "raise") for a in pre):
                st["pfr"] += 1
            if pre and pre[0]["action"] == "call" and pre[0]["raises_before"] == 0:
                st["limp"] += 1
            three_opp = [a for a in pre if a["raises_before"] == 1]
            if three_opp:
                st["threebet_opp"] += 1
                if three_opp[0]["action"] in ("raise", "bet"):
                    st["threebet"] += 1
            opened = any(a["action"] == "raise" and a["raises_before"] == 0 for a in pre)
            if opened:
                facing3 = [a for a in pre if a["raises_before"] >= 2]
                if facing3:
                    st["f3b_opp"] += 1
                    if facing3[0]["action"] == "fold":
                        st["f3b"] += 1
            folded_pre = any(a["action"] == "fold" for a in pre)
            saw_flop = saw_flop_possible and not folded_pre
            if saw_flop:
                st["saw_flop"] += 1
            # c-bet
            if saw_flop and seat == pfa:
                my_flop = [a for a in mine if a["street"] == 1]
                if my_flop and my_flop[0]["raises_before"] == 0:
                    st["cbet_opp"] += 1
                    if my_flop[0]["action"] == "bet":
                        st["cbet"] += 1
            if cbet_made and seat != pfa:
                after = [a for a in mine if a["street"] == 1 and a["raises_before"] == 1
                         and acts.index(a) > acts.index(first_flop_bet)]
                if after:
                    st["fcbet_opp"] += 1
                    if after[0]["action"] == "fold":
                        st["fcbet"] += 1
            for a in mine:
                if a["street"] == 0:
                    continue
                if a["action"] in ("bet", "raise"):
                    st["post_aggr"] += 1
                elif a["action"] == "call":
                    st["post_calls"] += 1
                elif a["action"] == "check":
                    st["post_checks"] += 1
                elif a["action"] == "fold":
                    st["post_folds"] += 1
                if a["facing_bet"]:
                    st["faced_bet"] += 1
                    if a["action"] == "fold":
                        st["folded_to_bet"] += 1
                    elif a["action"] == "raise":
                        st["raised_vs_bet"] += 1
            if showdown and saw_flop and not pl["folded"]:
                st["wtsd"] += 1
                if seat in winners:
                    st["wsd"] += 1
            if pl["shown"] and not pl["is_hero"]:
                entry = {
                    "cards": pretty_cards(pl["shown"]),
                    "class": hand_class(*pl["shown"]),
                    "position": pl["position"],
                    "line": describe_line(mine),
                    "board": pretty_cards(hand.board),
                    "made": describe_score(evaluate(list(pl["shown"]) + hand.board)) if len(hand.board) >= 3 else "",
                    "won": seat in winners,
                }
                st["shown"] = ([entry] + st["shown"])[:30]

    # ------------------------------------------------------------ views
    def estimate(self, pid, stat):
        """Shrunk estimate of a frequency stat."""
        st = self.stats.get(pid) or new_stats(pid)
        prior, k = PRIORS[stat]
        ev, ep = expected_vpip_pfr(self.table_size)
        if stat == "vpip":
            prior = 1.1 * ev           # the average player is a bit looser than a solid one
        elif stat == "pfr":
            prior = 0.8 * ep
        elif stat == "threebet":
            prior = 0.06 * (1 + max(0, 6 - self.table_size) * 0.12)
        if stat == "fold_to_cbet":
            # few c-bet samples: lean on how often this player folds to bets in general
            prior = self.estimate(pid, "fold_to_bet")
        if stat == "vpip":
            x, n = st["vpip"], st["hands"]
        elif stat == "pfr":
            x, n = st["pfr"], st["hands"]
        elif stat == "limp":
            x, n = st["limp"], st["hands"]
        elif stat == "threebet":
            x, n = st["threebet"], st["threebet_opp"]
        elif stat == "fold_to_3bet":
            x, n = st["f3b"], st["f3b_opp"]
        elif stat == "cbet":
            x, n = st["cbet"], st["cbet_opp"]
        elif stat == "fold_to_cbet":
            x, n = st["fcbet"], st["fcbet_opp"]
        elif stat == "fold_to_bet":
            x, n = st["folded_to_bet"], st["faced_bet"]
        elif stat == "afq":
            x = st["post_aggr"]
            n = st["post_aggr"] + st["post_calls"] + st["post_folds"] + st["post_checks"]
        elif stat == "wtsd":
            x, n = st["wtsd"], st["saw_flop"]
        else:
            raise KeyError(stat)
        return (x + prior * k) / (n + k)

    def model(self, pid):
        """All the estimates the advisor uses for one player."""
        return {s: self.estimate(pid, s) for s in PRIORS}

    def raw(self, pid):
        st = self.stats.get(pid) or new_stats(pid)
        agg = st["post_aggr"]
        calls = st["post_calls"]
        return {
            "hands": st["hands"],
            "vpip": _pct(st["vpip"], st["hands"]),
            "pfr": _pct(st["pfr"], st["hands"]),
            "limp": _pct(st["limp"], st["hands"]),
            "threebet": _pct(st["threebet"], st["threebet_opp"]),
            "fold_to_3bet": _pct(st["f3b"], st["f3b_opp"]),
            "cbet": _pct(st["cbet"], st["cbet_opp"]),
            "fold_to_cbet": _pct(st["fcbet"], st["fcbet_opp"]),
            "fold_to_bet": _pct(st["folded_to_bet"], st["faced_bet"]),
            "af": (agg / calls) if calls else (None if not agg else float(agg)),
            "afq": _pct(agg, agg + calls + st["post_folds"] + st["post_checks"]),
            "wtsd": _pct(st["wtsd"], st["saw_flop"]),
            "wsd": _pct(st["wsd"], st["wtsd"]),
            "samples": {
                "threebet": st["threebet_opp"], "fold_to_3bet": st["f3b_opp"], "cbet": st["cbet_opp"],
                "fold_to_cbet": st["fcbet_opp"], "fold_to_bet": st["faced_bet"],
                "post_actions": agg + calls + st["post_folds"] + st["post_checks"],
                "saw_flop": st["saw_flop"],
            },
        }

    def profile(self, pid):
        """Player type from shrunk VPIP / PFR / aggression."""
        st = self.stats.get(pid) or new_stats(pid)
        n = st["hands"]
        m = self.model(pid)
        vpip, pfr, afq, ftb = m["vpip"], m["pfr"], m["afq"], m["fold_to_bet"]
        ratio = pfr / vpip if vpip > 0 else 0
        aggressive = ratio >= 0.5 and afq >= 0.33
        passive = ratio < 0.35 or afq < 0.28
        # looseness relative to a solid player at this table size (heads-up everyone plays more hands)
        vrel = vpip / expected_vpip_pfr(self.table_size)[0]
        if n < 6:
            key, label = "unknown", "Unknown"
        elif vrel < 0.55:
            key, label = "nit", "Nit"
        elif aggressive and (vrel > 1.75 or (vrel > 0.85 and afq >= 0.55)):
            key, label = "maniac", "Maniac"
        elif passive and ((vrel > 1.23 and ftb < 0.42) or (vrel > 0.7 and ftb < 0.3)):
            key, label = "station", "Calling station"
        elif vrel > 1.23 and aggressive:
            key, label = "lag", "Loose-aggressive (LAG)"
        elif vrel > 1.23:
            key, label = "loose_passive", "Loose-passive"
        elif vrel <= 0.92 and passive:
            key, label = "rock", "Tight-passive (Rock)"
        elif vrel <= 0.92:
            key, label = "tag", "Tight-aggressive (TAG)"
        elif aggressive:
            key, label = "reg", "Regular (aggressive)"
        else:
            key, label = "reg_passive", "Regular (passive)"
        if n < 20:
            conf = "low"
        elif n < 60:
            conf = "medium"
        else:
            conf = "high"
        return {"key": key, "label": label, "confidence": conf, "hands": n}

    def exploit_notes(self, pid):
        prof = self.profile(pid)
        k = prof["key"]
        notes = {
            "nit": "Plays very few hands: steal their blinds, give their raises a lot of credit, bluff them more.",
            "maniac": "Bets and raises with weak hands: call down lighter, let them bluff, trap with strong hands.",
            "station": "Calls too much and rarely folds: value-bet thinner and bigger, almost never bluff.",
            "lag": "Wide and aggressive: 3-bet and call a bit lighter against them, avoid thin bluffs.",
            "loose_passive": "Plays many hands passively: isolate them with raises, value-bet; their bets mean strength.",
            "rock": "Tight and passive: their bets/raises are strong; bluff them when they check.",
            "tag": "Solid player: stay close to standard play, avoid marginal spots.",
            "reg": "Fairly standard aggressive player.",
            "reg_passive": "Fairly standard but passive: their aggression usually means a strong hand.",
        }
        return notes.get(k)

    def summary(self, pid):
        st = self.stats.get(pid) or new_stats(pid)
        return {
            "pid": pid,
            "name": st.get("name", pid),
            "raw": self.raw(pid),
            "model": {k: round(v, 3) for k, v in self.model(pid).items()},
            "profile": self.profile(pid),
            "note": self.exploit_notes(pid),
            "shown": st.get("shown", []),
        }


def benchmarks(table_size):
    """Typical ranges for a solid winning player at this table size, with coaching tips."""
    ev, ep = expected_vpip_pfr(table_size)
    short = table_size <= 3
    return [
        {"key": "vpip", "label": "VPIP", "lo": round(ev * 0.85, 3), "hi": round(ev * 1.15, 3), "min_n": 20,
         "low": "You play very few hands: open up a little, especially on the button and cutoff.",
         "high": "You play too many hands: fold more weak offsuit hands, especially out of position."},
        {"key": "pfr", "label": "PFR", "lo": round(ep * 0.85, 3), "hi": round(ep * 1.2, 3), "min_n": 20,
         "low": "You call more than you raise: when a hand is worth playing, raising usually beats limping or calling.",
         "high": "You raise very often: make sure your raises still have enough strong hands in them."},
        {"key": "threebet", "label": "3-bet", "lo": 0.10 if short else 0.06, "hi": 0.18 if short else 0.11, "min_n": 15,
         "low": "You rarely re-raise: 3-bet your strongest hands (and a few suited bluffs) instead of just calling.",
         "high": "You 3-bet a lot: good players will start 4-betting you light."},
        {"key": "af", "label": "Aggression factor", "lo": 1.8, "hi": 3.5, "min_n": 15,
         "low": "Post-flop you call much more than you bet: bet your strong hands and good draws.",
         "high": "Very aggressive post-flop: make sure you are not bluffing calling stations."},
        {"key": "cbet", "label": "C-bet", "lo": 0.5, "hi": 0.72, "min_n": 10,
         "low": "You often give up after raising pre-flop: c-bet more on boards that favour you.",
         "high": "You c-bet almost every flop: check back some weak hands on bad boards."},
        {"key": "fold_to_bet", "label": "Fold to bet", "lo": 0.35, "hi": 0.55, "min_n": 15,
         "low": "You rarely fold to bets: let go of weak pairs against strong lines.",
         "high": "You fold to bets often: opponents can profit by bluffing you."},
        {"key": "wtsd", "label": "Went to showdown", "lo": 0.24, "hi": 0.33, "min_n": 15,
         "low": "You rarely reach showdown: you may be folding too often on later streets.",
         "high": "You go to showdown a lot: you may be calling down too light."},
        {"key": "wsd", "label": "Won at showdown", "lo": 0.48, "hi": 0.6, "min_n": 10,
         "low": "You lose many showdowns: tighten up your river calls.",
         "high": "You win most showdowns: you could call (or bluff-catch) a little more."},
    ]
