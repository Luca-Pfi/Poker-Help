import pytest

from poker_bot.game import Hand, HandError, position_names
from poker_bot.session import Session


def setup(n=6, button=0, hero=0, cards="AhKd", stacks=100.0):
    return {
        "players": [{"seat": i, "name": "P%d" % i, "pid": "p%d" % i, "stack": stacks} for i in range(n)],
        "button": button, "sb": 0.5, "bb": 1.0, "hero_seat": hero, "hero_cards": cards,
    }


def test_positions():
    assert position_names(2) == ["BTN", "BB"]
    assert position_names(6) == ["BTN", "SB", "BB", "UTG", "HJ", "CO"]
    assert position_names(9)[-1] == "CO"
    assert len(position_names(10)) == 10


def test_blinds_and_first_to_act():
    h = Hand(setup(6, button=0))
    st = h.state()
    assert st["pot"] == 1.5
    assert h.sb_seat == 1 and h.bb_seat == 2
    assert h.to_act == 3            # UTG
    assert h.legal()["to_call"] == 1.0


def test_heads_up_button_posts_small_blind_and_acts_first():
    h = Hand(setup(2, button=0))
    assert h.sb_seat == 0 and h.bb_seat == 1
    assert h.to_act == 0
    h.apply({"type": "action", "seat": 0, "action": "call"})
    assert h.to_act == 1            # BB option
    h.apply({"type": "action", "seat": 1, "action": "check"})
    assert h.awaiting_board == 3
    h.apply({"type": "board", "cards": "2c7d9h"})
    assert h.to_act == 1            # BB first post-flop


def test_big_blind_option_and_round_close():
    h = Hand(setup(4, button=0))   # SB1 BB2 UTG3
    for seat in (3, 0, 1):
        h.apply({"type": "action", "seat": seat, "action": "call"})
    assert h.to_act == 2
    h.apply({"type": "action", "seat": 2, "action": "check"})
    assert h.awaiting_board == 3 and h.to_act is None


def test_raise_reopens_action():
    h = Hand(setup(4, button=0))
    h.apply({"type": "action", "seat": 3, "action": "raise", "amount": 3})
    h.apply({"type": "action", "seat": 0, "action": "call"})
    h.apply({"type": "action", "seat": 1, "action": "fold"})
    h.apply({"type": "action", "seat": 2, "action": "raise", "amount": 10})
    assert h.to_act == 3
    h.apply({"type": "action", "seat": 3, "action": "call"})
    assert h.to_act == 0
    h.apply({"type": "action", "seat": 0, "action": "fold"})
    assert h.awaiting_board == 3
    assert h.pot() == 0.5 + 10 + 10 + 3


def test_everyone_folds_ends_hand():
    h = Hand(setup(3, button=0))   # BTN0 SB1 BB2 ; BTN first preflop
    h.apply({"type": "action", "seat": 0, "action": "fold"})
    h.apply({"type": "action", "seat": 1, "action": "fold"})
    assert h.finished and h.result["winners"] == [2]


def test_illegal_actions():
    h = Hand(setup(3, button=0))
    with pytest.raises(HandError):
        h.apply({"type": "action", "seat": 2, "action": "call"})     # not their turn
    with pytest.raises(HandError):
        h.apply({"type": "action", "seat": 0, "action": "check"})    # facing the big blind
    with pytest.raises(HandError):
        h.apply({"type": "action", "seat": 0, "action": "raise", "amount": 1})


def test_all_in_runout_needs_only_board():
    h = Hand(setup(2, button=0, stacks=20))
    h.apply({"type": "action", "seat": 0, "action": "allin"})
    h.apply({"type": "action", "seat": 1, "action": "call"})
    assert h.awaiting_board == 3
    h.apply({"type": "board", "cards": "2c7d9h"})
    assert h.awaiting_board == 1 and h.to_act is None
    h.apply({"type": "board", "cards": "Js"})
    h.apply({"type": "board", "cards": "3s"})
    assert h.finished and h.result["reason"] == "showdown"


def test_board_rejects_duplicate_cards():
    h = Hand(setup(2, button=0, cards="AhKd"))
    h.apply({"type": "action", "seat": 0, "action": "call"})
    h.apply({"type": "action", "seat": 1, "action": "check"})
    with pytest.raises(HandError):
        h.apply({"type": "board", "cards": "Ah7d9h"})


def test_side_pot_payouts():
    st = setup(3, button=0)
    st["players"][0]["stack"] = 10      # short stack
    h = Hand(st)
    h.apply({"type": "action", "seat": 0, "action": "allin"})
    h.apply({"type": "action", "seat": 1, "action": "raise", "amount": 30})
    h.apply({"type": "action", "seat": 2, "action": "call"})
    for cards in ("2c7d9h", "Js", "3s"):
        h.apply({"type": "board", "cards": cards})
        while h.to_act is not None:
            h.apply({"type": "action", "seat": h.to_act, "action": "check"})
    assert h.finished
    pots = h.pots()
    assert [p["amount"] for p in pots] == [30, 40]
    assert pots[1]["eligible"] == [1, 2]
    pay = h.payouts([[0], [1]])        # short stack wins main, seat 1 the side pot
    assert pay[0] == 30                 # 10 from each of three players
    assert pay[1] == 40                 # side pot 20 + 20
    assert pay[2] == 0
    split = h.payouts([0, 1])           # 0 and 1 split the main pot, 1 takes the side pot
    assert split[0] == 15 and split[1] == 55
    with pytest.raises(HandError):
        h.payouts([0])                  # side pot winner missing


def test_session_flow_and_undo_and_stats():
    s = Session(config={"players": 3, "hero_seat": 0, "button": 0})
    s.start_hand("AhKd")
    s.act(0, "raise", 3)
    s.act(1, "fold")
    s.act(2, "call")
    s.board("2c7d9h")
    s.act(2, "check")
    s.act(0, "bet", 4)
    s.undo()
    assert s.hand.to_act == 0
    s.act(0, "bet", 4)
    s.act(2, "fold")
    res = s.finish()
    assert res["winners"] == [0]
    assert s.seats[0]["stack"] == 100 + 3 + 0.5
    st = s.tracker.raw(s.seats[2]["pid"])
    assert st["hands"] == 1 and st["vpip"] == 1.0 and st["fold_to_bet"] == 1.0
    hero = s.tracker.raw(s.seats[0]["pid"])
    assert hero["pfr"] == 1.0 and hero["cbet"] == 1.0
    assert s.button == 1                # moved on
    # serialisation round trip
    s2 = Session(data=s.to_json())
    assert s2.tracker.raw(s.seats[2]["pid"]) == st
