"""Check a search plan offline before it costs anything.

profile/plan-examples.json (written by /jobpilot:setup, extended by /jobpilot:tune) lists postings the plan should and
should not return:
    {"positive": [{"title": "Machine Learning Engineer", "track": "mle", "description": "...", "industry": "",
                   "company": ""}],
     "negative": [{"title": "Sales Engineer"}]}
check() replays them through the actor's matching rules (querymatch) and reports recall per track, negatives that
would be paid for, groups that match nothing, stemming traps ("Research" never matches "Researcher"), unknown input
keys and bad enum values, and a cost estimate from earlier runs.
"""
import re

from . import config, querymatch, search, util
from .apify import known_schema

PRICE_PER_ROW = 0.005   # the actor's free-tier price per posting; paid Apify plans pay less
BROAD = {"engineer", "analyst", "manager", "developer", "scientist", "associate", "specialist", "consultant", "lead",
         "director", "intern", "senior", "junior", "staff", "data", "software", "research", "researcher"}
SYMBOLS = re.compile(r"[+#]|\.\w")


def _examples(ws, path=None):
    data = util.read_json(path or ws.examples_path, None)
    if not data:
        return {"positive": [], "negative": []}
    return {"positive": list(data.get("positive") or []), "negative": list(data.get("negative") or [])}


def lint(cfg):
    """[(level, message)] about the plan's filters themselves."""
    out = []
    schema = known_schema(cfg["search"]["actor"])
    for name, g in config.groups(cfg):
        f = config.group_filters(cfg, g)
        for key, value in f.items():
            if schema and key not in schema:
                out.append(("error", "group %s: %s is not an input of %s" % (name, key, cfg["search"]["actor"])))
                continue
            enum = (schema.get(key) or {}).get("enum")
            if enum:
                values = value if isinstance(value, list) else [value]
                bad = [v for v in values if v not in enum]
                if bad:
                    out.append(("error", "group %s: %s values %s are not among %s" % (name, key, bad, enum)))
        for key in config.TEXT_FILTERS:
            for term in f.get(key) or []:
                if SYMBOLS.search(term):
                    out.append(("warn", "group %s: %r in %s: symbols are dropped by the search ('C++' becomes 'c', "
                                        "which matches any 'C'); spell it out or drop it" % (name, term, key)))
                if ":*" in term and not re.search(r"\w:\*(\s|$)", term):
                    out.append(("error", "group %s: %r: ':*' must follow a word (prefix match)" % (name, term)))
                words = querymatch.parse(term)
                if key == "titleSearch" and len(words) == 1 and words[0][0] in BROAD:
                    out.append(("warn", "group %s: titleSearch %r alone matches a huge number of postings (cost); "
                                        "combine it with descriptionSearch or use a phrase" % (name, term)))
        n = len(f.get("descriptionSearch") or [])
        if n > 13:
            out.append(("warn", "group %s: %d descriptionSearch terms; keep a handful (<= 13)" % (name, n)))
    return out


def _relaxed(term):
    """The term with every word as a prefix ("Data Scientist" -> "Data:* Scientist:*")."""
    return " ".join(w if w.endswith(":*") else w + ":*" for w in term.split())


def stemming_traps(groups, missed):
    """For positive examples no group returns: terms that would match them if their words were prefixes, i.e. the
    plan misses them only because the search does not stem (Research vs Researcher, Engineer vs Engineers)."""
    out = []
    for ex in missed:
        title = ex.get("title", "")
        doc = querymatch.Doc(title)
        for name, f in groups:
            for term in f.get("titleSearch") or []:
                if querymatch.term_matches(querymatch.parse(_relaxed(term)), doc) and \
                        not querymatch.term_matches(querymatch.parse(term), doc):
                    out.append({"group": name, "term": term, "title": title, "suggest": _relaxed(term)})
    return out


def history(ws):
    """Observed rows and cost per group per day from earlier manifests."""
    per = {}
    for _, m in search.manifests(ws):
        start, end = util.parse_time(m.get("window_start")), util.parse_time(m.get("window_end"))
        hours = max(1.0, (end - start).total_seconds() / 3600) if start and end else 36.0
        for name, g in (m.get("groups") or {}).items():
            if g.get("status") != "SUCCEEDED":
                continue
            h = per.setdefault(name, {"runs": 0, "rows_per_day": 0.0, "cost_per_day": 0.0})
            h["runs"] += 1
            h["rows_per_day"] += (g.get("rows") or 0) * 24 / hours
            h["cost_per_day"] += (g.get("cost") or 0) * 24 / hours
    for h in per.values():
        h["rows_per_day"] = round(h["rows_per_day"] / h["runs"], 1)
        h["cost_per_day"] = round(h["cost_per_day"] / h["runs"], 3)
    return per


def check(ws, examples=None):
    cfg = ws.cfg
    problems = config.check(cfg, ws) + lint(cfg)
    groups = [(g["name"], g["filters"]) for g in search.plan(cfg)]
    track_of = {g["name"]: g["track"] for g in search.plan(cfg)}
    ex = _examples(ws, examples)
    report = {"errors": [m for lvl, m in problems if lvl == "error"],
              "warnings": [m for lvl, m in problems if lvl == "warn"],
              "examples": {"positive": len(ex["positive"]), "negative": len(ex["negative"])},
              "tracks": {}, "groups": {}, "negatives": {"matched": []}, "traps": [], "history": history(ws)}
    for name, _ in groups:
        report["groups"][name] = {"track": track_of[name], "positives": 0, "unique": 0, "negatives": 0}
    for t in config.track_ids(cfg):
        report["tracks"][t] = {"positives": 0, "found": 0, "missed": []}
    for e in ex["positive"]:
        hits = querymatch.plan_groups(groups, e.get("title", ""), e.get("company", ""), e.get("industry", ""),
                                      e.get("description", ""))
        t = report["tracks"].setdefault(e.get("track") or "?", {"positives": 0, "found": 0, "missed": []})
        t["positives"] += 1
        if hits:
            t["found"] += 1
        else:
            t["missed"].append(e.get("title", ""))
        for h in hits:
            report["groups"][h]["positives"] += 1
        if len(hits) == 1:
            report["groups"][hits[0]]["unique"] += 1
    for e in ex["negative"]:
        hits = querymatch.plan_groups(groups, e.get("title", ""), e.get("company", ""), e.get("industry", ""),
                                      e.get("description", ""))
        for h in hits:
            report["groups"][h]["negatives"] += 1
        if hits:
            report["negatives"]["matched"].append({"title": e.get("title", ""), "groups": hits})
    for t in report["tracks"].values():
        t["recall"] = round(t["found"] / t["positives"], 2) if t["positives"] else None
    report["negatives"]["rate"] = round(len(report["negatives"]["matched"]) / len(ex["negative"]), 2) \
        if ex["negative"] else None
    report["dead_groups"] = [n for n, g in report["groups"].items() if ex["positive"] and not g["positives"]]
    missed = [e for e in ex["positive"] if not querymatch.plan_groups(groups, e.get("title", ""), e.get("company", ""),
                                                                      e.get("industry", ""), e.get("description", ""))]
    report["traps"] = stemming_traps(groups, missed)
    hist = report["history"]
    known = [hist[n]["cost_per_day"] for n, _ in groups if n in hist]
    report["estimate_usd_per_day"] = round(sum(known), 2) if known else None
    if not ex["positive"]:
        report["warnings"].append("no positive examples in profile/plan-examples.json: recall cannot be checked")
    for t, v in report["tracks"].items():
        if v["recall"] is not None and v["recall"] < 0.9:
            report["warnings"].append("track %s: the plan finds %d of %d example titles (%.0f%%); see 'missed'"
                                      % (t, v["found"], v["positives"], 100 * v["recall"]))
    report["ok"] = not report["errors"]
    return report


def render(r):
    lines = []
    for m in r["errors"]:
        lines.append("error: " + m)
    for m in r["warnings"]:
        lines.append("warn:  " + m)
    lines.append("")
    lines.append("examples: %(positive)d positive, %(negative)d negative" % r["examples"])
    for t, v in r["tracks"].items():
        if v["positives"]:
            lines.append("track %-10s recall %d/%d%s" % (t, v["found"], v["positives"],
                                                         ("  missed: " + "; ".join(v["missed"][:8])) if v["missed"] else ""))
    if r["negatives"]["matched"]:
        lines.append("negatives that would be returned (and paid for): " + "; ".join(
            "%s (%s)" % (m["title"], ",".join(m["groups"])) for m in r["negatives"]["matched"][:10]))
    lines.append("")
    lines.append("%-24s %-8s %9s %7s %9s %12s" % ("group", "track", "positives", "unique", "negatives", "rows/day"))
    for n, g in r["groups"].items():
        h = r["history"].get(n) or {}
        lines.append("%-24s %-8s %9d %7d %9d %12s" % (n, g["track"], g["positives"], g["unique"], g["negatives"],
                                                      h.get("rows_per_day", "-")))
    if r["dead_groups"]:
        lines.append("groups matching no positive example: " + ", ".join(r["dead_groups"]))
    for t in r["traps"]:
        lines.append("stemming trap: %r is missed by %s's %r (no stemming); add the variant or use %r"
                     % (t["title"], t["group"], t["term"], t["suggest"]))
    if r.get("estimate_usd_per_day") is not None:
        lines.append("estimated cost from earlier runs: $%.2f/day" % r["estimate_usd_per_day"])
    else:
        lines.append("no runs yet: the first `jobpilot collect` shows real volumes (every run is capped)")
    lines.append("plan OK" if r["ok"] else "plan has errors")
    return "\n".join(lines)
