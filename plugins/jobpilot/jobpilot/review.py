"""The two-stage review of new postings: the free coarse filter (triage), then the AI review in batches.

    run(ws) -> summary

Stage 1 drops what the candidate can never take and reuses earlier reviews for reposts (same employer, title and JD).
Stage 2 sends batches of full job descriptions to a one-shot `claude -p` run (role jd-review, no tools, JSON schema
output). Each answer is checked by scoring.validate (verbatim quotes, ranges, categories); a posting whose review
fails is retried in a later batch, up to review.max_attempts times. A checked review sets the posting's status:
ready (in the queue), below_threshold, excluded or hold.
"""
from pathlib import Path

from . import claude, config, schema as S, scoring, search, triage, util

PROMPTS = Path(__file__).resolve().parent / "prompts"
CLOSED = ("applied", "skipped")


def eligibility_text(cfg):
    cand = cfg["candidate"]
    lines = []
    wa = cand.get("work_authorization") or ""
    wa_text = {
        "us_citizen": "U.S. citizen (citizenship and clearance requirements are fine unless an active clearance is "
                      "required and the profile shows none).",
        "permanent_resident": "U.S. permanent resident (green card): authorized to work, needs no sponsorship, but is "
                              "not a U.S. citizen. Exclude roles that strictly require U.S. citizenship or an active "
                              "security clearance (flag citizenship_required / clearance_required). A role open to "
                              "permanent residents or anyone authorized to work is fine.",
        "work_visa": "holds a U.S. work visa (authorized now; may need sponsorship later). Exclude roles that require "
                     "U.S. citizenship or a clearance; note 'no sponsorship' language in eligibility_flags.",
        "needs_sponsorship": "needs visa sponsorship. Exclude roles that say they cannot sponsor (flag "
                             "no_sponsorship) and roles that require U.S. citizenship or a clearance.",
        "other": "see the profile; exclude roles that require U.S. citizenship or a clearance.",
    }.get(wa)
    if wa_text:
        lines.append("Work authorization: " + wa_text)
    types = ["full-time"]
    if cand.get("open_to_contract"):
        types.append("contract")
    if cand.get("open_to_internships"):
        types.append("internships")
    lines.append("Employment: %s only; exclude other employment types (flag non_full_time)." % " or ".join(types))
    lines.append("Exclude roles that require current student enrollment or a graduation window the profile does not "
                 "match (flags enrollment_required / graduation_window), and roles with no current vacancy.")
    if cand.get("seniority"):
        lines.append("Target levels: %s. Senior-level gaps lower seniority_1 and may trigger a fit cap; they are not an "
                     "exclusion by themselves." % ", ".join(cand["seniority"]))
    if cand.get("locations"):
        lines.append("Locations: %s%s. Exclude roles that require relocation to somewhere else entirely "
                     "(flag location_mismatch)." % (", ".join(cand["locations"]),
                                                    " (work arrangements: %s)" % ", ".join(cand["work_arrangements"])
                                                    if cand.get("work_arrangements") else ""))
    if cand.get("languages"):
        lines.append("Languages: %s. Exclude roles that require fluency in another language (flag language_required)."
                     % ", ".join(cand["languages"]))
    return "\n".join("   - " + ln for ln in lines)


def tracks_text(cfg):
    out = []
    for t in config.tracks(cfg):
        cats = ", ".join(config.categories(cfg, t["id"]))
        out.append("- `%s` (priority %s) — %s: %s Categories: %s." % (
            t["id"], t.get("priority", 1), t.get("name", t["id"]), (t.get("description") or "").strip(), cats))
    return "\n".join(out)


def system_prompt(ws):
    text, _ = scoring.rubric(ws)
    if not text.strip():
        raise util.JobPilotError("profile/rubric.md is empty; run /jobpilot:setup first")
    template = (PROMPTS / "jd-review.md").read_text(encoding="utf-8")
    return (template.replace("{tracks}", tracks_text(ws.cfg)).replace("{eligibility}", eligibility_text(ws.cfg))
            .replace("{profile}", text.strip()))


def output_schema(cfg, n):
    quotes = S.array(S.string(8, 600), 6)
    comp = lambda top: S.obj({"score": {"type": "number", "minimum": 0, "maximum": top},  # noqa: E731
                              "reason": S.string(8, 800), "jd_evidence": quotes, "profile_evidence": quotes})
    assessment = S.obj({
        "category": {"type": "string", "enum": config.categories(cfg)},
        "gaps": S.array(S.string(1, 400), 10),
        "components": S.obj({k: comp(v) for k, v in scoring.COMPONENTS.items()}),
        "fit_cap": S.obj({"score": {"type": "number", "enum": list(scoring.CAPS)}, "reason": S.string(8, 800),
                          "jd_evidence": quotes}),
    }, required=["category", "gaps", "components"])
    review = S.obj({
        "listing_id": S.string(1, 80),
        "include": {"type": "boolean"},
        "track": {"type": "string", "enum": config.track_ids(cfg) + ["none"]},
        "reason": S.string(8, 2000),
        "evidence": S.array(S.string(8, 600), 12),
        "intermediary": {"type": "boolean"},
        "end_employer": S.string(0, 200),
        "careers_url": S.string(0, 500),
        "eligibility_flags": S.array({"type": "string", "enum": list(scoring.FLAGS)}, 8),
        "injection_suspected": {"type": "boolean"},
        "assessment": assessment,
    }, required=["listing_id", "include", "track", "reason", "evidence", "intermediary", "end_employer",
                 "careers_url", "eligibility_flags", "injection_suspected"])
    return S.obj({"reviews": S.array(review, n, 1)})


def packet(ws, row, max_chars):
    jd = search.read_jd(ws, row["id"]).strip()
    meta = ["title: %s" % row.get("title"), "company: %s" % row.get("company"), "location: %s" % row.get("location")]
    for key, label in (("work_arrangement", "work arrangement"), ("employment_type", "employment type"),
                       ("seniority", "seniority"), ("experience_level", "experience (years, provider estimate)"),
                       ("salary", "salary"), ("industry", "industry"), ("posted_at", "posted")):
        if row.get(key):
            meta.append("%s: %s" % (label, row[key]))
    if row.get("agency"):
        meta.append("publisher flagged as a recruitment agency")
    body = "\n".join(meta) + "\n\n" + util.clip(jd, max_chars)
    return claude.untrusted("jd", body, listing_id=row["id"])


# ---------------------------------------------------------------- stage 1

def coarse(ws, log=print):
    """Triage every new posting and reuse reviews of identical reposts. Returns counts."""
    db, cfg = ws.db, ws.cfg
    _, rsha = scoring.rubric(ws)
    reviewed = {}
    for row in db.find("stage='reviewed'"):
        key = triage.reuse_key(row)
        if key and row.get("review", {}).get("rubric_sha") == rsha:
            reviewed.setdefault(key, row)
    counts = {"dropped": 0, "reused": 0, "pending": 0}
    changed = []
    for row in db.find("stage='new'"):
        jd = search.read_jd(ws, row["id"])
        hit = triage.drop(row, jd, cfg)
        if hit:
            row.update(stage="dropped", status="dropped", triage={"rule": hit[0], "detail": hit[1],
                                                                   "version": triage.RULES_VERSION})
            counts["dropped"] += 1
            changed.append(row)
            continue
        src = reviewed.get(triage.reuse_key(row))
        if src and src["id"] != row["id"]:
            row.update(stage="reviewed", review=dict(src["review"], reused_from=src["id"]), track=src.get("track"),
                       score=src.get("score"))
            status, note = scoring.status_for(src["review"], cfg)
            row.update(status=status, status_note="same posting as %s; %s" % (src["id"], note))
            counts["reused"] += 1
            changed.append(row)
            continue
        counts["pending"] += 1
    if changed:
        db.put_many(changed)
    if counts["dropped"] or counts["reused"]:
        log("coarse filter: %(dropped)d dropped, %(reused)d reposts reused, %(pending)d to review" % counts)
    return counts


# ---------------------------------------------------------------- stage 2

def backlog(ws, rescore=True):
    """Postings waiting for the AI review: new ones first (by track preference, newest first), then stale scores."""
    db, cfg = ws.db, ws.cfg
    max_attempts = int(cfg["review"].get("max_attempts") or 3)
    rows = [r for r in db.find("stage='new'") if int(r.get("review_attempts") or 0) < max_attempts]
    tiers = {t: config.tier(cfg, t) for t in config.track_ids(cfg)}
    rows.sort(key=lambda r: (min([tiers.get(t, 99) for t in r.get("hint_tracks") or []] or [99]),
                             -(util.parse_time(r.get("posted_at")) or util.parse_time("1970-01-01")).timestamp()))
    if rescore:
        _, rsha = scoring.rubric(ws)
        stale = [r for r in db.find("stage='reviewed' AND status IN ('ready','hold','below_threshold','stale')")
                 if (r.get("review") or {}).get("rubric_sha") != rsha and not (r.get("review") or {}).get("reused_from")]
        stale.sort(key=lambda r: -(r.get("score") or 0))
        rows += stale[: int(cfg["review"].get("rescore_per_run") or 40)]
    return rows


def apply_review(ws, row, raw, result, model):
    _, rsha = scoring.rubric(ws)
    rec = dict(result, rubric_sha=rsha, jd_sha=row.get("jd_sha"), model=model, reviewed_at=util.stamp(ws.now()))
    row.update(review=rec, stage="reviewed", track=result.get("track"), score=result.get("score"))
    row.pop("review_error", None)
    ws.db.set_review(row["id"], {"raw": raw, "checked": rec})
    if row.get("status") in CLOSED:
        return row  # an applied or skipped posting keeps its status; only its score is refreshed
    row["status"], row["status_note"] = scoring.status_for(result, ws.cfg)
    return row


def run(ws, *, max_batches=None, ids=None, rescore=True, log=print):
    cfg = ws.cfg
    errors = [m for level, m in config.check(cfg, ws) if level == "error" and "search" not in m and "group" not in m]
    if errors:
        raise util.JobPilotError("fix the setup first:\n  - " + "\n  - ".join(errors))
    summary = {"coarse": coarse(ws, log=log), "reviewed": 0, "ready": 0, "excluded": 0, "below": 0, "hold": 0,
               "failed": 0, "batches": 0, "cost_usd": 0.0, "errors": []}
    rows = backlog(ws, rescore=rescore)
    if ids:
        wanted = set(map(str, ids))
        rows = [r for r in ws.db.find("1=1") if r["id"] in wanted]
    if not rows:
        log("nothing to review")
        return summary
    rc = cfg["review"]
    size = max(1, min(10, int(rc.get("batch_size") or 8)))
    limit = int(max_batches if max_batches is not None else rc.get("max_batches") or 25)
    system = system_prompt(ws)
    profile_text, _ = scoring.rubric(ws)
    item_schema = output_schema(cfg, 1)["properties"]["reviews"]["items"]
    with ws.lock("review", wait=False):
        for start in range(0, len(rows), size):
            if summary["batches"] >= limit:
                log("batch limit reached (%d); the rest waits for the next run" % limit)
                break
            batch = rows[start:start + size]
            task = ("Review these %d job postings for the candidate (one entry per listing_id).\n\n" % len(batch) +
                    "\n\n".join(packet(ws, r, int(rc.get("max_jd_chars") or 14000)) for r in batch))
            res = claude.run(ws, "jd-review", task, system=system, schema=output_schema(cfg, len(batch)),
                             model=rc.get("model") or "sonnet", effort=rc.get("effort") or "",
                             timeout=int(rc.get("timeout_seconds") or 600))
            summary["batches"] += 1
            summary["cost_usd"] = round(summary["cost_usd"] + (res.cost_usd or 0), 4)
            by_id, bad = {}, {}
            if not res.ok:
                summary["errors"].append(res.error)
                log("review batch: %s" % res.error)
                if res.error.startswith(("claude_not_found", "launch_failed")):
                    break
            # One malformed entry must not cost the whole batch: each entry is checked against the entry schema.
            entries = res.output.get("reviews") if isinstance(res.output, dict) else None
            for item in entries if isinstance(entries, list) else []:
                pid = str(item.get("listing_id")) if isinstance(item, dict) else ""
                problems = S.validate(item, item_schema)
                if problems:
                    bad[pid] = "; ".join(problems[:3])
                else:
                    by_id.setdefault(pid, item)
            updated = []
            for row in batch:
                item = by_id.get(row["id"])
                if item is None:
                    row["review_attempts"] = int(row.get("review_attempts") or 0) + 1
                    row["review_error"] = bad.get(row["id"]) or res.error or "missing from the answer"
                    if row["review_attempts"] >= int(rc.get("max_attempts") or 3) and row.get("stage") == "new":
                        row.update(stage="failed", status="pending")
                        summary["failed"] += 1
                    updated.append(row)
                    continue
                try:
                    result = scoring.validate(item, search.read_jd(ws, row["id"]), profile_text, cfg)
                except scoring.Invalid as exc:
                    row["review_attempts"] = int(row.get("review_attempts") or 0) + 1
                    row["review_error"] = "rejected: %s" % exc
                    if row["review_attempts"] >= int(rc.get("max_attempts") or 3) and row.get("stage") == "new":
                        row.update(stage="failed", status="pending")
                        summary["failed"] += 1
                    updated.append(row)
                    continue
                apply_review(ws, row, item, result, rc.get("model") or "sonnet")
                summary["reviewed"] += 1
                key = {"ready": "ready", "excluded": "excluded", "below_threshold": "below", "hold": "hold"}.get(
                    row["status"])
                if key:
                    summary[key] += 1
                updated.append(row)
            ws.db.put_many(updated)
            log("batch %d: %d reviewed (%d ready)" % (summary["batches"], sum(1 for r in batch if r["id"] in by_id),
                                                       sum(1 for r in batch if r.get("status") == "ready")))
    if summary["reviewed"]:
        ws.event("review", "reviewed %d postings: %d ready, %d below threshold, %d excluded" % (
            summary["reviewed"], summary["ready"], summary["below"], summary["excluded"]),
            ready=summary["ready"], cost=summary["cost_usd"])
    return summary
