"""Fast pure-Python 5/6/7-card poker hand evaluator.

Scores are integers: higher is better.  score >> 20 is the hand category:
0 high card, 1 pair, 2 two pair, 3 trips, 4 straight, 5 flush,
6 full house, 7 quads, 8 straight flush.  The low 20 bits hold up to five
tie-break ranks as 4-bit nibbles.

The hot path works on three per-card keys that can be summed incrementally,
which lets the Monte Carlo engine evaluate many players sharing one board
with only a couple of additions and a dict lookup each:

    RK[c]  rank-count key   (3 bits per rank)
    SK[c]  suit-count key   (4 bits per suit, start from SUIT_START)
    FM[c]  suit-mask key    (16 bits per suit)
"""

from .cards import RANK_NAMES, RANK_PLURALS

HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS, STRAIGHT_FLUSH = range(9)
CATEGORY_NAMES = ["High card", "Pair", "Two pair", "Three of a kind", "Straight",
                  "Flush", "Full house", "Four of a kind", "Straight flush"]

RK = [1 << (3 * (c >> 2)) for c in range(52)]
SK = [1 << (4 * (c & 3)) for c in range(52)]
FM = [1 << ((c >> 2) + 16 * (c & 3)) for c in range(52)]
SUIT_START = 0x3333          # each nibble reaches 8 when that suit has >= 5 cards
FLUSH_BITS = 0x8888


def _straight_high(mask):
    """Highest straight rank in a 13-bit rank mask, or -1."""
    for hi in range(12, 3, -1):
        need = 0x1F << (hi - 4)
        if mask & need == need:
            return hi
    if mask & 0x100F == 0x100F:  # A-2-3-4-5 wheel
        return 3
    return -1


def _top_bits(mask, n):
    out = []
    r = 12
    while r >= 0 and len(out) < n:
        if mask >> r & 1:
            out.append(r)
        r -= 1
    return out


def _encode(cat, ranks):
    v = cat
    for i in range(5):
        v = (v << 4) | (ranks[i] if i < len(ranks) else 0)
    return v


STRAIGHT_HIGH = [_straight_high(m) for m in range(1 << 13)]

# Score for a 13-bit mask of cards in the flush suit (>= 5 bits set).
FLUSH_SCORE = [0] * (1 << 13)
for _m in range(1 << 13):
    if bin(_m).count("1") >= 5:
        _sh = STRAIGHT_HIGH[_m]
        if _sh >= 0:
            FLUSH_SCORE[_m] = _encode(STRAIGHT_FLUSH, [_sh])
        else:
            FLUSH_SCORE[_m] = _encode(FLUSH, _top_bits(_m, 5))


def _nonflush_score(k):
    counts = [(k >> (3 * r)) & 7 for r in range(13)]
    mask = 0
    quads, trips, pairs, singles = [], [], [], []
    for r in range(12, -1, -1):
        n = counts[r]
        if n:
            mask |= 1 << r
            if n == 4:
                quads.append(r)
            elif n == 3:
                trips.append(r)
            elif n == 2:
                pairs.append(r)
            else:
                singles.append(r)
    if quads:
        q = quads[0]
        kick = [r for r in range(12, -1, -1) if counts[r] and r != q][:1]
        return _encode(QUADS, [q] + kick)
    if trips and (len(trips) > 1 or pairs):
        t = trips[0]
        rest = trips[1:] + pairs
        p = max(rest)
        return _encode(FULL_HOUSE, [t, p])
    sh = STRAIGHT_HIGH[mask]
    if sh >= 0:
        return _encode(STRAIGHT, [sh])
    if trips:
        t = trips[0]
        kick = [r for r in range(12, -1, -1) if counts[r] and r != t][:2]
        return _encode(TRIPS, [t] + kick)
    if len(pairs) >= 2:
        p1, p2 = pairs[0], pairs[1]
        kick = [r for r in range(12, -1, -1) if counts[r] and r != p1 and r != p2][:1]
        return _encode(TWO_PAIR, [p1, p2] + kick)
    if pairs:
        p = pairs[0]
        kick = [r for r in range(12, -1, -1) if counts[r] and r != p][:3]
        return _encode(PAIR, [p] + kick)
    return _encode(HIGH_CARD, singles[:5])


NONFLUSH = {}  # rank-count key -> score (filled lazily)


def score_keys(k, s, fm):
    """Score from accumulated keys (see module doc)."""
    if s & FLUSH_BITS:
        if s & 0x8:
            return FLUSH_SCORE[fm & 0x1FFF]
        if s & 0x80:
            return FLUSH_SCORE[(fm >> 16) & 0x1FFF]
        if s & 0x800:
            return FLUSH_SCORE[(fm >> 32) & 0x1FFF]
        return FLUSH_SCORE[(fm >> 48) & 0x1FFF]
    v = NONFLUSH.get(k)
    if v is None:
        v = NONFLUSH[k] = _nonflush_score(k)
    return v


def evaluate(cards):
    """Score the best 5-card hand from 5, 6 or 7 cards."""
    k = 0
    s = SUIT_START
    fm = 0
    for c in cards:
        k += RK[c]
        s += SK[c]
        fm |= FM[c]
    return score_keys(k, s, fm)


def category(score):
    return score >> 20


def score_ranks(score):
    return [(score >> (16 - 4 * i)) & 0xF for i in range(5)]


def describe_score(score):
    """Human readable description, e.g. 'Full house, Kings full of Sevens'."""
    cat = score >> 20
    r = score_ranks(score)
    if cat == STRAIGHT_FLUSH:
        return "Royal flush" if r[0] == 12 else "Straight flush, %s high" % RANK_NAMES[r[0]]
    if cat == QUADS:
        return "Four of a kind, %s" % RANK_PLURALS[r[0]]
    if cat == FULL_HOUSE:
        return "Full house, %s full of %s" % (RANK_PLURALS[r[0]], RANK_PLURALS[r[1]])
    if cat == FLUSH:
        return "Flush, %s high" % RANK_NAMES[r[0]]
    if cat == STRAIGHT:
        return "Straight, %s high" % RANK_NAMES[r[0]]
    if cat == TRIPS:
        return "Three of a kind, %s" % RANK_PLURALS[r[0]]
    if cat == TWO_PAIR:
        return "Two pair, %s and %s" % (RANK_PLURALS[r[0]], RANK_PLURALS[r[1]])
    if cat == PAIR:
        return "Pair of %s" % RANK_PLURALS[r[0]]
    return "%s high" % RANK_NAMES[r[0]]
