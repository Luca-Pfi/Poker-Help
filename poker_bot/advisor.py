"""The decision engine.

1.  Estimate each opponent's range by replaying their actions in this hand,
    using their session statistics (VPIP, PFR, 3-bet, aggression, fold-to-bet...).
2.  For every option (fold / check / call / bet or raise sizes / all-in) work
    out who would continue and with which part of their range.
3.  Monte Carlo the rest of the hand and compute the expected value (EV) of
    each option relative to folding.
4.  Recommend the best option and explain the maths.
"""

from .cards import hand_class, pretty_cards, RANK_NAMES, RANK_PLURALS
from .evaluator import (evaluate, describe_score, PAIR, TWO_PAIR, TRIPS, HIGH_CARD)
from .ranges import (N_COMBOS, PREFLOP_STRENGTH, CLASS_TOP_PCT, top_range, base_open_fraction,
                     board_strengths, within_range_percentile, narrow, downweight_top, draw_info,
                     range_grid, range_size, top_classes, range_notation)
from .equity import OpponentSampler, simulate
from .cards import ALL_COMBOS


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _fmt(x):
    x = float(x)
    if abs(x - round(x)) < 1e-9:
        return "%d" % round(x)
    return ("%.2f" % x).rstrip("0").rstrip(".")


def _pc(x):
    return "%.0f%%" % (100 * x)


# =================================================================== ranges
def preflop_order(hand):
    """Seats in preflop acting order."""
    if hand.n == 2:
        return [hand.order[0], hand.order[1]]
    return hand.order[3:] + hand.order[:3]


def players_behind_preflop(hand, seat):
    order = preflop_order(hand)
    return len(order) - order.index(seat) - 1


def open_width(model, players_behind):
    return _clamp(base_open_fraction(players_behind) * (model["pfr"] / 0.15), 0.03, 0.85)


def build_range(hand, seat, model):
    """Weighted range (1326 floats) for `seat` given their actions so far."""
    w = [1.0] * N_COMBOS
    pl = hand.p[seat]
    pos = pl["position"]
    entered = False      # voluntarily put money in preflop
    opened = False
    had_range = False    # range already narrowed by a voluntary action
    limpers = 0
    for a in hand.actions:
        if a["street"] == 0 and a["seat"] != seat and a["action"] == "call" and a["raises_before"] == 0:
            limpers += 1
        if a["seat"] != seat:
            continue
        act = a["action"]
        if act == "fold":
            return [0.0] * N_COMBOS
        if a["street"] == 0:
            rb = a["raises_before"]
            nb = players_behind_preflop(hand, seat)
            ow = open_width(model, nb)
            vpip, pfr = model["vpip"], model["pfr"]
            if rb == 0:
                if act in ("raise", "bet"):
                    if entered:  # limp-raise: very strong
                        pct = within_range_percentile(w, PREFLOP_STRENGTH)
                        w = narrow(w, pct, 0.15, floor=0.02)
                    else:
                        width = ow * (0.8 if limpers else 1.0)
                        w = [x * y for x, y in zip(w, top_range(width))]
                    opened = True
                elif act == "call":   # limp / complete
                    raise_share = _clamp(pfr / max(vpip, 0.01), 0.0, 1.0)
                    lw = top_range(_clamp(vpip * 1.1, 0.05, 0.95), softness=0.05)
                    rw = top_range(ow)
                    w = [x * l * (1.0 - 0.85 * raise_share * r) for x, l, r in zip(w, lw, rw)]
                    entered = True
                elif act == "check":  # big blind option
                    rw = top_range(_clamp(ow * 0.7, 0.03, 0.4))
                    w = [x * (1.0 - 0.8 * r) for x, r in zip(w, rw)]
            elif rb == 1:
                if act == "call":
                    if entered:   # limp-call
                        pct = within_range_percentile(w, PREFLOP_STRENGTH)
                        w = narrow(w, pct, 0.6, floor=0.05)
                    else:
                        if pos == "BB":
                            width = _clamp(vpip * 1.5, 0.12, 0.7)
                        elif pos == "SB":
                            width = _clamp(vpip * 0.7, 0.05, 0.4)
                        else:
                            width = _clamp(vpip * 0.55, 0.04, 0.45)
                        tb = top_range(_clamp(model["threebet"], 0.01, 0.3))
                        cw = top_range(width, softness=0.04)
                        w = [x * c * (1.0 - 0.7 * t) for x, c, t in zip(w, cw, tb)]
                    entered = True
                elif act in ("raise", "bet"):
                    if entered:
                        pct = within_range_percentile(w, PREFLOP_STRENGTH)
                        w = narrow(w, pct, 0.12, floor=0.02)
                    else:
                        w = [x * y for x, y in zip(w, top_range(_clamp(model["threebet"], 0.015, 0.3)))]
                    entered = True
            elif rb == 2:
                pct = within_range_percentile(w, PREFLOP_STRENGTH)
                if act == "call":
                    if opened or entered:
                        w = narrow(w, pct, _clamp(1 - model["fold_to_3bet"], 0.15, 0.85), floor=0.03,
                                   top_cut=0.2, top_weight=0.5)
                    else:
                        w = [x * y for x, y in zip(w, top_range(0.045))]
                elif act in ("raise", "bet"):
                    if opened or entered:
                        w = narrow(w, pct, 0.25, floor=0.04)
                    else:
                        w = [x * y for x, y in zip(w, top_range(0.025))]
                entered = True
            else:
                pct = within_range_percentile(w, PREFLOP_STRENGTH)
                if act == "call":
                    w = narrow(w, pct, 0.5, floor=0.03) if (opened or entered) else \
                        [x * y for x, y in zip(w, top_range(0.03))]
                elif act in ("raise", "bet"):
                    w = narrow(w, pct, 0.4, floor=0.03) if (opened or entered) else \
                        [x * y for x, y in zip(w, top_range(0.015))]
                entered = True
            had_range = had_range or act != "check"
        else:
            strengths = board_strengths(a["board"])
            pct = within_range_percentile(w, strengths)
            dec = hybrid_strength(pct, strengths)
            afq = model["afq"]
            river = a["street"] == 3
            b = (a["to"] - a["bet_before"]) / max(a["pot_before"], 1e-9)
            if act == "bet":
                is_cbet = a["street"] == 1 and hand.preflop_aggressor == seat
                keep = model["cbet"] if is_cbet else _clamp(0.25 + 0.6 * afq, 0.25, 0.75)
                # bigger bets and river bets are more value-heavy
                keep *= (1.66 / (1.0 + max(b, 0.1))) ** 0.5
                if river:
                    keep *= 0.8
                floor = _clamp((0.04 + 0.2 * afq) if river else (0.04 + 0.3 * afq), 0.04, 0.3)
                w = narrow(w, pct, _clamp(keep, 0.12, 0.9), floor=floor)
            elif act == "raise":
                keep = _clamp(0.06 + 0.3 * afq, 0.08, 0.3) * (0.8 if river else 1.0)
                w = narrow(w, pct, keep, floor=_clamp(0.02 + 0.15 * afq, 0.03, 0.15))
            elif act == "call":
                bf = a["to_call"] / max(a["pot_before"], 1e-9)
                keep = continue_postflop(model, bf, a["raises_before"] > 1)
                w = narrow(w, dec, keep, floor=0.02)
                pct2 = within_range_percentile(w, strengths)
                w = downweight_top(w, pct2, 0.08, _clamp(1 - 1.2 * afq, 0.3, 0.9))
            elif act == "check":
                checked_to = any(x["street"] == a["street"] and x is not a and x["seat"] != seat
                                 for x in hand.actions[:hand.actions.index(a)])
                if checked_to:   # checking behind / when checked to: weaker
                    factor = _clamp(1 - 1.5 * afq, 0.25, 0.85)
                else:            # first to act: checking to the aggressor is routine
                    factor = _clamp(1 - 0.5 * afq, 0.6, 0.95)
                w = downweight_top(w, pct, 0.2, factor)
    return w


def hybrid_strength(pct_within, abs_strength):
    """Decision strength post-flop: half position within their own range,
    half absolute hand strength (so strong ranges fold less, weak ones more)."""
    return [0.5 * p + 0.5 * a if a >= 0 else 0.0 for p, a in zip(pct_within, abs_strength)]


def continue_postflop(model, bet_frac, is_raise, cbet=False):
    """Probability an opponent continues facing a bet of `bet_frac` x pot."""
    f = model["fold_to_cbet"] if cbet else model["fold_to_bet"]
    if is_raise:
        f = _clamp(f + 0.05, 0.1, 0.92)
    # stats are observed at ~2/3 pot; bigger bets get fewer calls (but less than 1:1)
    size = (1.66 / (1.0 + max(bet_frac, 0.05))) ** 0.6
    return _clamp((1.0 - f) * size, 0.04, 0.97)


def reraise_share_postflop(model):
    """Share of an opponent's continuing hands that raise instead of calling."""
    return _clamp(0.12 + 0.35 * model["afq"], 0.1, 0.4)


def reraise_share_preflop(hand, seat, model, rr_level, p_cont):
    """Share of continuing hands that put in the next raise (rr_level = raise number)."""
    mine = [a for a in hand.actions if a["seat"] == seat and a["street"] == 0]
    entered = any(a["action"] in ("call", "raise", "bet") for a in mine)
    if rr_level <= 0:
        return 0.0
    if rr_level == 1:      # raising over hero's limp
        return _clamp(model["pfr"] / max(model["vpip"], 1e-6), 0.2, 0.7)
    if rr_level == 2:      # 3-bet against an open
        if entered:        # limper facing an iso-raise
            return 0.12
        return _clamp(model["threebet"] / max(p_cont, 1e-6), 0.1, 0.6)
    if rr_level == 3:      # 4-bet against a 3-bet
        return 0.3
    return 0.5


def continue_preflop(hand, seat, model, new_level, size_ratio=1.0):
    """Probability `seat` continues (calls or raises) once the pot has
    `new_level` raises in it.  size_ratio > 1 for bigger-than-standard raises."""
    pl = hand.p[seat]
    pos = pl["position"]
    vpip = model["vpip"]
    mine = [a for a in hand.actions if a["seat"] == seat and a["street"] == 0]
    entered = any(a["action"] in ("call", "raise", "bet") for a in mine)
    last_raiser = hand.last_aggressor == seat
    opened = any(a["action"] in ("raise", "bet") for a in mine)
    if new_level <= 0:
        if pos == "BB":
            return 1.0
        return 1.0 if entered else _clamp(vpip * 0.8, 0.05, 0.7)
    if new_level == 1:
        if entered:
            p = _clamp(0.45 + 0.5 * vpip, 0.3, 0.85)
        elif pos == "BB":
            p = _clamp(1.4 * vpip, 0.15, 0.75)
        elif pos == "SB":
            p = _clamp(0.7 * vpip, 0.05, 0.45)
        else:
            p = _clamp(0.5 * vpip, 0.03, 0.45)
    elif new_level == 2:
        if opened:
            p = 1.0 - model["fold_to_3bet"]
        elif entered:
            p = _clamp(0.25 + 0.3 * vpip, 0.1, 0.5)
        else:
            p = _clamp(0.12 * vpip, 0.01, 0.1)
    else:
        if last_raiser or opened:
            p = 0.55
        elif entered:
            p = 0.2
        else:
            p = 0.01
    if size_ratio > 1.0:
        # bigger raises get somewhat fewer calls, but strong hands continue anyway
        p = p / (min(size_ratio, 2.5) ** 0.35)
    return _clamp(p, 0.005, 1.0)


# ============================================================= descriptions
def describe_hand(hole, board):
    if not board:
        return None
    cards = list(hole) + list(board)
    score = evaluate(cards)
    cat = score >> 20
    hr = sorted((c >> 2 for c in hole), reverse=True)
    br = sorted((c >> 2 for c in board), reverse=True)
    ubr = sorted(set(br), reverse=True)
    if cat == PAIR:
        p = (score >> 16) & 0xF
        if hr[0] == hr[1] == p:
            if p > br[0]:
                return "Overpair (%s)" % RANK_PLURALS[p]
            return "Pocket %s (below the top card)" % RANK_PLURALS[p]
        if p in hr:
            kicker = hr[1] if hr[0] == p else hr[0]
            idx = ubr.index(p)
            kick_txt = "%s kicker" % RANK_NAMES[kicker]
            if idx == 0:
                return "Top pair (%s), %s" % (RANK_PLURALS[p], kick_txt)
            if idx == 1:
                return "Second pair (%s), %s" % (RANK_PLURALS[p], kick_txt)
            return "Low pair (%s)" % RANK_PLURALS[p]
        return "Pair on the board, %s high" % RANK_NAMES[hr[0]]
    if cat == TWO_PAIR:
        p1 = (score >> 16) & 0xF
        p2 = (score >> 12) & 0xF
        using = sum(1 for r in (p1, p2) if r in hr)
        if using == 2 and hr[0] != hr[1]:
            return "Two pair (%s and %s), using both cards" % (RANK_PLURALS[p1], RANK_PLURALS[p2])
        if using == 0:
            return "Two pair on the board, %s high" % RANK_NAMES[hr[0]]
        return describe_score(score) + " (one pair on board)"
    if cat == TRIPS:
        t = (score >> 16) & 0xF
        if hr[0] == hr[1] == t:
            return "Set of %s" % RANK_PLURALS[t]
        if t in hr:
            return "Trips (%s)" % RANK_PLURALS[t]
        return "Trips on the board"
    if cat == HIGH_CARD:
        return "%s high (no pair)" % RANK_NAMES[hr[0]]
    return describe_score(score)


def describe_draws(d):
    out = []
    if d["fd"]:
        out.append("flush draw")
    if d["oesd"]:
        out.append("open-ended straight draw")
    elif d["gut"]:
        out.append("gutshot")
    if d["bdfd"] and not d["fd"]:
        out.append("backdoor flush draw")
    return out


# ================================================================ advisor
class Advisor:
    def __init__(self, hand, tracker, time_budget=1.2, max_iters=60000, seed=None):
        self.h = hand
        self.t = tracker
        self.time_budget = time_budget
        self.max_iters = max_iters
        self.seed = seed

    # --------------------------------------------------------------- main
    def advise(self):
        h = self.h
        hero = h.hero_seat
        if h.finished:
            return {"status": "finished"}
        if h.awaiting_board:
            return {"status": "awaiting_board"}
        if h.to_act != hero:
            return {"status": "waiting", "to_act": h.to_act}
        if len(h.hero_cards) != 2:
            return {"status": "no_cards"}

        bb = h.bb_amt
        street = h.street
        board = list(h.board)
        hole = list(h.hero_cards)
        me = h.p[hero]
        pot = h.pot()
        to_call = h.to_call(hero)
        stack = me["stack"]
        max_to = me["street_bet"] + stack
        dead = 0
        for c in hole + board:
            dead |= 1 << c

        opps = [s for s in h.live_seats() if s != hero]
        models = {s: self.t.model(h.p[s]["pid"]) for s in opps}
        ranges = {}
        pcts = {}
        cur_strength = board_strengths(board) if board else PREFLOP_STRENGTH
        for s in opps:
            w = build_range(h, s, models[s])
            ranges[s] = w
            # pre-flop: position within their range; post-flop: hybrid with absolute strength
            wp = within_range_percentile(w, cur_strength)
            pcts[s] = wp if street == 0 else hybrid_strength(wp, cur_strength)

        options = self._options(to_call, pot, stack, max_to)
        hero_pct = CLASS_TOP_PCT[hand_class(*hole)]
        ip = h.hero_position_in_postflop()
        scenarios = []
        for opt in options:
            scenarios.append(self._scenario(opt, opps, models, pot, to_call, hero_pct, ip))

        samplers = [OpponentSampler(ranges[s], pcts[s], dead) for s in opps]
        sim = simulate(hole, board, samplers, [sc["sim"] for sc in scenarios],
                       time_budget=self.time_budget, max_iters=self.max_iters, seed=self.seed)

        # EVs
        results = []
        for opt, sc, r in zip(options, scenarios, sim["scenarios"]):
            R = sc["R"]
            add = sc["sim"]["add"]
            ev = r["p_fold"] * pot + R * r["avg_share_pot"] - r["p_called"] * add
            # facing a re-raise hero either folds (losing `add`) or calls
            vs_rr = None
            if r["p_reraise"] > 0:
                ev_rr_call = sc["R_rr"] * r["avg_rr_share_pot"] - r["p_reraise"] * sc["sim"]["rr_add"]
                ev_rr_fold = -r["p_reraise"] * add
                if ev_rr_call >= ev_rr_fold:
                    ev += ev_rr_call
                    vs_rr = "call"
                else:
                    ev += ev_rr_fold
                    vs_rr = "fold"
            if opt["action"] == "fold":
                ev = 0.0
                vs_rr = None
            results.append({
                "action": opt["action"],
                "label": opt["label"],
                "amount": opt.get("amount"),
                "to": opt.get("to"),
                "ev": round(ev, 3),
                "ev_bb": round(ev / bb, 2),
                "p_fold": round(r["p_fold"], 3) if opt["action"] in ("bet", "raise", "allin") else None,
                "equity_called": round(r["equity_called"], 3),
                "p_reraise": round(r["p_reraise"], 3),
                "equity_vs_reraise": round(r["equity_reraise"], 3),
                "vs_reraise": vs_rr,
                "realization": round(R, 2),
            })

        best = self._choose(results, pot)
        passive = next(r for r in results if r["action"] in ("check", "call"))
        passive_sc = sim["scenarios"][results.index(passive)]
        equity = passive_sc["equity_called"] if passive_sc["p_called"] > 0 else sim["equity_all"]

        chart = None
        if street == 0 and h.raises_this_street == 0:
            chart = self._preflop_chart(hole, opps, models, to_call, results)
            if chart["action"] != best["action"]:
                pick = next((r for r in results if r["action"] == chart["action"]), None)
                if pick is None and chart["action"] == "raise":
                    pick = next((r for r in results if r["action"] == "allin"), None)
                if pick is not None:
                    chart["overrode"] = best["label"]
                    best = pick

        # opponents info
        opp_info = []
        for k, s in enumerate(opps):
            pl = h.p[s]
            w = ranges[s]
            summ = self.t.summary(pl["pid"])
            acted = any(a["seat"] == s for a in h.actions)
            rp = range_size(w, hole + board) / range_size([1.0] * N_COMBOS, hole + board)
            opp_info.append({
                "seat": s,
                "name": pl["name"],
                "position": pl["position"],
                "hu_equity": round(sim["hu_equity"][k], 3),
                "acted": acted,
                "range_pct": round(rp, 3),
                "range_text": range_notation(w, 0.5) if street == 0 and rp < 0.55 else None,
                "top_classes": [[lab, round(v, 3)] for lab, v in top_classes(w, 6, hole + board)],
                "grid": range_grid(w, hole + board),
                "profile": summ["profile"],
                "note": summ["note"],
                "stats": summ["raw"],
                "model": summ["model"],
                "all_in": pl["all_in"],
            })

        draws = draw_info(hole, board) if board and len(board) < 5 else None
        info = {
            "status": "ok",
            "street": ["preflop", "flop", "turn", "river"][street],
            "hero_cards": pretty_cards(hole),
            "hand_class": hand_class(*hole),
            "hand_percentile": round(hero_pct, 3),
            "hand_desc": describe_hand(hole, board),
            "draws": describe_draws(draws) if draws else [],
            "outs": draws["outs"] if draws else 0,
            "pot": round(pot, 2),
            "to_call": round(to_call, 2),
            "pot_odds": round(to_call / (pot + to_call), 3) if to_call > 0 else 0.0,
            "spr": round(stack / pot, 2) if pot > 0 else None,
            "stack": round(stack, 2),
            "bb": bb,
            "in_position": ip,
            "equity": round(equity, 3),
            "equity_all": round(sim["equity_all"], 3),
            "equity_se": round(passive_sc["equity_se"], 4),
            "iterations": sim["iterations"],
            "options": results,
            "recommendation": best,
            "chart": chart,
            "opponents": opp_info,
        }
        info["confidence"], info["runner_up"] = self._confidence(results, best, pot)
        info["reasons"] = self._reasons(info, results, best, opps, models, chart)
        return info

    # ------------------------------------------------------------ options
    def _options(self, to_call, pot, stack, max_to):
        h = self.h
        bb = h.bb_amt
        cur = h.current_bet
        me = h.p[h.hero_seat]
        opts = []
        if to_call > 0:
            opts.append({"action": "fold", "label": "Fold"})
            if to_call >= stack - 1e-9:
                opts.append({"action": "call", "label": "Call %s (all-in)" % _fmt(stack), "amount": stack})
                return opts
            if h.street == 0 and h.raises_this_street == 0:
                verb = "Complete" if h.p[h.hero_seat]["position"] == "SB" else "Limp"
                opts.append({"action": "call", "label": "%s (call %s)" % (verb, _fmt(to_call)), "amount": to_call})
            else:
                opts.append({"action": "call", "label": "Call %s" % _fmt(to_call), "amount": to_call})
        else:
            opts.append({"action": "check", "label": "Check"})
        targets = []
        if h.street == 0:
            limpers = sum(1 for a in h.actions if a["street"] == 0 and a["action"] == "call"
                          and a["raises_before"] == 0)
            rb = h.raises_this_street
            if rb == 0:
                targets.append(("Raise to %s", bb * (2.5 if limpers == 0 else 3.5 + limpers)))
            elif rb == 1:
                callers = sum(1 for a in h.actions if a["street"] == 0 and a["action"] == "call"
                              and a["raises_before"] == 1)
                mult = 3.0 if h.hero_position_in_postflop() else 3.6
                targets.append(("3-bet to %s", cur * mult + cur * callers))
            elif rb == 2:
                targets.append(("4-bet to %s", cur * 2.3))
        else:
            if to_call == 0:
                for frac, name in ((0.33, "1/3"), (0.66, "2/3"), (1.0, "pot")):
                    targets.append(("Bet %%s (%s pot)" % name if name != "pot" else "Bet %s (pot)", pot * frac))
            else:
                targets.append(("Raise to %s", cur * 3.0))
        seen = set()
        for label, to in targets:
            to = round(max(to, h.current_bet + max(h.min_raise, bb)), 2)
            if to >= max_to * 0.85:   # close to all-in: just shove
                continue
            if to in seen:
                continue
            seen.add(to)
            act = "raise" if cur > 0 else "bet"
            opts.append({"action": act, "label": label % _fmt(to), "to": to,
                         "amount": round(to - me["street_bet"], 2)})
        if max_to > cur + 1e-9:
            # only offer a shove when stacks are shallow relative to the pot
            if h.street == 0:
                shove_ok = stack <= 25 * bb or h.raises_this_street >= 2 or stack <= 3 * (pot + to_call)
            else:
                shove_ok = stack <= 2.5 * (pot + to_call)
            if shove_ok or len(opts) <= 2:
                opts.append({"action": "allin", "label": "All-in (%s)" % _fmt(max_to), "to": round(max_to, 2),
                             "amount": round(stack, 2)})
        return opts

    def _scenario(self, opt, opps, models, pot, to_call, hero_pct, ip):
        """Continue / re-raise thresholds and chip amounts for one option.

        Pre-flop the thresholds are on the hand's percentile within the
        opponent's range; post-flop they are on absolute hand strength
        (made hand + draws), so an opponent whose range is already strong
        folds less often."""
        h = self.h
        hero = h.hero_seat
        me = h.p[hero]
        act = opt["action"]
        thr, thr_rr, calls, rr_calls = [], [], [], []
        street = h.street
        bb = h.bb_amt
        all_in_all = True
        hero_max_to = me["street_bet"] + me["stack"]
        rr_to_level = h.current_bet

        def rr_target(base_to):
            """Size of an opponent's re-raise over `base_to`."""
            if street == 0:
                # number of the raise the opponent would be making
                n_raise = h.raises_this_street + (2 if act in ("bet", "raise", "allin") else 1)
                if n_raise == 1:
                    return 4.0 * bb          # isolation raise over a limp
                if n_raise == 2:
                    return 3.2 * base_to     # 3-bet
                if n_raise == 3:
                    return 2.3 * base_to     # 4-bet
                return hero_max_to           # 5-bet = shove
            return 3.0 * max(base_to, bb)

        if act in ("fold", "check", "call"):
            add = 0.0 if act in ("fold", "check") else min(to_call, me["stack"])
            hero_allin = act == "call" and add >= me["stack"] - 1e-9
            rr_to = min(rr_target(max(h.current_bet, bb)), hero_max_to)
            for s in opps:
                pl = h.p[s]
                facing = h.current_bet - pl["street_bet"]
                no_rr = 2.0
                if pl["all_in"]:
                    thr.append(0.0); thr_rr.append(no_rr); calls.append(0.0); rr_calls.append(0.0)
                    continue
                all_in_all = False
                if facing <= 1e-9 and (pl["acted"] or h.current_bet > 0):
                    thr.append(0.0); thr_rr.append(no_rr); calls.append(0.0); rr_calls.append(0.0)
                    continue
                rr_share = 0.0
                if facing <= 1e-9:
                    # hasn't acted, nothing to call (e.g. checked to them)
                    if street == 0:
                        p = continue_preflop(h, s, models[s], 0)
                    else:
                        p = 1.0
                else:
                    if street == 0:
                        p = continue_preflop(h, s, models[s], h.raises_this_street)
                        rr_share = reraise_share_preflop(h, s, models[s], h.raises_this_street + 1, p)
                    else:
                        p = continue_postflop(models[s], facing / max(pot + add - facing, 1e-9), False)
                        rr_share = reraise_share_postflop(models[s])
                can_rr = (not hero_allin) and pl["stack"] > facing + 1e-9
                thr.append(1.0 - p)
                thr_rr.append(1.0 - p * rr_share if can_rr and rr_share > 0 else no_rr)
                calls.append(min(facing, pl["stack"]))
                rr_calls.append(min(rr_to - pl["street_bet"], pl["stack"]))
            final_allin = hero_allin
            rr_add = rr_to - me["street_bet"]
        else:
            to = opt["to"]
            add = round(to - me["street_bet"], 2)
            hero_allin = add >= me["stack"] - 1e-9
            new_level = h.raises_this_street + 1
            rr_to = min(rr_target(to), hero_max_to)
            for s in opps:
                pl = h.p[s]
                if pl["all_in"]:
                    thr.append(0.0); thr_rr.append(2.0); calls.append(0.0); rr_calls.append(0.0)
                    continue
                facing = to - pl["street_bet"]
                if street == 0:
                    std = {1: 2.5 * bb, 2: 3.3 * h.current_bet, 3: 2.3 * h.current_bet}.get(new_level, to)
                    ratio = max(1.0, to / max(std, 1e-9))
                    p = continue_preflop(h, s, models[s], new_level, size_ratio=ratio)
                    rr_share = reraise_share_preflop(h, s, models[s], new_level + 1, p)
                else:
                    is_raise = h.current_bet > 0
                    cbet = street == 1 and h.preflop_aggressor == hero and not is_raise
                    p = continue_postflop(models[s], facing / max(pot + add - facing, 1e-9), is_raise, cbet)
                    rr_share = reraise_share_postflop(models[s])
                can_rr = (not hero_allin) and pl["stack"] > facing + 1e-9
                thr.append(1.0 - p)
                thr_rr.append(1.0 - p * rr_share if can_rr else 2.0)
                calls.append(min(facing, pl["stack"]))
                rr_calls.append(min(rr_to - pl["street_bet"], pl["stack"]))
                if pl["stack"] > facing + 1e-9:
                    all_in_all = False
            final_allin = hero_allin or all_in_all
            rr_add = rr_to - me["street_bet"]
        # equity realisation
        R = self._realization(street, ip, hero_pct, len(opps))
        if final_allin:
            R = 1.0
        R_rr = 1.0 if rr_to >= hero_max_to - 1e-9 else R
        return {"R": R, "R_rr": R_rr,
                "sim": {"thr": thr, "thr_rr": thr_rr, "calls": calls, "rr_calls": rr_calls,
                        "add": add, "rr_add": rr_add, "pot": pot}}

    def _realization(self, street, ip, hero_pct, n_opp):
        if street == 3:
            return 1.0
        if street == 0:
            R = (0.95 if ip else 0.82) * (0.78 + 0.25 * (1.0 - hero_pct))
        elif street == 1:
            R = 0.95 if ip else 0.86
        else:
            R = 0.97 if ip else 0.9
        if n_opp > 1:
            R *= 0.96
        return min(R, 1.05)

    # ----------------------------------------------------------- choosing
    def _choose(self, results, pot):
        # prefer the simpler / lower-variance play when EVs are within a hair
        eps = max(0.02 * pot, 0.05 * self.h.bb_amt)
        best = max(results, key=lambda r: r["ev"])
        for r in results:
            if r["action"] in ("check", "call", "fold") and r["ev"] >= best["ev"] - eps and r is not best:
                if r["action"] == "fold" and best["ev"] > 0:
                    continue
                if best["action"] in ("bet", "raise", "allin"):
                    return r
        return best

    def _confidence(self, results, best, pot):
        others = sorted((r for r in results if r is not best), key=lambda r: -r["ev"])
        if not others:
            return "clear", None
        gap = best["ev"] - others[0]["ev"]
        scale = max(pot, self.h.bb_amt * 2)
        if gap < 0.04 * scale:
            conf = "close"
        elif gap < 0.12 * scale:
            conf = "moderate"
        else:
            conf = "clear"
        return conf, others[0]

    # ------------------------------------------------------- preflop chart
    def _preflop_chart(self, hole, opps, models, to_call, results):
        h = self.h
        hero = h.hero_seat
        pos = h.p[hero]["position"]
        hp = CLASS_TOP_PCT[hand_class(*hole)]
        behind = [s for s in opps if not h.p[s]["acted"]]
        nb = players_behind_preflop(h, hero)
        base = base_open_fraction(nb)
        if behind:
            tight = sum((1 - models[s]["vpip"]) / (1 - 0.28) for s in behind) / len(behind)
        else:
            tight = 1.0
        adj = _clamp(tight, 0.8, 1.3)
        limpers = sum(1 for a in h.actions if a["street"] == 0 and a["action"] == "call" and a["raises_before"] == 0)
        open_frac = _clamp(base * adj, 0.05, 0.75)
        label = hand_class(*hole)
        if to_call <= 0:   # big blind with limpers / completes
            value_frac = 0.12 if limpers <= 1 else 0.09
            action = "raise" if hp <= value_frac else "check"
            text = ("In the big blind facing %s, raise the top %s of hands for value and check the rest. "
                    "%s is in the top %s." % ("%d limper%s" % (limpers, "s" if limpers > 1 else "") if limpers else "a completed small blind",
                                             _pc(value_frac), label, _pc(hp)))
            return {"action": action, "range": value_frac, "hand_pct": hp, "text": text}
        if limpers:
            iso = open_frac * 0.75
            speculative = (label[0] == label[1]) or label.endswith("s")
            if hp <= iso:
                action = "raise"
            elif speculative and hp <= min(0.5, open_frac * 1.8):
                action = "call"
            else:
                action = "fold"
            text = ("%d limper%s in front of you: isolate with a raise using the top %s of hands; "
                    "over-limp small pairs / suited hands up to the top %s. %s is in the top %s."
                    % (limpers, "s" if limpers > 1 else "", _pc(iso), _pc(min(0.5, open_frac * 1.8)), label, _pc(hp)))
            return {"action": action, "range": iso, "hand_pct": hp, "text": text}
        action = "raise" if hp <= open_frac else "fold"
        extra = ""
        if adj > 1.03:
            extra = " (widened from %s because the players behind fold a lot)" % _pc(base)
        elif adj < 0.97:
            extra = " (tightened from %s because the players behind play many hands)" % _pc(base)
        text = ("Folded to you in the %s with %d player%s left to act: the standard open-raise range is the "
                "top %s of hands%s. %s is in the top %s."
                % (pos, nb, "s" if nb != 1 else "", _pc(open_frac), extra, label, _pc(hp)))
        return {"action": action, "range": open_frac, "hand_pct": hp, "text": text}

    # ------------------------------------------------------------ reasons
    def _reasons(self, info, results, best, opps, models, chart):
        h = self.h
        out = []
        bb = h.bb_amt
        if info["street"] == "preflop":
            out.append("%s (%s) is in the top %s of starting hands."
                       % (info["hero_cards"], info["hand_class"], _pc(info["hand_percentile"])))
        else:
            txt = "You have %s" % (info["hand_desc"][0].lower() + info["hand_desc"][1:])
            if info["draws"]:
                txt += " plus a %s" % " and ".join(info["draws"])
                if info["outs"]:
                    left = 2 if info["street"] == "flop" else 1
                    approx = min(0.99, info["outs"] * (4 if left == 2 else 2) / 100.0)
                    txt += " (~%d outs, about %s to hit by the river)" % (info["outs"], _pc(approx))
            out.append(txt + ".")
        if chart:
            out.append(chart["text"])
        # equity vs field
        names = []
        for o in info["opponents"]:
            names.append("%s %s" % (o["name"], _pc(o["hu_equity"])))
        if info["opponents"]:
            out.append("Estimated equity vs the players likely to stay in: %s (heads-up vs each: %s)."
                       % (_pc(info["equity"]), ", ".join(names)))
        to_call = info["to_call"]
        call = next((r for r in results if r["action"] == "call"), None)
        if to_call > 0 and call:
            need = info["pot_odds"]
            out.append("Pot odds: call %s to win %s, so you need %s equity. You have about %s, "
                       "so calling is worth %+.2f bb once future streets are accounted for."
                       % (_fmt(to_call), _fmt(info["pot"] + to_call), _pc(need), _pc(info["equity"]), call["ev_bb"]))
        aggr = [r for r in results if r["action"] in ("bet", "raise", "allin")]
        if best["action"] in ("bet", "raise", "allin"):
            txt = ("%s: everyone folds about %s of the time; when called you have %s equity"
                   % (best["label"], _pc(best["p_fold"] or 0), _pc(best["equity_called"])))
            if best.get("p_reraise"):
                txt += "; they re-raise ~%s of the time and you should then %s" % (
                    _pc(best["p_reraise"]), best["vs_reraise"] or "fold")
            out.append(txt + ". EV %+.2f bb." % best["ev_bb"])
        elif aggr:
            top = max(aggr, key=lambda r: r["ev"])
            out.append("Best aggressive option (%s): fold-out ~%s, equity when called %s, EV %+.2f bb."
                       % (top["label"], _pc(top["p_fold"] or 0), _pc(top["equity_called"]), top["ev_bb"]))
        for o in info["opponents"]:
            prof = o["profile"]
            if prof["key"] != "unknown" and o["note"]:
                out.append("%s looks like a %s (%d hands, %s confidence): %s"
                           % (o["name"], prof["label"].lower(), prof["hands"], prof["confidence"], o["note"]))
        if info["confidence"] == "close" and info["runner_up"]:
            out.append("Close decision: %s is almost as good (EV %+.2f bb)."
                       % (info["runner_up"]["label"], info["runner_up"]["ev_bb"]))
        if chart and chart.get("overrode"):
            out.append("The simulation slightly preferred '%s'; the position-based chart is used for unopened pots "
                       "because it accounts for post-flop play better." % chart["overrode"])
        R = best.get("realization", 1.0)
        if info["street"] != "river" and R < 1.0:
            out.append("EVs include one possible re-raise and assume you realise ~%s of your equity "
                       "(%s, more streets to come)." % (_pc(R), "in position" if info["in_position"] else "out of position"))
        return out


def advise(hand, tracker, **kw):
    return Advisor(hand, tracker, **kw).advise()
