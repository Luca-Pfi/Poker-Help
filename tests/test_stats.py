"""Session stats: your own record, advice adherence, head-to-head and
table-size-aware player profiles."""

from poker_bot.session import Session
from poker_bot.tracker import Tracker, new_stats


def play_simple_hand(s, follow=True):
    # 3-handed: button 0 (hero), SB 1, BB 2
    s.start_hand("AhKd")
    adv = {"action": "raise", "to": 2.5} if follow else {"action": "fold"}
    s.act(0, "raise", 2.5, adv)
    s.act(1, "fold")
    s.act(2, "call")
    s.board("2c7d9h")
    s.act(2, "check")
    s.act(0, "bet", 3, {"action": "bet", "to": 3})
    s.act(2, "fold")
    return s.finish()


def test_hero_record_and_adherence():
    s = Session(config={"players": 3, "hero_seat": 0, "button": 0})
    play_simple_hand(s, follow=True)
    hero = s.hero_summary()
    assert hero["hands"] == 1
    assert hero["net"] == 3.0            # SB's 0.5 blind + BB's 2.5 call
    assert hero["decisions"] == 2 and hero["followed_pct"] == 1.0
    assert hero["hands_followed"] == 1 and hero["hands_deviated"] == 0
    assert hero["curve"] == [3.0]
    assert hero["by_position"]["BTN"]["hands"] == 1


def test_deviation_is_recorded():
    s = Session(config={"players": 3, "hero_seat": 0, "button": 0})
    play_simple_hand(s, follow=False)
    hero = s.hero_summary()
    assert hero["followed_pct"] == 0.5
    assert hero["hands_deviated"] == 1


def test_head_to_head():
    s = Session(config={"players": 3, "hero_seat": 0, "button": 0})
    play_simple_hand(s)
    bb_pid = s.seats[2]["pid"]
    sb_pid = s.seats[1]["pid"]
    assert s.h2h[bb_pid]["hands"] == 1 and s.h2h[bb_pid]["net"] == 3.0
    assert sb_pid not in s.h2h           # folded pre-flop without putting money in voluntarily
    st = s.stats()
    bb_row = next(p for p in st["players"] if p["pid"] == bb_pid)
    assert bb_row["h2h"]["hands"] == 1
    assert {b["key"] for b in st["benchmarks"]} >= {"vpip", "pfr", "af", "wtsd"}


def test_record_survives_save_and_load():
    s = Session(config={"players": 3, "hero_seat": 0, "button": 0})
    play_simple_hand(s)
    s2 = Session(data=s.to_json())
    assert s2.hero_summary()["net"] == 3.0
    assert s2.h2h == s.h2h


def test_profiles_depend_on_table_size():
    """60% VPIP is loose at a 6-max table but normal heads-up."""
    st = new_stats("x")
    st.update({"hands": 200, "vpip": 120, "pfr": 80, "post_aggr": 60, "post_calls": 40, "post_checks": 40,
               "post_folds": 20, "faced_bet": 60, "folded_to_bet": 25})
    six = Tracker({"p": dict(st)}, table_size=6)
    hu = Tracker({"p": dict(st)}, table_size=2)
    assert six.profile("p")["key"] in ("lag", "maniac")
    assert hu.profile("p")["key"] in ("reg", "tag")


def test_advice_has_plain_english_summary():
    s = Session(config={"players": 6, "hero_seat": 5, "button": 5})
    s.start_hand("AhAd")
    for seat in (2, 3, 4):
        s.act(seat, "fold")
    a = s.advise(time_budget=0.2, seed=1)
    assert a["summary"] and a["kind"] == "open"
    assert a["recommendation"]["action"] == "raise"
