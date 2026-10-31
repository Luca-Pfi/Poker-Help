"""Terminal version of the advisor.

    python -m poker_bot              # new session (or resume the saved one)
    python -m poker_bot --new        # always start fresh

The session (including opponent stats) is saved to ~/.poker_help_session.json
after every hand.
"""

import argparse
import json
import os
import sys

from .session import Session
from .game import HandError
from .cards import pretty_cards

SAVE = os.path.expanduser("~/.poker_help_session.json")

HELP = """Actions:  f = fold   k = check   c = call   b 6 / r 15 = bet / raise TO amount
          a = all-in   u = undo   s = stats   q = quit (session is saved)"""


def ask(prompt, default=None):
    try:
        v = input(prompt).strip()
    except EOFError:
        print()
        sys.exit(0)
    return v if v else (default if default is not None else "")


def save(sess):
    with open(SAVE, "w") as f:
        json.dump(sess.to_json(), f)


def pct(x):
    return "  -  " if x is None else "%4.0f%%" % (100 * x)


def show_stats(sess):
    print("\n%-14s %-24s %5s %6s %6s %6s %6s %8s" % ("Player", "Type", "Hands", "VPIP", "PFR", "3bet", "AF", "Fold/bet"))
    for d in sess.stats():
        r = d["raw"]
        af = "  -  " if r["af"] is None else "%5.1f" % r["af"]
        print("%-14s %-24s %5d %6s %6s %6s %6s %8s" % (d["name"][:14], d["profile"]["label"][:24], r["hands"],
                                                    pct(r["vpip"]), pct(r["pfr"]), pct(r["threebet"]), af,
                                                    pct(r["fold_to_bet"])))
    print()


def show_advice(sess):
    a = sess.advise(time_budget=1.5)
    if a.get("status") != "ok":
        return
    print("\n  ----- ADVICE -------------------------------------------")
    print("  %s %s" % (a["hero_cards"], ("- " + a["hand_desc"]) if a["hand_desc"] else ""))
    if a["draws"]:
        print("  Draws: %s (%d outs)" % (", ".join(a["draws"]), a["outs"]))
    print("  Equity %.1f%%   pot %s   to call %s   pot odds %s   (%d sims)"
          % (100 * a["equity"], a["pot"], a["to_call"], pct(a["pot_odds"]).strip(), a["iterations"]))
    for o in a["options"]:
        mark = ">>" if o["label"] == a["recommendation"]["label"] else "  "
        extra = ""
        if o["p_fold"] is not None:
            extra = "  folds %s, equity if called %s" % (pct(o["p_fold"]).strip(), pct(o["equity_called"]).strip())
        print("  %s %-24s EV %+7.2f bb%s" % (mark, o["label"], o["ev_bb"], extra))
    print("  RECOMMENDED: %s  (%s)" % (a["recommendation"]["label"].upper(), a["confidence"]))
    for r in a["reasons"]:
        print("   - " + r)
    print("  --------------------------------------------------------\n")


def setup():
    print("New session")
    n = int(ask("Players at the table [6]: ", "6"))
    hero = int(ask("Your seat number (1-%d) [1]: " % n, "1")) - 1
    sb = float(ask("Small blind [0.5]: ", "0.5"))
    bb = float(ask("Big blind [1]: ", "1"))
    stack = float(ask("Starting stack [100]: ", "100"))
    names = []
    for i in range(n):
        default = "You" if i == hero else "Seat %d" % (i + 1)
        names.append(ask("  Name for seat %d [%s]: " % (i + 1, default), default))
    return Session(config={"players": n, "hero_seat": hero, "sb": sb, "bb": bb,
                           "start_stack": stack, "names": names})


def play_hand(sess):
    st = sess.state()
    hero_pos = next(s["next_position"] for s in st["seats"] if s["is_hero"])
    btn = next(s["name"] for s in st["seats"] if s["seat"] == st["button"])
    print("\n=== Hand #%d  (button: %s, you are %s) ===" % (st["hand_no"] + 1, btn, hero_pos))
    while True:
        cards = ask("Your cards (e.g. AhKd), 's' stats, 'q' quit: ")
        if cards.lower() == "q":
            save(sess)
            sys.exit(0)
        if cards.lower() == "s":
            show_stats(sess)
            continue
        try:
            sess.start_hand(cards)
            break
        except (HandError, ValueError) as e:
            print("  !", e)
    last_advised = None
    while True:
        h = sess.hand
        if h.finished:
            break
        if h.awaiting_board:
            name = {3: "Flop", 1: "Turn" if len(h.board) == 3 else "River"}[h.awaiting_board]
            v = ask("%s card%s: " % (name, "s" if h.awaiting_board > 1 else ""))
            if v.lower() == "u":
                sess.undo()
                continue
            try:
                sess.board(v)
                print("  Board: %s   pot %s" % (pretty_cards(sess.hand.board), sess.hand.pot()))
            except (HandError, ValueError) as e:
                print("  !", e)
            continue
        seat = h.to_act
        pl = h.p[seat]
        L = h.legal()
        if seat == h.hero_seat and last_advised != len(h.events):
            show_advice(sess)
            last_advised = len(h.events)
        facing = ("to call %s" % L["to_call"]) if L["to_call"] > 0 else "no bet"
        v = ask("%s (%s) %s, pot %s > " % (pl["name"], pl["position"], facing, L["pot"])).lower().split()
        if not v:
            continue
        cmd = v[0]
        try:
            if cmd == "u":
                sess.undo()
                if sess.hand is None:
                    return
            elif cmd == "s":
                show_stats(sess)
            elif cmd == "q":
                save(sess)
                sys.exit(0)
            elif cmd in ("h", "?"):
                print(HELP)
            elif cmd == "f":
                sess.act(seat, "fold")
            elif cmd == "k":
                sess.act(seat, "check")
            elif cmd == "c":
                sess.act(seat, "call")
            elif cmd == "a":
                sess.act(seat, "allin")
            elif cmd in ("b", "r"):
                if len(v) < 2:
                    print("  ! give an amount, e.g. 'r 7.5'")
                    continue
                sess.act(seat, "bet" if cmd == "b" else "raise", float(v[1]))
            else:
                print(HELP)
        except (HandError, ValueError) as e:
            print("  !", e)

    h = sess.hand
    winners = None
    if h.result["reason"] == "showdown":
        live = h.live_seats()
        for s in live:
            if s != h.hero_seat:
                c = ask("Cards shown by %s (Enter if mucked/unknown): " % h.p[s]["name"])
                if c:
                    try:
                        sess.show(s, c)
                    except (HandError, ValueError) as e:
                        print("  !", e)
        auto = sess.hand.auto_pot_winners()
        if auto:
            winners = auto
        else:
            pots = sess.hand.pots()
            winners = []
            for i, pot in enumerate(pots):
                names = ", ".join("%d=%s" % (k + 1, h.p[s]["name"]) for k, s in enumerate(pot["eligible"]))
                while True:
                    raw = ask("%s %s - winner(s) [%s]: " % ("Main pot" if i == 0 else "Side pot %d" % i, pot["amount"], names))
                    try:
                        picks = [pot["eligible"][int(x) - 1] for x in raw.replace(",", " ").split()]
                        if picks:
                            break
                    except (ValueError, IndexError):
                        pass
                    print("  ! enter the number(s) of the winner(s)")
                winners.append(picks)
    res = sess.finish(winners)
    names = ", ".join(sess.state()["seats"][w]["name"] for w in res["winners"])
    print("  %s won. Your result: %+.2f" % (names, res["hero_net"]))
    save(sess)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Poker Help - terminal advisor")
    ap.add_argument("--new", action="store_true", help="start a new session")
    args = ap.parse_args(argv)
    sess = None
    if not args.new and os.path.exists(SAVE):
        try:
            with open(SAVE) as f:
                sess = Session(data=json.load(f))
            if sess.hand is not None:
                sess.abandon()
            print("Resumed saved session (%d hands). Use --new to start over." % sess.hand_no)
        except Exception as e:  # corrupted file
            print("Could not load saved session:", e)
            sess = None
    if sess is None:
        sess = setup()
        save(sess)
    print(HELP)
    while True:
        play_hand(sess)


if __name__ == "__main__":
    main()
