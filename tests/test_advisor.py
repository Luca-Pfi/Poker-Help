"""Sanity checks: the advisor should agree with basic poker strategy in
clear-cut spots."""

from poker_bot.session import Session


def advise(players, hero, button, cards, script):
    s = Session(config={"players": players, "hero_seat": hero, "button": button})
    s.start_hand(cards)
    for step in script:
        if step[0] == "b":
            s.board(step[1])
        else:
            s.act(step[0], step[1], step[2] if len(step) > 2 else None)
    return s.advise(time_budget=0.4, seed=1)


def rec(a):
    return a["recommendation"]["action"]


def test_preflop_open_ranges():
    assert rec(advise(9, 3, 0, "AhAc", [])) == "raise"
    assert rec(advise(9, 3, 0, "7h2c", [])) == "fold"
    folded = [(3, "fold"), (4, "fold"), (5, "fold"), (6, "fold"), (7, "fold"), (8, "fold")]
    assert rec(advise(9, 0, 0, "Kh9c", folded)) == "raise"


def test_facing_open():
    opened = [(2, "raise", 2.5), (3, "fold"), (4, "fold")]
    assert rec(advise(6, 5, 5, "QhQc", opened)) in ("raise", "allin")
    assert rec(advise(6, 5, 5, "9h4c", opened)) == "fold"
    bb_spot = [(2, "fold"), (3, "fold"), (4, "fold"), (5, "raise", 2.5), (0, "fold")]
    assert rec(advise(6, 1, 5, "Th8h", bb_spot)) in ("call", "raise")
    assert rec(advise(6, 1, 5, "7h2c", bb_spot)) == "fold"


def test_postflop():
    pre = [(2, "fold"), (3, "fold"), (4, "fold"), (5, "raise", 2.5), (0, "fold"), (1, "call")]
    a = advise(6, 5, 5, "7h7c", pre + [("b", "Kd7s2c"), (1, "check")])
    assert rec(a) == "bet"
    assert a["equity"] > 0.85
    a = advise(6, 5, 5, "AhQh", pre + [("b", "Kh7h2c"), (1, "bet", 5.5)])
    assert rec(a) in ("call", "raise")
    assert "flush draw" in a["draws"]
    a = advise(6, 5, 5, "3d2d", pre + [("b", "KhTs2c"), (1, "bet", 5.5)])
    assert rec(a) == "fold"


def test_advice_payload_shape():
    a = advise(6, 5, 5, "AhKh", [(2, "raise", 2.5), (3, "fold"), (4, "fold")])
    assert a["status"] == "ok"
    assert 0 <= a["equity"] <= 1
    assert a["iterations"] > 100
    assert a["reasons"]
    opp = a["opponents"][0]
    assert len(opp["grid"]) == 13 and len(opp["grid"][0]) == 13
    labels = [o["label"] for o in a["options"]]
    assert labels[0] == "Fold"


def test_opponent_stats_change_advice():
    """A player who never folds to bets should not be bluffed."""
    s = Session(config={"players": 2, "hero_seat": 0, "button": 0})
    villain = s.seats[1]["pid"]
    st = s.tracker.get(villain)
    st.update({"hands": 200, "vpip": 120, "pfr": 10, "saw_flop": 110, "faced_bet": 150,
               "folded_to_bet": 8, "post_calls": 140, "post_aggr": 10, "post_checks": 120})
    s.start_hand("6h5h")
    s.act(0, "raise", 2.5)
    s.act(1, "call")
    s.board("Kd9s2c")
    s.act(1, "check")
    a = s.advise(time_budget=0.4, seed=3)
    assert a["recommendation"]["action"] == "check"
    prof = a["opponents"][0]["profile"]
    assert prof["key"] == "station"
