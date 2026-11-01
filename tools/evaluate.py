"""Evaluate the advisor.

1. Matches: the advisor plays simulated hands against scripted opponents
   (calling station, nit, maniac, solid regular).  The same card sequence
   is replayed for three hero types so results can be compared hand by hand:
     advisor         - the bot, remembering opponents across hands
     advisor_nomem   - the bot with its opponent memory wiped every hand
     baseline_tag    - a simple rule-based tight-aggressive player
2. Frequency tests against theory: opening ranges, defence vs opens,
   3-bet / 4-bet behaviour, c-betting, defence vs c-bets.
3. Adaptation tests: the same spots against different opponent profiles.

Usage: python tools/evaluate.py [hands_per_match] > report.json
"""

import json
import math
import os
import random
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from poker_bot.session import Session  # noqa: E402
from poker_bot.tracker import Tracker, new_stats  # noqa: E402
from poker_bot.advisor import Advisor, players_behind_preflop  # noqa: E402
from poker_bot.cards import hand_class, cards_str, COMBO_INDEX, ALL_CLASSES, combos_for_class  # noqa: E402
from poker_bot.ranges import CLASS_TOP_PCT, base_open_fraction, board_strengths  # noqa: E402
from poker_bot.game import HandError  # noqa: E402
from poker_bot.evaluator import evaluate  # noqa: E402

BUDGET = 0.05
MAX_ITERS = 2500

# ----------------------------------------------------------------- bots
PROFILES = {
    "station": dict(vpip=.50, pfr=.05, threebet=.02, f3b=.20, limp=True, value=.12, bluff=.02,
                    cbet=.25, call_thr=.22, raise_thr=.97, bluff_raise=0.0),
    "nit": dict(vpip=.13, pfr=.10, threebet=.03, f3b=.70, limp=False, value=.18, bluff=.03,
                cbet=.45, call_thr=.62, raise_thr=.92, bluff_raise=0.0),
    "maniac": dict(vpip=.60, pfr=.42, threebet=.18, f3b=.25, limp=False, value=.55, bluff=.45,
                   cbet=.85, call_thr=.30, raise_thr=.78, bluff_raise=.12),
    "tag": dict(vpip=.23, pfr=.18, threebet=.07, f3b=.50, limp=False, value=.32, bluff=.12,
                cbet=.62, call_thr=.45, raise_thr=.90, bluff_raise=.03),
}


def strength(h, hole):
    a, b = sorted(hole)
    return board_strengths(h.board)[COMBO_INDEX[(a, b)]]


def bot_act(prof, h, seat, hole, rng):
    """Rule-based player.  Returns (action, amount)."""
    L = h.legal()
    to_call, pot, bb, cur = L["to_call"], L["pot"], h.bb_amt, h.current_bet
    pl = h.p[seat]
    if h.street == 0:
        pct = CLASS_TOP_PCT[hand_class(*hole)]
        rb = h.raises_this_street
        nb = players_behind_preflop(h, seat)
        ow = min(0.85, max(0.03, base_open_fraction(nb) * prof["pfr"] / 0.15))
        limpers = sum(1 for a in h.actions if a["street"] == 0 and a["action"] == "call" and a["raises_before"] == 0)
        if rb == 0:
            if pct <= ow:
                return "raise", bb * (2.5 + limpers)
            if to_call == 0:
                return "check", None
            if prof["limp"] and pct <= prof["vpip"]:
                return "call", None
            return "fold", None
        if rb == 1:
            if pct <= prof["threebet"]:
                return "raise", cur * 3
            if pl["position"] == "BB":
                width = min(0.8, prof["vpip"] + 0.2)
            else:
                width = max(0.02, prof["vpip"] - 0.5 * prof["pfr"])
            if pct <= width:
                return "call", None
            return ("check", None) if to_call == 0 else ("fold", None)
        if rb == 2:
            opened = any(a["seat"] == seat and a["action"] == "raise" for a in h.actions if a["street"] == 0)
            if pct <= 0.025:
                return "raise", cur * 2.3
            if opened and pct <= ow * (1 - prof["f3b"]):
                return "call", None
            if pct <= 0.03:
                return "call", None
            return "fold", None
        if pct <= 0.015:
            return "allin", None
        if pct <= (0.06 if prof["f3b"] < 0.3 else 0.035):
            return "call", None
        return "fold", None
    s = strength(h, hole)
    if to_call == 0:
        if s >= 1 - prof["value"]:
            return "bet", round(pot * 0.66, 2)
        if h.street == 1 and h.preflop_aggressor == seat and rng.random() < prof["cbet"]:
            return "bet", round(pot * 0.5, 2)
        if s < 0.45 and rng.random() < prof["bluff"]:
            return "bet", round(pot * 0.66, 2)
        return "check", None
    b = to_call / max(pot - to_call, 1e-9)
    thr = prof["call_thr"] + 0.12 * (b - 0.66)
    if s >= prof["raise_thr"] or rng.random() < prof["bluff_raise"]:
        return "raise", cur * 3
    if s >= thr:
        return "call", None
    return "fold", None


# ---------------------------------------------------------------- match
def advisor_act(h, tracker):
    a = Advisor(h, tracker, time_budget=BUDGET, max_iters=MAX_ITERS).advise()
    r = a["recommendation"]
    return r["action"], r.get("to"), a


def run_match(args):
    hero_kind, opp_profiles, n_hands, seed = args
    card_rng = random.Random(seed)
    bot_rng = random.Random(seed * 7 + 1)
    n = 1 + len(opp_profiles)
    names = ["Hero"] + ["%s%d" % (p, i + 1) for i, p in enumerate(opp_profiles)]
    s = Session(config={"players": n, "hero_seat": 0, "button": seed % n, "names": names})
    prof_of = {i + 1: PROFILES[p] for i, p in enumerate(opp_profiles)}
    nets, events, labels = [], [], []
    checkpoints = {25, 50, 100, 200, 400}
    for hand_i in range(n_hands):
        for st in s.seats:
            s.update_seat(st["seat"], stack=100)
        if hero_kind == "advisor_nomem":
            s.tracker = Tracker()
        deck = list(range(52))
        card_rng.shuffle(deck)
        holes = {i: deck[2 * i:2 * i + 2] for i in range(n)}
        board = deck[2 * n:2 * n + 5]
        s.start_hand(cards_str(holes[0]))
        while True:
            h = s.hand
            if h.finished:
                break
            if h.awaiting_board:
                k = h.awaiting_board
                s.board(cards_str(board[len(h.board):len(h.board) + k]))
                continue
            seat = h.to_act
            to_call = h.to_call(seat)
            info = None
            if seat == 0:
                if hero_kind == "baseline_tag":
                    act, amt = bot_act(PROFILES["tag"], h, 0, holes[0], bot_rng)
                else:
                    act, amt, info = advisor_act(h, s.tracker)
                ev = {
                    "street": h.street, "action": act, "facing": to_call > 0,
                    "opps": len(h.live_seats()) - 1,
                    "pfa": h.preflop_aggressor == 0,
                    "raises": h.raises_this_street,
                    "s": strength(h, holes[0]) if h.board else CLASS_TOP_PCT[hand_class(*holes[0])],
                    "pot": h.pot(), "to_call": to_call,
                    "pos": h.p[0]["position"],
                }
                # was hero ahead of every live opponent's actual hand at the end?
                full = board
                hs = evaluate(holes[0] + full)
                ev["best_at_end"] = all(hs >= evaluate(holes[o] + full) for o in h.live_seats() if o != 0)
                events.append(ev)
            else:
                act, amt = bot_act(prof_of[seat], h, seat, holes[seat], bot_rng)
            try:
                s.act(seat, act, amt)
            except HandError:
                s.act(seat, "call" if to_call > 0 else "check")
        h = s.hand
        if h.result["reason"] == "showdown":
            for o in h.live_seats():
                if o != 0:
                    s.show(o, cards_str(holes[o]))
        res = s.finish()
        nets.append(res["hero_net"])
        if hero_kind == "advisor" and (hand_i + 1) in checkpoints:
            labels.append({"hands": hand_i + 1,
                           "labels": {names[i]: s.tracker.profile(s.seats[i]["pid"])["key"] for i in range(1, n)}})
    hero_raw = s.tracker.raw(s.seats[0]["pid"]) if hero_kind != "advisor_nomem" else None
    return {"kind": hero_kind, "opps": opp_profiles, "seed": seed, "nets": nets, "events": events,
            "labels": labels, "hero_raw": hero_raw}


def summarize(results, bb=1.0):
    nets = [x for r in results for x in r["nets"]]
    n = len(nets)
    mean = sum(nets) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in nets) / max(n - 1, 1))
    return {"hands": n, "bb100": round(100 * mean / bb, 1), "ci95": round(196 * sd / math.sqrt(n) / bb, 1)}


def paired_diff(a_results, b_results):
    a = [x for r in sorted(a_results, key=lambda r: r["seed"]) for x in r["nets"]]
    b = [x for r in sorted(b_results, key=lambda r: r["seed"]) for x in r["nets"]]
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    mean = sum(d) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / max(n - 1, 1))
    return {"bb100": round(100 * mean, 1), "ci95": round(196 * sd / math.sqrt(n), 1)}


def behaviour(results):
    ev = [e for r in results for e in r["events"]]
    out = {}

    def frac(sel, cond):
        xs = [e for e in ev if sel(e)]
        if not xs:
            return None, 0
        return round(sum(1 for e in xs if cond(e)) / len(xs), 3), len(xs)

    aggr = lambda e: e["action"] in ("bet", "raise", "allin")
    out["cbet_flop_as_pfa"] = frac(lambda e: e["street"] == 1 and e["pfa"] and not e["facing"] and e["raises"] == 0, aggr)
    out["cbet_flop_hu"] = frac(lambda e: e["street"] == 1 and e["pfa"] and not e["facing"] and e["opps"] == 1, aggr)
    out["cbet_flop_multiway"] = frac(lambda e: e["street"] == 1 and e["pfa"] and not e["facing"] and e["opps"] >= 2, aggr)
    out["bet_when_checked_to_turn"] = frac(lambda e: e["street"] == 2 and not e["facing"], aggr)
    out["bet_when_checked_to_river"] = frac(lambda e: e["street"] == 3 and not e["facing"], aggr)
    # bluffs: postflop bets/raises with weak hands (absolute strength < 0.5)
    out["bluff_share_of_flop_bets"] = frac(lambda e: e["street"] == 1 and aggr(e), lambda e: e["s"] < 0.5)
    out["bluff_share_of_turn_bets"] = frac(lambda e: e["street"] == 2 and aggr(e), lambda e: e["s"] < 0.5)
    out["bluff_share_of_river_bets"] = frac(lambda e: e["street"] == 3 and aggr(e), lambda e: e["s"] < 0.5)
    out["value_bet_strong_river"] = frac(lambda e: e["street"] == 3 and not e["facing"] and e["s"] >= 0.85, aggr)
    out["fold_to_flop_bet"] = frac(lambda e: e["street"] == 1 and e["facing"], lambda e: e["action"] == "fold")
    out["fold_to_turn_bet"] = frac(lambda e: e["street"] == 2 and e["facing"], lambda e: e["action"] == "fold")
    out["fold_to_river_bet"] = frac(lambda e: e["street"] == 3 and e["facing"], lambda e: e["action"] == "fold")
    out["raise_vs_flop_bet"] = frac(lambda e: e["street"] == 1 and e["facing"], aggr)
    # decision quality against the actual cards (noisy but informative)
    out["river_call_was_best"] = frac(lambda e: e["street"] == 3 and e["facing"] and e["action"] == "call", lambda e: e["best_at_end"])
    out["river_fold_was_behind"] = frac(lambda e: e["street"] == 3 and e["facing"] and e["action"] == "fold", lambda e: not e["best_at_end"])
    out["river_value_bet_was_best"] = frac(lambda e: e["street"] == 3 and aggr(e) and e["s"] >= 0.5, lambda e: e["best_at_end"])
    return {k: {"freq": v[0], "n": v[1]} for k, v in out.items()}


# ------------------------------------------------------- spot / frequency
def _seat_session(n, hero_pos, opp_stats=None, seed=0):
    """Session with hero sitting in position `hero_pos` (6-max naming)."""
    from poker_bot.game import position_names
    names = position_names(n)
    idx = names.index(hero_pos)          # index from the button
    hero = 0
    button = (hero - idx) % n
    s = Session(config={"players": n, "hero_seat": hero, "button": button})
    if opp_stats:
        for st in s.seats:
            if st["seat"] != hero:
                s.tracker.stats[st["pid"]] = dict(opp_stats, name=st["name"])
    return s


def weighted(classes_actions):
    tot = 0
    acc = {}
    for lab, act in classes_actions:
        w = len(combos_for_class(lab))
        tot += w
        acc[act] = acc.get(act, 0) + w
    return {k: round(v / tot, 3) for k, v in sorted(acc.items())}


def example_cards(label):
    a, b = combos_for_class(label)[0]
    return cards_str([a, b])


def rfi_ranges(n=6, opp_stats=None):
    from poker_bot.game import position_names
    out = {}
    for pos in position_names(n):
        if pos == "BB":
            continue
        res = []
        for lab in ALL_CLASSES:
            s = _seat_session(n, pos, opp_stats)
            s.start_hand(example_cards(lab))
            h = s.hand
            while h.to_act != 0:
                s.act(h.to_act, "fold")
            a = Advisor(h, s.tracker, time_budget=0.01, max_iters=400).advise()
            res.append((lab, a["recommendation"]["action"]))
        out[pos] = weighted(res)
    return out


def facing_open(hero_pos, opener_pos, n=6, opp_stats=None, restrict=None):
    """Hero's response to a 2.5bb open (everyone else folds)."""
    res = []
    for lab in ALL_CLASSES:
        if restrict and lab not in restrict:
            continue
        s = _seat_session(n, hero_pos, opp_stats)
        try:
            s.start_hand(example_cards(lab))
        except HandError:
            continue
        h = s.hand
        while h.to_act != 0:
            seat = h.to_act
            if h.p[seat]["position"] == opener_pos and h.raises_this_street == 0:
                s.act(seat, "raise", 2.5)
            else:
                s.act(seat, "fold")
        a = Advisor(h, s.tracker, time_budget=0.02, max_iters=1200).advise()
        res.append((lab, a["recommendation"]["action"]))
    return weighted(res), res


def facing_3bet(hero_pos, threebettor_pos, n=6, opp_stats=None, villain_stats=None):
    # only hands hero would open
    opens = []
    for lab in ALL_CLASSES:
        s = _seat_session(n, hero_pos, opp_stats)
        s.start_hand(example_cards(lab))
        h = s.hand
        while h.to_act != 0:
            s.act(h.to_act, "fold")
        a = Advisor(h, s.tracker, time_budget=0.01, max_iters=300).advise()
        if a["recommendation"]["action"] == "raise":
            opens.append(lab)
    res = []
    for lab in opens:
        s = _seat_session(n, hero_pos, opp_stats)
        if villain_stats:
            for st in s.seats:
                if st["seat"] != 0:
                    s.tracker.stats[st["pid"]] = dict(villain_stats, name=st["name"])
        s.start_hand(example_cards(lab))
        h = s.hand
        while True:
            seat = h.to_act
            if seat == 0 and h.raises_this_street == 0:
                s.act(0, "raise", 2.5)
            elif seat == 0:
                break
            elif h.p[seat]["position"] == threebettor_pos and h.raises_this_street == 1:
                s.act(seat, "raise", 8.5)
            else:
                s.act(seat, "fold")
        a = Advisor(h, s.tracker, time_budget=0.02, max_iters=1200).advise()
        res.append((lab, a["recommendation"]["action"]))
    return weighted(res), len(opens)


def flop_spots(role, n_samples, opp_stats=None, hero_classes=None, villain_bet=None, seed=11, hand_filter=None):
    """BTN (opener) vs BB (caller) single-raised pot, random flops.
    role='pfa': hero is BTN and BB checks.   role='def': hero is BB, checks, BTN bets `villain_bet` x pot."""
    rng = random.Random(seed)
    acts = []
    tries = 0
    while len(acts) < n_samples and tries < n_samples * 30:
        tries += 1
        lab = rng.choice(hero_classes)
        combo = rng.choice(combos_for_class(lab))
        deck = [c for c in range(52) if c not in combo]
        flop = rng.sample(deck, 3)
        if hand_filter and not hand_filter(list(combo), flop):
            continue
        hero_pos = "BTN" if role == "pfa" else "BB"
        s = _seat_session(6, hero_pos, opp_stats)
        s.start_hand(cards_str(list(combo)))
        h = s.hand
        while not h.awaiting_board:
            seat = h.to_act
            pos = h.p[seat]["position"]
            if pos == "BTN" and h.raises_this_street == 0:
                s.act(seat, "raise", 2.5)
            elif pos == "BB":
                s.act(seat, "call")
            else:
                s.act(seat, "fold")
        s.board(cards_str(flop))
        if role == "pfa":
            s.act(h.to_act, "check")
        else:
            s.act(0, "check")
            s.act(h.to_act, "bet", round(h.pot() * villain_bet, 2))
        a = Advisor(h, s.tracker, time_budget=0.02, max_iters=1200).advise()
        acts.append(a["recommendation"]["action"])
    tot = len(acts)
    return {k: round(acts.count(k) / tot, 3) for k in sorted(set(acts))}


def river_spot(hero_cards, board, villain_stats, villain_line):
    """Hero BTN vs BB, single raised pot; checked down to river. villain_line:
    'check' -> hero to act checked to; 'bet' -> BB leads pot on river."""
    s = _seat_session(6, "BTN", villain_stats)
    s.start_hand(hero_cards)
    h = s.hand
    while not h.awaiting_board:
        seat = h.to_act
        pos = h.p[seat]["position"]
        if pos == "BTN" and h.raises_this_street == 0:
            s.act(seat, "raise", 2.5)
        elif pos == "BB":
            s.act(seat, "call")
        else:
            s.act(seat, "fold")
    s.board(board[:6])
    s.act(h.to_act, "check"); s.act(0, "bet", 2.0); s.act(h.to_act, "call")
    s.board(board[6:8])
    s.act(h.to_act, "check"); s.act(0, "check")
    s.board(board[8:10])
    if villain_line == "check":
        s.act(h.to_act, "check")
    else:
        s.act(h.to_act, "bet", h.pot())
    a = Advisor(h, s.tracker, time_budget=0.05, max_iters=4000, seed=5).advise()
    return a["recommendation"]["action"], a["equity"]


def stats_for(profile, hands=300):
    """Tracker counts matching a scripted profile (as if observed for `hands` hands)."""
    p = PROFILES[profile]
    d = new_stats(profile)
    d["hands"] = hands
    d["vpip"] = int(p["vpip"] * hands)
    d["pfr"] = int(p["pfr"] * hands)
    d["threebet_opp"] = int(hands * 0.3)
    d["threebet"] = int(p["threebet"] * d["threebet_opp"])
    d["f3b_opp"] = 30
    d["f3b"] = int(p["f3b"] * 30)
    d["saw_flop"] = int(p["vpip"] * hands * 0.8)
    d["faced_bet"] = int(hands * 0.3)
    fold = {"station": 0.15, "nit": 0.62, "maniac": 0.3, "tag": 0.45}[profile]
    d["folded_to_bet"] = int(fold * d["faced_bet"])
    d["cbet_opp"] = 40
    d["cbet"] = int(p["cbet"] * 40)
    d["fcbet_opp"] = 40
    d["fcbet"] = int(fold * 40)
    acts = int(hands * 0.6)
    afq = {"station": 0.08, "nit": 0.25, "maniac": 0.6, "tag": 0.38}[profile]
    d["post_aggr"] = int(afq * acts)
    d["post_calls"] = int((1 - afq) * acts * 0.5)
    d["post_checks"] = int((1 - afq) * acts * 0.35)
    d["post_folds"] = int((1 - afq) * acts * 0.15)
    return d


def main():
    hands = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    t0 = time.time()
    report = {"hands_per_match": hands}
    chunks = 8
    per = hands // chunks
    matchups = {"HU vs station": ["station"], "HU vs nit": ["nit"], "HU vs maniac": ["maniac"],
                "HU vs TAG": ["tag"], "6-max mixed": ["station", "nit", "maniac", "tag", "station"]}
    jobs = []
    for name, opps in matchups.items():
        for kind in ("advisor", "advisor_nomem", "baseline_tag"):
            for c in range(chunks):
                jobs.append((name, (kind, opps, per, 1000 + 17 * c + len(name))))
    with Pool() as pool:
        out = pool.map(run_match, [j[1] for j in jobs], chunksize=1)
    by = {}
    for (name, args), r in zip(jobs, out):
        by.setdefault(name, {}).setdefault(args[0], []).append(r)
    report["matches"] = {}
    for name, kinds in by.items():
        m = {k: summarize(v) for k, v in kinds.items()}
        m["advisor_minus_baseline"] = paired_diff(kinds["advisor"], kinds["baseline_tag"])
        m["memory_gain"] = paired_diff(kinds["advisor"], kinds["advisor_nomem"])
        m["behaviour"] = behaviour(kinds["advisor"])
        m["hero_stats"] = kinds["advisor"][0]["hero_raw"]
        m["labels"] = kinds["advisor"][0]["labels"]
        report["matches"][name] = m
    report["match_seconds"] = round(time.time() - t0)
    print("matches done", report["match_seconds"], file=sys.stderr)

    # frequency tests (default population priors)
    t1 = time.time()
    report["rfi_6max"] = rfi_ranges(6)
    report["bb_vs_btn"] = facing_open("BB", "BTN")[0]
    report["btn_vs_utg"] = facing_open("BTN", "UTG")[0]
    report["sb_vs_btn"] = facing_open("SB", "BTN")[0]
    report["co_vs_btn_3bet"] = facing_3bet("CO", "BTN")
    btn_open = [lab for lab in ALL_CLASSES if CLASS_TOP_PCT[lab] <= 0.45]
    bb_def = [lab for lab, act in facing_open("BB", "BTN")[1] if act != "fold"]
    report["cbet_btn_vs_bb"] = flop_spots("pfa", 300, hero_classes=btn_open)
    report["bb_vs_cbet_33"] = flop_spots("def", 300, hero_classes=bb_def, villain_bet=0.33)
    report["bb_vs_cbet_75"] = flop_spots("def", 300, hero_classes=bb_def, villain_bet=0.75)
    print("frequency tests done", round(time.time() - t1), file=sys.stderr)

    # adaptation tests
    t2 = time.time()
    adapt = {}
    profs = ["station", "nit", "maniac", "tag"]
    adapt["btn_steal_pct"] = {}
    for p in profs:
        res = []
        st = stats_for(p)
        for lab in ALL_CLASSES:
            s = _seat_session(6, "BTN", st)
            s.start_hand(example_cards(lab))
            h = s.hand
            while h.to_act != 0:
                s.act(h.to_act, "fold")
            a = Advisor(h, s.tracker, time_budget=0.01, max_iters=300).advise()
            res.append((lab, a["recommendation"]["action"]))
        adapt["btn_steal_pct"][p] = weighted(res).get("raise", 0)

    def is_air(combo, flop):
        from poker_bot.ranges import draw_info
        sc = evaluate(combo + flop)
        d = draw_info(combo, flop)
        return (sc >> 20) == 0 and not (d["fd"] or d["oesd"] or d["gut"])

    def is_medium(combo, flop):
        sc = evaluate(combo + flop)
        return (sc >> 20) == 1
    adapt["cbet_with_air"] = {p: flop_spots("pfa", 150, stats_for(p), btn_open, seed=3, hand_filter=is_air).get("bet", 0)
                              for p in profs}
    adapt["bet_one_pair_flop"] = {p: flop_spots("pfa", 150, stats_for(p), btn_open, seed=4, hand_filter=is_medium).get("bet", 0)
                                  for p in profs}
    # river bluff catching and thin value
    river_boards = ["Kd7s2c", "4h", "9d"]
    bc = {}
    thin = {}
    bluff = {}
    for p in profs:
        st = stats_for(p)
        bc[p] = [river_spot(hc, "".join(river_boards), st, "bet")[0] for hc in ["8c7c", "Ts7h", "QcQh", "Ac7c", "JdJs"]]
        thin[p] = [river_spot(hc, "".join(river_boards), st, "check")[0] for hc in ["KcTc", "Kh8h", "QcQh", "Ac7c", "9h9s"]]
        bluff[p] = [river_spot(hc, "".join(river_boards), st, "check")[0] for hc in ["6h5h", "QhJh", "Th8h", "5c3c", "JsTs"]]
    adapt["river_bluffcatch_calls_of_5"] = {p: sum(1 for x in v if x in ("call", "raise", "allin")) for p, v in bc.items()}
    adapt["river_thin_value_bets_of_5"] = {p: sum(1 for x in v if x in ("bet", "raise", "allin")) for p, v in thin.items()}
    adapt["river_bluffs_of_5"] = {p: sum(1 for x in v if x in ("bet", "raise", "allin")) for p, v in bluff.items()}
    adapt["vs_3bet_continue"] = {}
    for p in ("nit", "maniac", "tag"):
        r, n_open = facing_3bet("CO", "BTN", villain_stats=stats_for(p))
        adapt["vs_3bet_continue"][p] = round(1 - r.get("fold", 0), 3)
    report["adaptation"] = adapt
    print("adaptation tests done", round(time.time() - t2), file=sys.stderr)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
