"""Monte Carlo engine.

Opponents' hole cards are sampled from weighted ranges.  Each opponent's
sampled hand has a "strength percentile within their range" for the
current street; in a betting scenario an opponent continues when that
percentile is above their fold threshold (i.e. they fold the weakest part
of their range).  One simulation therefore yields, for every scenario at
once: the fold-out probability, the equity against the hands that continue,
the pot size when called, and per-opponent heads-up equity.

Scenarios can also give opponents a re-raise threshold: the strongest part
of the continuing hands re-raises instead of calling.  Hero's equity against
those re-raising hands is tracked separately so the advisor can decide
whether hero would call or fold to the re-raise.
"""

import random
import time
from bisect import bisect_right

from .cards import ALL_COMBOS
from .evaluator import RK, SK, FM, SUIT_START, score_keys


class OpponentSampler:
    def __init__(self, weights, pct, dead_mask):
        """pct: per-combo decision strength (compared against scenario thresholds)."""
        combos, cum, pcts = [], [], []
        acc = 0.0
        for i, w in enumerate(weights):
            if w <= 1e-6:
                continue
            a, b = ALL_COMBOS[i]
            if (dead_mask >> a) & 1 or (dead_mask >> b) & 1:
                continue
            acc += w
            combos.append((a, b))
            cum.append(acc)
            pcts.append(pct[i])
        if not combos:  # degenerate range: fall back to any live combo
            for i, (a, b) in enumerate(ALL_COMBOS):
                if not ((dead_mask >> a) & 1 or (dead_mask >> b) & 1):
                    acc += 1.0
                    combos.append((a, b))
                    cum.append(acc)
                    pcts.append(0.5)
        self.combos = combos
        self.cum = cum
        self.pcts = pcts
        self.total = acc


def simulate(hero_cards, board, samplers, scenarios, time_budget=1.0, max_iters=50000,
             min_iters=600, seed=None):
    """Run the simulation.

    scenarios: list of dicts with
        thr:      per-opponent continue threshold on the decision strength
                  (0 = always continue, > 1 = always fold)
        thr_rr:   per-opponent re-raise threshold (> 1 = never re-raises)
        add:      hero's additional chips put in
        calls:    per-opponent chips added if that opponent continues
        rr_add:   hero's chips added in total if hero calls a re-raise
        rr_calls: per-opponent chips added when that opponent re-raises
        pot:      pot before hero acts
    Returns dict with per-scenario accumulators and per-opponent HU equity.
    """
    rng = random.Random(seed)
    rnd = rng.random
    h1, h2 = hero_cards
    dead = 0
    for c in list(hero_cards) + list(board):
        dead |= 1 << c
    live_deck = [c for c in range(52) if not (dead >> c) & 1]
    nlive = len(live_deck)
    need = 5 - len(board)
    bk0 = sum(RK[c] for c in board)
    bs0 = SUIT_START + sum(SK[c] for c in board)
    bf0 = 0
    for c in board:
        bf0 |= FM[c]
    hk = RK[h1] + RK[h2]
    hs = SK[h1] + SK[h2]
    hf = FM[h1] | FM[h2]

    nopp = len(samplers)
    nsc = len(scenarios)
    thr = [sc["thr"] for sc in scenarios]
    thr_rr = [sc.get("thr_rr") or [2.0] * nopp for sc in scenarios]
    calls = [sc["calls"] for sc in scenarios]
    rr_calls = [sc.get("rr_calls") or [0.0] * nopp for sc in scenarios]
    sc_n_fold = [0] * nsc
    sc_n_called = [0] * nsc
    sc_share = [0.0] * nsc
    sc_share_pot = [0.0] * nsc
    sc_share_sq = [0.0] * nsc
    sc_n_rr = [0] * nsc
    sc_rr_share = [0.0] * nsc
    sc_rr_share_pot = [0.0] * nsc
    sc_pot0 = [sc["pot"] + sc["add"] for sc in scenarios]
    sc_rr_pot0 = [sc["pot"] + sc.get("rr_add", sc["add"]) for sc in scenarios]
    hu = [0.0] * nopp
    all_share = 0.0

    samp = [(s.combos, s.cum, s.total, s.pcts) for s in samplers]
    t_end = time.perf_counter() + time_budget
    iters = 0
    rejected = 0
    opp_score = [0] * nopp
    opp_pct = [0.0] * nopp
    while iters < max_iters:
        if iters >= min_iters and (iters & 127) == 0 and time.perf_counter() > t_end:
            break
        used = dead
        ok = True
        for j in range(nopp):
            combos, cum, total, pcts = samp[j]
            for _try in range(30):
                idx = bisect_right(cum, rnd() * total)
                if idx >= len(combos):
                    idx = len(combos) - 1
                a, b = combos[idx]
                if not ((used >> a) & 1 or (used >> b) & 1):
                    break
            else:
                ok = False
                break
            used |= (1 << a) | (1 << b)
            opp_pct[j] = pcts[idx]
            opp_score[j] = (a, b)
        if not ok:
            rejected += 1
            if rejected > max_iters * 4:
                break
            continue
        bk, bs, bf = bk0, bs0, bf0
        got = 0
        while got < need:
            c = live_deck[int(rnd() * nlive)]
            if (used >> c) & 1:
                continue
            used |= 1 << c
            bk += RK[c]
            bs += SK[c]
            bf |= FM[c]
            got += 1
        hero = score_keys(bk + hk, bs + hs, bf | hf)
        best_all = 0
        ties_all = 0
        for j in range(nopp):
            a, b = opp_score[j]
            v = score_keys(bk + RK[a] + RK[b], bs + SK[a] + SK[b], bf | FM[a] | FM[b])
            opp_score[j] = v
            if hero > v:
                hu[j] += 1.0
            elif hero == v:
                hu[j] += 0.5
            if v > best_all:
                best_all, ties_all = v, 1
            elif v == best_all:
                ties_all += 1
        if hero > best_all:
            all_share += 1.0
        elif hero == best_all:
            all_share += 1.0 / (ties_all + 1)

        for s in range(nsc):
            th = thr[s]
            trr = thr_rr[s]
            best = -1
            ties = 0
            extra = 0.0
            cl = calls[s]
            rr_best = -1
            rr_ties = 0
            rr_extra = 0.0
            for j in range(nopp):
                pj = opp_pct[j]
                if pj >= th[j]:
                    v = opp_score[j]
                    extra += cl[j]
                    if v > best:
                        best, ties = v, 1
                    elif v == best:
                        ties += 1
                    if pj >= trr[j]:
                        rr_extra += rr_calls[s][j]
                        if v > rr_best:
                            rr_best, rr_ties = v, 1
                        elif v == rr_best:
                            rr_ties += 1
            if best < 0:
                sc_n_fold[s] += 1
                continue
            if rr_best >= 0:
                if hero > rr_best:
                    share = 1.0
                elif hero == rr_best:
                    share = 1.0 / (rr_ties + 1)
                else:
                    share = 0.0
                sc_n_rr[s] += 1
                sc_rr_share[s] += share
                sc_rr_share_pot[s] += share * (sc_rr_pot0[s] + rr_extra)
                continue
            if hero > best:
                share = 1.0
            elif hero == best:
                share = 1.0 / (ties + 1)
            else:
                share = 0.0
            sc_n_called[s] += 1
            sc_share[s] += share
            sc_share_pot[s] += share * (sc_pot0[s] + extra)
            sc_share_sq[s] += share * share
        iters += 1

    n = max(iters, 1)
    out_sc = []
    for s in range(nsc):
        nc = sc_n_called[s]
        eq = sc_share[s] / nc if nc else 0.0
        var = (sc_share_sq[s] / nc - eq * eq) if nc else 0.0
        nrr = sc_n_rr[s]
        out_sc.append({
            "p_fold": sc_n_fold[s] / n,
            "p_called": nc / n,
            "p_reraise": nrr / n,
            "equity_called": eq,
            "equity_reraise": sc_rr_share[s] / nrr if nrr else 0.0,
            "equity_se": (max(var, 0.0) / nc) ** 0.5 if nc else 0.0,
            "avg_share_pot": sc_share_pot[s] / n,        # E[share * final pot ; called, no re-raise]
            "avg_rr_share_pot": sc_rr_share_pot[s] / n,  # E[share * pot ; re-raised and hero calls]
        })
    return {
        "iterations": iters,
        "scenarios": out_sc,
        "hu_equity": [h / n for h in hu],
        "equity_all": all_share / n,
    }
