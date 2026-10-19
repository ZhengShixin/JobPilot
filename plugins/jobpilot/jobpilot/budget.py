"""Apify spending under a daily and a monthly cap (data/budget.jsonl).

Every paid run reserves its cap (the run's maxTotalChargeUsd) before it starts and settles its real cost when it
ends; spent = settled costs + open reservations. Today's cap = min(daily cap, what the month has left spread evenly
over its remaining days), so a busy week can never eat the end of the month.
"""
import calendar

from . import util


def _slots(ws):
    slots = {}
    for e in util.jsonl_read(ws.budget_path):
        s = slots.setdefault(e.get("slot"), {"reserved": 0.0, "settled": None, "released": False, "day": e.get("day"),
                                             "kind": e.get("kind", "search"), "note": e.get("note", "")})
        op = e.get("op")
        if op == "reserve":
            s["reserved"] = float(e.get("usd") or 0)
            s["day"] = e.get("day")
            s["released"] = False
        elif op == "settle":
            s["settled"] = float(e.get("usd") or 0)
            s["run_id"] = e.get("run_id")
        elif op == "release":
            s["released"] = True
    return slots


def _cost(s):
    if s["settled"] is not None:
        return s["settled"]
    return 0.0 if s["released"] else s["reserved"]


def spent(ws, day=None, month=None, kind=None):
    total = 0.0
    for s in _slots(ws).values():
        d = s.get("day") or ""
        if day and d != day:
            continue
        if month and not d.startswith(month):
            continue
        if kind and s.get("kind") != kind:
            continue
        total += _cost(s)
    return round(total, 4)


def today_cap(ws):
    cfg = ws.cfg["search"]
    today = ws.now().date()
    month = today.strftime("%Y-%m")
    before_today = spent(ws, month=month) - spent(ws, day=today.isoformat())
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
    monthly_left = max(0.0, float(cfg.get("monthly_budget_usd") or 0) - before_today)
    return round(min(float(cfg.get("daily_budget_usd") or 0), monthly_left / days_left), 4)


def available(ws):
    return round(max(0.0, today_cap(ws) - spent(ws, day=ws.today())), 4)


def reserve(ws, slot, usd, kind="search", note=""):
    usd = round(float(usd), 4)
    if usd > available(ws) + 1e-9:
        raise util.JobPilotError("Apify budget: $%.2f needed, $%.2f left today" % (usd, available(ws)))
    return util.jsonl_append(ws.budget_path, {"op": "reserve", "slot": slot, "usd": usd, "day": ws.today(),
                                              "kind": kind, "note": note})


def settle(ws, slot, usd, run_id=None):
    return util.jsonl_append(ws.budget_path, {"op": "settle", "slot": slot, "usd": round(float(usd), 4),
                                              "run_id": run_id})


def release(ws, slot):
    return util.jsonl_append(ws.budget_path, {"op": "release", "slot": slot})


def summary(ws):
    today = ws.today()
    month = today[:7]
    cfg = ws.cfg["search"]
    return {"today": spent(ws, day=today), "today_cap": today_cap(ws), "available": available(ws),
            "month": spent(ws, month=month), "monthly_budget": float(cfg.get("monthly_budget_usd") or 0),
            "daily_budget": float(cfg.get("daily_budget_usd") or 0),
            "month_search": spent(ws, month=month, kind="search"),
            "month_referrals": spent(ws, month=month, kind="referrals")}
