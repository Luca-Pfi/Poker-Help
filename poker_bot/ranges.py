"""Hand ranges: preflop strength ordering, post-flop hand strength, and
helpers to build / narrow weighted ranges over the 1326 two-card combos.

A range is a list of 1326 floats (weights in [0, 1]) aligned with
cards.ALL_COMBOS.  "Strength" values are in [0, 1] with 1 = strongest.
"""

import math

from .cards import (ALL_COMBOS, COMBO_CLASS, ALL_CLASSES, RANKS,
                    combos_for_class, class_grid_pos)
from .evaluator import evaluate, FLUSH, STRAIGHT, STRAIGHT_HIGH
from .preflop_data import HAND_ORDER

N_COMBOS = len(ALL_COMBOS)

# ---------------------------------------------------------------- preflop
CLASS_STRENGTH = {}       # class label -> preflop strength (1 = AA)
CLASS_TOP_PCT = {}        # class label -> top-x% at which the class enters a range
_cum = 0
for _label in HAND_ORDER:
    _n = len(combos_for_class(_label))
    CLASS_STRENGTH[_label] = 1.0 - (_cum + _n / 2.0) / N_COMBOS
    _cum += _n
    CLASS_TOP_PCT[_label] = _cum / N_COMBOS

PREFLOP_STRENGTH = [CLASS_STRENGTH[c] for c in COMBO_CLASS]
COMBO_SUITED = [(a & 3) == (b & 3) for (a, b) in ALL_COMBOS]


def preflop_percentile(c1, c2):
    """Fraction of starting hands that are at least as strong (0.004 = AA)."""
    from .cards import hand_class
    return CLASS_TOP_PCT[hand_class(c1, c2)]


def _sigmoid(x):
    if x > 30:
        return 1.0
    if x < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def top_range(fraction, softness=0.03):
    """Weights for (roughly) the top `fraction` of starting hands."""
    fraction = max(0.0, min(1.0, fraction))
    if fraction >= 0.999:
        return [1.0] * N_COMBOS
    cut = 1.0 - fraction
    return [_sigmoid((s - cut) / softness) for s in PREFLOP_STRENGTH]


# Standard open-raise frequency by number of players still to act behind.
# (roughly a solid, slightly tight 100bb cash-game strategy)
BASE_OPEN = {0: 0.0, 1: 0.42, 2: 0.45, 3: 0.28, 4: 0.22, 5: 0.18, 6: 0.155, 7: 0.135, 8: 0.12, 9: 0.11}


def base_open_fraction(players_behind, n_players=6):
    """Standard open-raise frequency.  Short-handed tables open much wider:
    heads-up the button is in position and should raise most hands."""
    if n_players == 2 and players_behind == 1:
        return 0.80
    if n_players == 3:
        if players_behind == 2:
            return 0.50
        if players_behind == 1:
            return 0.45
    return BASE_OPEN.get(players_behind, 0.10)


# VPIP / PFR of a solid winning player by number of players dealt in.
EXPECTED_VPIP_PFR = {2: (0.62, 0.42), 3: (0.42, 0.30), 4: (0.34, 0.24), 5: (0.29, 0.20), 6: (0.26, 0.18),
                     7: (0.24, 0.16), 8: (0.22, 0.15), 9: (0.21, 0.14), 10: (0.20, 0.13)}


def expected_vpip_pfr(n_players):
    return EXPECTED_VPIP_PFR[max(2, min(10, int(n_players or 6)))]


# --------------------------------------------------------------- postflop
_STRENGTH_CACHE = {}

# bonus = share of the gap to 1.0 that a draw adds to the effective strength
DRAW_BONUS = {
    3: {"combo": 0.55, "fd": 0.40, "oesd": 0.32, "gut": 0.14, "bdfd": 0.03},
    4: {"combo": 0.40, "fd": 0.28, "oesd": 0.24, "gut": 0.10, "bdfd": 0.0},
}


def straight_outs(mask):
    """Number of ranks that would complete a straight for this rank mask."""
    n = 0
    for r in range(13):
        if not mask >> r & 1 and STRAIGHT_HIGH[mask | (1 << r)] >= 0:
            n += 1
    return n


def draw_info(hole, board):
    """Detect draws for a hand. Returns dict with flags and an outs estimate."""
    info = {"fd": False, "oesd": False, "gut": False, "bdfd": False, "outs": 0}
    if len(board) >= 5:
        return info
    cards = list(hole) + list(board)
    score = evaluate(cards) if len(cards) >= 5 else 0
    cat = score >> 20
    if cat < FLUSH:
        for s in range(4):
            n = sum(1 for c in cards if c & 3 == s)
            in_hole = sum(1 for c in hole if c & 3 == s)
            if n == 4 and in_hole:
                info["fd"] = True
            elif n == 3 and in_hole == 2 and len(board) == 3:
                info["bdfd"] = True
    if cat < STRAIGHT:
        mask = 0
        bmask = 0
        for c in cards:
            mask |= 1 << (c >> 2)
        for c in board:
            bmask |= 1 << (c >> 2)
        outs = straight_outs(mask)
        board_outs = straight_outs(bmask) if len(board) >= 3 else 0
        if outs > board_outs:
            if outs >= 2:
                info["oesd"] = True
            elif outs == 1:
                info["gut"] = True
    outs = 0
    if info["fd"]:
        outs += 9
    if info["oesd"]:
        outs += 8 if not info["fd"] else 6
    elif info["gut"]:
        outs += 4 if not info["fd"] else 3
    info["outs"] = outs
    return info


def board_strengths(board):
    """Effective strength (0..1) of every combo on this board; -1 if blocked.

    Made-hand strength is the percentile of the hand among all possible
    holdings; on the flop/turn draws add a bonus toward 1.0.
    """
    key = tuple(sorted(board))
    hit = _STRENGTH_CACHE.get(key)
    if hit is not None:
        return hit
    if not board:
        return PREFLOP_STRENGTH
    dead = 0
    for c in board:
        dead |= 1 << c
    scores = []
    valid = []
    for i, (a, b) in enumerate(ALL_COMBOS):
        if (dead >> a) & 1 or (dead >> b) & 1:
            continue
        scores.append(evaluate(list(board) + [a, b]))
        valid.append(i)
    # percentile with ties averaged
    order = sorted(range(len(scores)), key=lambda j: scores[j])
    pct = [0.0] * len(scores)
    n = len(scores)
    j = 0
    while j < n:
        k = j
        while k + 1 < n and scores[order[k + 1]] == scores[order[j]]:
            k += 1
        p = ((j + k) / 2.0 + 0.5) / n
        for t in range(j, k + 1):
            pct[order[t]] = p
        j = k + 1
    out = [-1.0] * N_COMBOS
    bonus_table = DRAW_BONUS.get(len(board))
    for j, idx in enumerate(valid):
        s = pct[j]
        if bonus_table:
            d = draw_info(ALL_COMBOS[idx], board)
            if d["fd"] and (d["oesd"] or d["gut"]):
                bonus = bonus_table["combo"]
            elif d["fd"]:
                bonus = bonus_table["fd"]
            elif d["oesd"]:
                bonus = bonus_table["oesd"]
            elif d["gut"]:
                bonus = bonus_table["gut"]
            elif d["bdfd"]:
                bonus = bonus_table["bdfd"]
            else:
                bonus = 0.0
            s = s + (1.0 - s) * bonus
        out[idx] = s
    if len(_STRENGTH_CACHE) > 64:
        _STRENGTH_CACHE.clear()
    _STRENGTH_CACHE[key] = out
    return out


def within_range_percentile(weights, strengths):
    """For each combo: share of the (weighted) range it beats in strength.
    1.0 = top of the range.  Blocked / zero-weight combos still get a value
    based on where they would sit."""
    items = [(strengths[i], weights[i], i) for i in range(N_COMBOS) if strengths[i] >= 0]
    items.sort()
    total = sum(w for _, w, _ in items)
    out = [0.0] * N_COMBOS
    if total <= 0:
        return out
    acc = 0.0
    j = 0
    n = len(items)
    while j < n:
        k = j
        group_w = items[j][1]
        while k + 1 < n and items[k + 1][0] == items[j][0]:
            k += 1
            group_w += items[k][1]
        p = (acc + group_w / 2.0) / total
        for t in range(j, k + 1):
            out[items[t][2]] = p
        acc += group_w
        j = k + 1
    return out


def narrow(weights, pct, keep_top, floor=0.0, softness=0.08, top_cut=None, top_weight=1.0):
    """Keep roughly the strongest `keep_top` share of a range.

    floor:      residual weight kept for hands below the cutoff (bluffs / mistakes)
    top_cut:    if set, hands in the top `top_cut` share are multiplied by top_weight
                (e.g. a call is less likely with the nuts because they would raise)
    """
    keep_top = max(0.01, min(1.0, keep_top))
    cut = 1.0 - keep_top
    out = [0.0] * N_COMBOS
    for i in range(N_COMBOS):
        w = weights[i]
        if w <= 0:
            continue
        p = pct[i]
        f = floor + (1.0 - floor) * _sigmoid((p - cut) / softness) if keep_top < 1.0 else 1.0
        if top_cut is not None and p > 1.0 - top_cut:
            f *= top_weight
        out[i] = w * f
    return out


def downweight_top(weights, pct, top_share, factor, softness=0.05):
    """Reduce the weight of the strongest `top_share` of a range (used after a check)."""
    cut = 1.0 - top_share
    out = [0.0] * N_COMBOS
    for i in range(N_COMBOS):
        w = weights[i]
        if w <= 0:
            continue
        s = _sigmoid((pct[i] - cut) / softness)
        out[i] = w * (1.0 - (1.0 - factor) * s)
    return out


# ---------------------------------------------------------------- display
def range_grid(weights, dead_cards=()):
    """13x13 grid of the average weight of each starting-hand class (0..1),
    normalised so the most likely class is 1.0."""
    dead = set(dead_cards)
    sums = {}
    counts = {}
    for i, (a, b) in enumerate(ALL_COMBOS):
        if a in dead or b in dead:
            continue
        lab = COMBO_CLASS[i]
        sums[lab] = sums.get(lab, 0.0) + weights[i]
        counts[lab] = counts.get(lab, 0) + 1
    grid = [[0.0] * 13 for _ in range(13)]
    mx = 0.0
    for lab in ALL_CLASSES:
        if counts.get(lab):
            v = sums[lab] / counts[lab]
            r, c = class_grid_pos(lab)
            grid[r][c] = v
            mx = max(mx, v)
    if mx > 0:
        grid = [[round(v / mx, 3) for v in row] for row in grid]
    return grid


def range_size(weights, dead_cards=()):
    dead = set(dead_cards)
    tot = 0.0
    for i, (a, b) in enumerate(ALL_COMBOS):
        if a in dead or b in dead:
            continue
        tot += weights[i]
    return tot


def top_classes(weights, n=8, dead_cards=()):
    """Most likely hand classes (by total weight) in a range."""
    dead = set(dead_cards)
    sums = {}
    for i, (a, b) in enumerate(ALL_COMBOS):
        if a in dead or b in dead:
            continue
        lab = COMBO_CLASS[i]
        sums[lab] = sums.get(lab, 0.0) + weights[i]
    total = sum(sums.values()) or 1.0
    best = sorted(sums.items(), key=lambda kv: -kv[1])[:n]
    return [(lab, v / total) for lab, v in best if v > 0]


CLASS_INDICES = {}
for _i, _cl in enumerate(COMBO_CLASS):
    CLASS_INDICES.setdefault(_cl, []).append(_i)


def range_notation(weights, threshold=0.5):
    """Compact text like 'TT+, AJs+, KQs, AQo+' for classes with weight >= threshold."""
    avg = {}
    for lab in ALL_CLASSES:
        idx = CLASS_INDICES[lab]
        avg[lab] = sum(weights[i] for i in idx) / len(idx)
    inc = {lab for lab, v in avg.items() if v >= threshold}
    parts = []
    # pairs
    pairs = [RANKS[r] * 2 for r in range(12, -1, -1)]
    parts.extend(_runs([p for p in pairs], inc, plus_label=lambda run: run[-1] + "+" if run[0] == "AA" else None))
    for suffix in ("s", "o"):
        for hi in range(12, 0, -1):
            labels = [RANKS[hi] + RANKS[lo] + suffix for lo in range(hi - 1, -1, -1)]
            parts.extend(_runs(labels, inc,
                               plus_label=lambda run, h=hi: run[-1] + "+" if run[0][1] == RANKS[h - 1] and len(run) > 1 else None))
    return ", ".join(parts)


def _runs(labels, inc, plus_label):
    out = []
    run = []
    for lab in labels + [None]:
        if lab is not None and lab in inc:
            run.append(lab)
        else:
            if run:
                p = plus_label(run)
                if p:
                    out.append(p)
                elif len(run) == 1:
                    out.append(run[0])
                else:
                    out.append(run[0] + "-" + run[-1])
            run = []
    return out


__all__ = [
    "PREFLOP_STRENGTH", "CLASS_STRENGTH", "CLASS_TOP_PCT", "top_range", "base_open_fraction", "expected_vpip_pfr", "COMBO_SUITED",
    "board_strengths", "within_range_percentile", "narrow", "downweight_top", "draw_info",
    "range_grid", "range_size", "top_classes", "range_notation", "preflop_percentile",
]
