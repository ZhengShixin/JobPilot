"""`jobpilot daily`: collect -> coarse filter -> AI review -> report -> notification, in one locked run.

Scheduled runs (`jobpilot schedule install`) call exactly this. A failing step is reported and the next one still
runs (a failed collection still lets the review clear its backlog), so the report always says what happened.
"""
import shlex
import subprocess

from . import brief, config, review, search, util


def notify(ws, title, text):
    n = ws.cfg.get("notify") or {}
    cmd = (n.get("command") or "").strip()
    if not cmd:
        return False
    full = cmd.replace("{title}", shlex.quote(title)).replace("{text}", shlex.quote(text))
    try:
        subprocess.run(full, shell=True, timeout=30, capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def run(ws, do_collect=True, do_review=True, log=print):
    problems, c, r = [], None, None
    with ws.lock("daily", wait=False):
        if do_collect:
            try:
                c = search.collect(ws, log=log)
                if c.get("skipped"):
                    problems.append("skipped for budget or refusal: " + ", ".join(c["skipped"]))
                if c.get("failed"):
                    problems.append("failed search groups: " + ", ".join(c["failed"]))
                if c.get("truncated"):
                    problems.append("capped search groups (may miss postings): " + ", ".join(c["truncated"]))
            except util.JobPilotError as exc:
                problems.append("collection: %s" % exc)
                log("collection: %s" % exc)
        if do_review:
            try:
                r = review.run(ws, log=log)
                problems += ["review: %s" % e for e in r.get("errors", [])[:3]]
            except util.JobPilotError as exc:
                problems.append("review: %s" % exc)
                log("review: %s" % exc)
        text, data = brief.build(ws, collect=c, review=r, problems=problems)
    ready = sum(t["admitted"] for t in data["tracks"])
    zh = config.language(ws.cfg) == "zh"
    title = "JobPilot"
    summary = ("今日新入库 %d 个岗位，队列 %d 个" % (ready, data["queue"])) if zh else \
        ("%d new matches today, %d in the queue" % (ready, data["queue"]))
    if problems:
        summary += ("（%d 个问题）" % len(problems)) if zh else (" (%d problems)" % len(problems))
    notify(ws, title, summary)
    ws.event("daily", summary, problems=problems)
    return {"collect": c, "review": r, "report": data, "problems": problems, "summary": summary}
