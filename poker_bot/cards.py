"""Card representation and parsing.

A card is an int 0..51:  rank = card >> 2  (0 = deuce ... 12 = ace)
                         suit = card & 3   (0 = clubs, 1 = diamonds, 2 = hearts, 3 = spades)
"""

RANKS = "23456789TJQKA"
SUITS = "cdhs"
SUIT_SYMBOLS = {"c": "♣", "d": "♦", "h": "♥", "s": "♠"}
RANK_NAMES = ["Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
              "Nine", "Ten", "Jack", "Queen", "King", "Ace"]
RANK_PLURALS = ["Twos", "Threes", "Fours", "Fives", "Sixes", "Sevens", "Eights",
                "Nines", "Tens", "Jacks", "Queens", "Kings", "Aces"]

FULL_DECK = list(range(52))


def make_card(rank, suit):
    return (rank << 2) | suit


def rank_of(card):
    return card >> 2


def suit_of(card):
    return card & 3


def parse_card(text):
    """Parse 'Ah', 'td', '10s', 'A♠' ... into a card int."""
    t = text.strip()
    if not t:
        raise ValueError("empty card")
    for sym_key, sym in SUIT_SYMBOLS.items():
        t = t.replace(sym, sym_key)
    t = t.replace("♤", "s").replace("♡", "h").replace("♢", "d").replace("♧", "c")
    if t[:2] == "10":
        t = "T" + t[2:]
    if len(t) != 2:
        raise ValueError("bad card: %r" % text)
    r = RANKS.find(t[0].upper())
    s = SUITS.find(t[1].lower())
    if r < 0 or s < 0:
        raise ValueError("bad card: %r" % text)
    return make_card(r, s)


def parse_cards(text):
    """Parse a string like 'AhKd', 'Ah Kd', 'ah,kd,10c' into a list of cards."""
    if isinstance(text, (list, tuple)):
        out = []
        for item in text:
            out.extend(parse_cards(item) if isinstance(item, str) else [int(item)])
        return out
    t = text.replace(",", " ").replace("10", "T")
    for sym_key, sym in SUIT_SYMBOLS.items():
        t = t.replace(sym, sym_key)
    t = "".join(t.split())
    if len(t) % 2:
        raise ValueError("bad card string: %r" % text)
    cards = [parse_card(t[i:i + 2]) for i in range(0, len(t), 2)]
    if len(set(cards)) != len(cards):
        raise ValueError("duplicate card in %r" % text)
    return cards


def card_str(card):
    return RANKS[card >> 2] + SUITS[card & 3]


def cards_str(cards, sep=""):
    return sep.join(card_str(c) for c in cards)


def pretty_card(card):
    return RANKS[card >> 2] + SUIT_SYMBOLS[SUITS[card & 3]]


def pretty_cards(cards, sep=" "):
    return sep.join(pretty_card(c) for c in cards)


def hand_class(c1, c2):
    """Return the 169-class label of a 2-card hand, e.g. 'AKs', 'T9o', 'QQ'."""
    r1, r2 = c1 >> 2, c2 >> 2
    if r1 < r2:
        r1, r2 = r2, r1
    if r1 == r2:
        return RANKS[r1] * 2
    suited = (c1 & 3) == (c2 & 3)
    return RANKS[r1] + RANKS[r2] + ("s" if suited else "o")


def class_grid_pos(label):
    """Row/col position of a 169-class label in the standard 13x13 grid
    (row/col 0 = Ace). Suited hands are above the diagonal."""
    r1 = 12 - RANKS.index(label[0])
    r2 = 12 - RANKS.index(label[1])
    if len(label) == 2:
        return r1, r1
    if label[2] == "s":
        return min(r1, r2), max(r1, r2)
    return max(r1, r2), min(r1, r2)


def grid_label(row, col):
    hi = RANKS[12 - min(row, col)]
    lo = RANKS[12 - max(row, col)]
    if row == col:
        return hi + lo
    return hi + lo + ("s" if row < col else "o")


ALL_COMBOS = [(a, b) for a in range(52) for b in range(a + 1, 52)]  # 1326
COMBO_INDEX = {c: i for i, c in enumerate(ALL_COMBOS)}


def combos_for_class(label):
    """All (c1, c2) combos (c1 < c2) belonging to a 169-class label."""
    r1 = RANKS.index(label[0])
    r2 = RANKS.index(label[1])
    out = []
    if len(label) == 2:
        for s1 in range(4):
            for s2 in range(s1 + 1, 4):
                a, b = make_card(r1, s1), make_card(r2, s2)
                out.append((min(a, b), max(a, b)))
    elif label[2] == "s":
        for s in range(4):
            a, b = make_card(r1, s), make_card(r2, s)
            out.append((min(a, b), max(a, b)))
    else:
        for s1 in range(4):
            for s2 in range(4):
                if s1 != s2:
                    a, b = make_card(r1, s1), make_card(r2, s2)
                    out.append((min(a, b), max(a, b)))
    return out


ALL_CLASSES = [grid_label(r, c) for r in range(13) for c in range(13)]
COMBO_CLASS = [hand_class(a, b) for (a, b) in ALL_COMBOS]
