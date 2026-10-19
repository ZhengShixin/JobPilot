"""The daily report (reports/daily/<date>.md + .json): what the search found, what is due, what to do next.

Written by `jobpilot daily` after each run and shown on the dashboard. English or Chinese (app.language).
"""
from datetime import timedelta

from . import budget, config, ledger, pool, util

T = {
    "en": {
        "title": "JobPilot daily report — {date}",
        "new": "New matches", "none_new": "No new matches today.",
        "track_line": "**{name}**: {admitted} admitted out of {seen} new postings",
        "due": "Due soon", "nothing_due": "Nothing due in the next 7 days.",
        "oa": "Assessment for {company} ({role}) due {due}", "iv": "Interview with {company} ({role}) at {time}",
        "unsched": "Interview with {company} ({role}): no time agreed yet",
        "fu": "Follow up on {company} ({role}): applied {days} days ago, no answer yet",
        "pipeline": "Pipeline", "queue": "{n} postings ready in the queue",
        "funnel": "{applied} applied · {oa} assessments · {interview} interviews · {offer} offers · {open} open",
        "spend": "Search spend", "spend_line": "today ${today:.2f} of ${cap:.2f} · this month ${month:.2f} of ${monthly:.2f}",
        "problems": "Problems", "next": "Next steps",
        "n1": "Open the dashboard (`/jobpilot:dashboard`) and pick today's applications.",
        "n2": "Tailor a resume for each one (`/jobpilot:tailor <id>`) and apply yourself; then mark it applied.",
        "score": "score",
    },
    "zh": {
        "title": "JobPilot 每日报告 — {date}",
        "new": "今日新匹配", "none_new": "今天没有新的匹配岗位。",
        "track_line": "**{name}**：新岗位 {seen} 个，入库 {admitted} 个",
        "due": "即将到期", "nothing_due": "未来 7 天没有到期事项。",
        "oa": "{company}（{role}）的测评 {due} 截止", "iv": "{company}（{role}）面试：{time}",
        "unsched": "{company}（{role}）面试：还没约定时间",
        "fu": "跟进 {company}（{role}）：投递已 {days} 天，还没有回复",
        "pipeline": "投递进展", "queue": "队列里有 {n} 个可投岗位",
        "funnel": "已投 {applied} · 测评 {oa} · 面试 {interview} · offer {offer} · 进行中 {open}",
        "spend": "搜索花费", "spend_line": "今天 ${today:.2f} / ${cap:.2f} · 本月 ${month:.2f} / ${monthly:.2f}",
        "problems": "问题", "next": "下一步",
        "n1": "打开 dashboard（`/jobpilot:dashboard`），挑今天要投的岗位。",
        "n2": "为每个岗位定制简历（`/jobpilot:tailor <编号>`），自己投递后标记为已投。",
        "score": "分",
    },
}


def build(ws, date=None, collect=None, review=None, problems=None):
    cfg = ws.cfg
    lang = config.language(cfg)
    t = T[lang]
    date = date or ws.today()
    next_day = (util.parse_time(date + "T00:00:00") + timedelta(days=1)).date().isoformat()
    new_rows = ws.db.find("first_seen >= ? AND first_seen < ?", (date, next_day))  # postings first seen that day
    tracks = []
    for tr in config.tracks(cfg):
        seen = [r for r in new_rows if tr["id"] in (r.get("hint_tracks") or []) or r.get("track") == tr["id"]]
        admitted = [r for r in new_rows if r.get("track") == tr["id"] and r.get("status") in ("ready", "applied", "hold")]
        tops = sorted(admitted, key=lambda r: -(r.get("score") or 0))[:3]
        tracks.append({"id": tr["id"], "name": tr.get("name", tr["id"]), "seen": len(seen), "admitted": len(admitted),
                       "top": [pool.public(r) for r in tops]})
    due = ledger.due(ws)
    fun = ledger.funnel(ws)
    money = budget.summary(ws)
    queue_n = len(pool.queue(ws))
    data = {"date": date, "tracks": tracks, "due": due, "funnel": fun, "budget": money, "queue": queue_n,
            "collect": collect, "review": review, "problems": problems or []}
    lines = ["# " + t["title"].format(date=date), ""]
    lines += ["## " + t["new"], ""]
    for tr in tracks:
        lines.append("- " + t["track_line"].format(**tr))
        for r in tr["top"]:
            lines.append("  - %s · %s — %s (%s %.1f) %s" % (r.get("company"), r.get("title"), r.get("location") or "",
                                                           t["score"], r.get("score") or 0, r.get("url") or ""))
    if not any(tr["admitted"] for tr in tracks):
        lines += ["", t["none_new"]]
    lines += ["", "## " + t["due"], ""]
    items = [t["oa"].format(**d) for d in due["deadlines"]] + [t["iv"].format(**d) for d in due["interviews"]] + \
        [t["unsched"].format(**d) for d in due["unscheduled"]] + [t["fu"].format(**d) for d in due["followups"][:5]]
    lines += ["- " + x for x in items] or [t["nothing_due"]]
    lines += ["", "## " + t["pipeline"], "", "- " + t["queue"].format(n=queue_n), "- " + t["funnel"].format(**fun)]
    lines += ["", "## " + t["spend"], "", "- " + t["spend_line"].format(today=money["today"], cap=money["today_cap"],
                                                                         month=money["month"],
                                                                         monthly=money["monthly_budget"])]
    if problems:
        lines += ["", "## " + t["problems"], ""] + ["- " + p for p in problems]
    lines += ["", "## " + t["next"], "", "1. " + t["n1"], "2. " + t["n2"], ""]
    text = "\n".join(lines)
    ws.reports.mkdir(parents=True, exist_ok=True)
    util.atomic_write_text(ws.reports / ("%s.md" % date), text)
    util.atomic_write_json(ws.reports / ("%s.json" % date), data)
    return text, data


def latest(ws):
    files = sorted(ws.reports.glob("*.json"))
    if not files:
        return None
    data = util.read_json(files[-1])
    md = files[-1].with_suffix(".md")
    data["markdown"] = md.read_text(encoding="utf-8") if md.exists() else ""
    return data


def dates(ws):
    return [p.stem for p in sorted(ws.reports.glob("*.json"))]
