import itertools
import random
from collections import Counter

from poker_bot.cards import parse_cards
from poker_bot.evaluator import evaluate, describe_score, category, STRAIGHT_FLUSH, FULL_HOUSE, FLUSH, STRAIGHT


def ref_eval5(cards):
    """Independent, slow reference evaluator for exactly 5 cards."""
    ranks = sorted((c >> 2 for c in cards), reverse=True)
    suits = [c & 3 for c in cards]
    flush = len(set(suits)) == 1
    uniq = sorted(set(ranks), reverse=True)
    straight_hi = -1
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_hi = uniq[0]
        elif uniq == [12, 3, 2, 1, 0]:
            straight_hi = 3
    cnt = Counter(ranks)
    groups = sorted(cnt.items(), key=lambda x: (x[1], x[0]), reverse=True)
    shape = [g[1] for g in groups]
    order = [g[0] for g in groups]
    if straight_hi >= 0 and flush:
        return (8, [straight_hi])
    if shape == [4, 1]:
        return (7, order)
    if shape == [3, 2]:
        return (6, order)
    if flush:
        return (5, ranks)
    if straight_hi >= 0:
        return (4, [straight_hi])
    if shape == [3, 1, 1]:
        return (3, order)
    if shape == [2, 2, 1]:
        return (2, order)
    if shape == [2, 1, 1, 1]:
        return (1, order)
    return (0, ranks)


def ref_best(cards):
    return max(ref_eval5(list(c)) for c in itertools.combinations(cards, 5))


def test_matches_reference_on_random_hands():
    rng = random.Random(1234)
    for _ in range(4000):
        n = rng.choice([5, 6, 7])
        cards = rng.sample(range(52), n)
        a = evaluate(cards)
        b = ref_best(cards)
        assert a >> 20 == b[0], (cards, a, b)


def test_ordering_consistent_with_reference():
    rng = random.Random(99)
    for _ in range(3000):
        deck = rng.sample(range(52), 9)
        board = deck[:5]
        h1 = board + deck[5:7]
        h2 = board + deck[7:9]
        e1, e2 = evaluate(h1), evaluate(h2)
        r1, r2 = ref_best(h1), ref_best(h2)
        assert (e1 > e2) == (r1 > r2)
        assert (e1 == e2) == (r1 == r2)


def test_specific_hands():
    assert category(evaluate(parse_cards("AhKhQhJhTh2c3d"))) == STRAIGHT_FLUSH
    assert describe_score(evaluate(parse_cards("AhKhQhJhTh"))) == "Royal flush"
    assert category(evaluate(parse_cards("5d4c3h2sAd9c9h"))) == STRAIGHT
    assert describe_score(evaluate(parse_cards("5d4c3h2sAd"))) == "Straight, Five high"
    assert category(evaluate(parse_cards("KsKdKh7c7d2s3s"))) == FULL_HOUSE
    assert category(evaluate(parse_cards("2h7h9hJhQh3c3d"))) == FLUSH
    # two trips make a full house with the higher trips
    assert describe_score(evaluate(parse_cards("9s9d9h4c4d4h2s"))) == "Full house, Nines full of Fours"
    # three pairs: best two plus best kicker
    s = evaluate(parse_cards("QsQdJhJc5d5h2s"))
    assert describe_score(s) == "Two pair, Queens and Jacks"
