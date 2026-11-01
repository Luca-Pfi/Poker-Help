"""JSON-in / JSON-out facade used by the web page (through Pyodide) and
handy for any other front end.

    call("new_session", '{"players": 6, "hero_seat": 0}')  -> '{"ok": true, "state": {...}}'
"""

import json
import traceback

from .session import Session
from .game import HandError

_SESSION = None


def _state():
    return _SESSION.state() if _SESSION else None


def _need():
    if _SESSION is None:
        raise HandError("Start a session first")
    return _SESSION


def _m_new_session(a):
    global _SESSION
    _SESSION = Session(config=a)
    return {"state": _state()}


def _m_load(a):
    global _SESSION
    data = a.get("session", a)
    if isinstance(data, str):
        data = json.loads(data)
    _SESSION = Session(data=data)
    return {"state": _state()}


def _m_close(a):
    global _SESSION
    _SESSION = None
    return {"state": None}


def _m_export(a):
    return {"session": _need().to_json()}


def _m_state(a):
    return {"state": _state()}


def _m_start_hand(a):
    _need().start_hand(a["hero_cards"], a.get("button"))
    return {"state": _state()}


def _m_act(a):
    _need().act(a["seat"], a["action"], a.get("amount"), a.get("advised"))
    return {"state": _state()}


def _m_board(a):
    _need().board(a["cards"])
    return {"state": _state()}


def _m_show(a):
    _need().show(a["seat"], a["cards"])
    return {"state": _state()}


def _m_undo(a):
    _need().undo()
    return {"state": _state()}


def _m_abandon(a):
    _need().abandon()
    return {"state": _state()}


def _m_finish(a):
    res = _need().finish(a.get("winners"))
    return {"state": _state(), "result": res}


def _m_advise(a):
    adv = _need().advise(time_budget=float(a.get("time_budget", 1.2)))
    return {"advice": adv}


def _m_update_seat(a):
    _need().update_seat(a["seat"], name=a.get("name"), stack=a.get("stack"),
                        new_player=bool(a.get("new_player")), sitting_out=a.get("sitting_out"))
    return {"state": _state()}


def _m_set_config(a):
    _need().set_config(**a)
    return {"state": _state()}


def _m_set_button(a):
    _need().set_button(a["seat"])
    return {"state": _state()}


def _m_stats(a):
    return {"stats": _need().stats()}


METHODS = {k[3:]: v for k, v in list(globals().items()) if k.startswith("_m_")}


def call(method, args_json="{}"):
    try:
        args = json.loads(args_json) if isinstance(args_json, str) else (args_json or {})
        fn = METHODS.get(method)
        if fn is None:
            raise HandError("Unknown method %s" % method)
        out = fn(args or {})
        out["ok"] = True
        return json.dumps(out)
    except (HandError, ValueError, KeyError) as e:
        return json.dumps({"ok": False, "error": str(e)})
    except Exception as e:  # pragma: no cover - surfaced to the UI for debugging
        return json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                           "trace": traceback.format_exc()})
