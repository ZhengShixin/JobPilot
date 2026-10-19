"""Evidence-bound fit scores. The AI proposes; this module checks and computes, and never invents a score.

A review is accepted only if every quoted JD excerpt really occurs in the job description, every profile excerpt
really occurs in the scoring profile (profile/rubric.md), every component is in range and the category belongs to the
chosen track. The score is computed here: min(fit cap, duties_4 + background_3 + skills_2 + seniority_1).
Scores are keyed to the sha256 of rubric.md: editing the rubric marks older scores stale until they are rescored.
"""
import math

from . import config, util

COMPONENTS = {"duties_4": 4.0, "background_3": 3.0, "skills_2": 2.0, "seniority_1": 1.0}
CAPS = (5.5, 6.5, 7.5)
FLAGS = ("citizenship_required", "clearance_required", "no_sponsorship", "non_full_time", "enrollment_required",
         "graduation_window", "language_required", "location_mismatch", "experience_mismatch",
         "no_current_vacancy", "other")


class Invalid(ValueError):
    pass


def rubric(ws):
    """(text, sha256) of profile/rubric.md."""
    try:
        text = ws.rubric_md.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "", ""
    return text, util.sha256_text(text)


def hard_flags(cfg):
    """Eligibility flags that exclude a posting for this candidate, whatever the reviewer's include says."""
    out = {"no_current_vacancy", "enrollment_required", "graduation_window"}
    if config.drop_citizenship_required(cfg):
        out |= {"citizenship_required"}
    if config.needs_sponsorship(cfg):
        out |= {"no_sponsorship"}
    if not cfg["candidate"].get("open_to_internships") and not cfg["candidate"].get("open_to_contract"):
        out |= {"non_full_time"}
    return out


def _quotes(quotes, haystack, what):
    for q in quotes or []:
        if not util.quote_in(q, haystack):
            raise Invalid("%s quote not found verbatim: %s" % (what, util.clip(q, 80)))


def validate(review, jd, profile_text, cfg):
    """A checked score record for one review, or raises Invalid. `review` is one entry of the jd-review output."""
    track = review.get("track")
    if review.get("include"):
        if track not in config.track_ids(cfg):
            raise Invalid("included with unknown track %r" % track)
    elif track not in config.track_ids(cfg) + ["none"]:
        raise Invalid("unknown track %r" % track)
    jd_norm = util.evidence_norm(jd)
    _quotes(review.get("evidence"), jd_norm, "evidence")
    out = {"include": bool(review.get("include")), "track": track, "reason": review.get("reason", ""),
           "evidence": list(review.get("evidence") or []), "flags": list(review.get("eligibility_flags") or []),
           "intermediary": bool(review.get("intermediary")), "end_employer": review.get("end_employer") or "",
           "careers_url": review.get("careers_url") or "", "injection": bool(review.get("injection_suspected"))}
    if not out["include"]:
        return out
    a = review.get("assessment")
    if not isinstance(a, dict):
        raise Invalid("an included posting needs an assessment")
    comps = a.get("components") or {}
    if set(comps) != set(COMPONENTS):
        raise Invalid("all four score components are required")
    prof_norm = util.evidence_norm(profile_text)
    values = {}
    for key, top in COMPONENTS.items():
        part = comps[key]
        v = part.get("score")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= top:
            raise Invalid("%s out of range" % key)
        if not (part.get("reason") or "").strip():
            raise Invalid("%s needs a reason" % key)
        if v > 0 and not part.get("jd_evidence"):
            raise Invalid("%s > 0 needs JD evidence" % key)
        _quotes(part.get("jd_evidence"), jd_norm, key + " JD")
        if key != "duties_4":
            if not part.get("profile_evidence"):
                raise Invalid("%s needs profile evidence" % key)
            _quotes(part.get("profile_evidence"), prof_norm, key + " profile")
        values[key] = float(v)
    category = a.get("category")
    if category not in config.categories(cfg, track):
        raise Invalid("category %r is not one of track %s's categories" % (category, track))
    if not isinstance(a.get("gaps"), list):
        raise Invalid("gaps must be a list")
    cap, cap_reason = 10.0, ""
    fc = a.get("fit_cap")
    if fc:
        cap = fc.get("score")
        if cap not in CAPS:
            raise Invalid("unsupported fit cap %r" % cap)
        if not fc.get("reason") or not fc.get("jd_evidence"):
            raise Invalid("a fit cap needs a reason and JD evidence")
        _quotes(fc.get("jd_evidence"), jd_norm, "fit cap")
        cap_reason = fc["reason"]
    total = round(min(cap, sum(values.values())), 1)
    out.update(score=total, components=values, category=category, gaps=[str(g) for g in a["gaps"]][:10],
               cap=cap if cap < 10 else None, cap_reason=cap_reason,
               reasons={k: comps[k].get("reason", "") for k in COMPONENTS},
               jd_quotes={k: list(comps[k].get("jd_evidence") or []) for k in COMPONENTS},
               profile_quotes={k: list(comps[k].get("profile_evidence") or []) for k in COMPONENTS})
    return out


def status_for(result, cfg):
    """(status, note) for a checked review."""
    hard = sorted(set(result.get("flags") or []) & hard_flags(cfg))
    if result.get("injection"):
        return "hold", "the posting looked like it carried instructions for the AI (prompt injection); check it by hand"
    if not result["include"]:
        return "excluded", util.clip(result.get("reason") or "", 300)
    if hard:
        return "excluded", "not eligible: " + ", ".join(hard)
    if result["score"] < config.min_score(cfg, result["track"]):
        return "below_threshold", "score %.1f < %.1f" % (result["score"], config.min_score(cfg, result["track"]))
    return "ready", ""
