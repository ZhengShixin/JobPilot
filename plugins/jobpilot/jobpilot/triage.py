"""Coarse filter: free, deterministic rules that drop postings the candidate can never take, before any AI review.

    drop(row, jd, cfg) -> (rule, detail) or None

Rules (the first that fires wins; the rule and its evidence are stored on the row and shown in the dashboard's
"Filtered out" list, so a wrong drop is visible and the rule can be loosened):
  employment   an excluded employment type (internship, part-time, temporary ...) or an intern/co-op title
  vacancy      the JD says there is no current opening (talent pools)
  citizenship  U.S. citizenship explicitly required (only when the candidate is not a citizen)
  sponsorship  the JD says it will not sponsor visas (only when the candidate needs sponsorship)
  salary       the advertised annual maximum is below candidate.min_salary_usd
  experience   the provider's experience band is one the config drops (e.g. "10+" for an entry-level search)
  company      an excluded company, a recruitment agency (skip_agencies) or an AI-training gig platform
  title        a title word the config excludes, unless the title also has a protected word
Each rule only removes what the AI review would reject anyway; it saves money, never changes a decision.
"""
import re

from . import config, util

RULES_VERSION = "1"

INTERN_TITLE = re.compile(r"\b(intern|interns|internship|internships|co-?op|apprentice\w*|summer (?:analyst|associate|"
                          r"intern\w*|student|program))\b", re.I)
NO_VACANCY = re.compile(r"not for a current (?:vacancy|opening)|no current (?:vacancy|opening)|not an active (?:job )?"
                        r"opening|(?:this is an?|for our) (?:evergreen|pipeline|talent (?:pool|community)) (?:posting|"
                        r"requisition|role)", re.I)
CITIZEN = re.compile(r"(?:must be|required to be|requires?|only|limited to)[^.\n]{0,40}(?:a U\.?S\.? citizen|U\.?S\.? "
                     r"citizenship|United States citizenship)", re.I)
CITIZEN_OK = re.compile(r"or[^.\n]{0,60}(?:legal authorization|authorized to work|permanent resident|green card)|"
                        r"citizenship[^.\n]{0,40}not (?:a )?require", re.I)
NO_SPONSOR = re.compile(r"(?:unable|not able|cannot|can ?not|can't|will not|won't|do(?:es)? not|are not able to|is not "
                        r"able to|no)\s+(?:to\s+)?(?:currently\s+)?(?:provide|offer|support|consider)?\s*(?:any\s+)?"
                        r"(?:employment\s+)?(?:visa\s+|immigration\s+|h-?1b\s+)?sponsor(?:ship)?|without (?:the need "
                        r"for |requiring )?(?:current or future |now or in the future )?(?:visa |employer )?sponsorship|"
                        r"sponsorship (?:is )?(?:not available|not offered|not provided|unavailable)", re.I)
# Platforms whose "research" or "engineer" listings are mostly paid AI-training work (rating or writing model data).
TRAINING_GIGS = re.compile(r"\b(Mercor|Outlier|Alignerr|DataAnnotation|micro1|Remotasks|Toloka|Appen|TELUS Digital|"
                           r"Invisible Technologies|Handshake AI|Synthires|AfterQuery|crewlia|Turing)\b", re.I)


def _words(words):
    """A regex for whole words/phrases ('x:*' = prefix), or None."""
    parts = []
    for w in words or []:
        w = str(w).strip()
        if not w:
            continue
        if w.endswith(":*"):
            parts.append(r"\b" + re.escape(w[:-2]))
        else:
            parts.append(r"\b" + re.escape(w) + r"\b")
    return re.compile("|".join(parts), re.I) if parts else None


def employment_drop(row, cfg):
    cand, f = cfg["candidate"], cfg["filters"]
    excluded = {str(x).upper() for x in f.get("exclude_employment_types") or []}
    if cand.get("open_to_internships"):
        excluded.discard("INTERN")
    if cand.get("open_to_contract"):
        excluded -= {"CONTRACTOR", "TEMPORARY"}
    types = {t.strip().upper() for t in str(row.get("employment_type") or "").split("|") if t.strip()}
    if types and types <= excluded:
        return "employment", "employment type " + "/".join(sorted(types))
    if not cand.get("open_to_internships"):
        m = INTERN_TITLE.search(row.get("title") or "")
        if m:
            return "employment", "title says %r" % m.group(0)
    return None


def _evidence(text, m, width=90):
    a, b = max(0, m.start() - width // 2), min(len(text), m.end() + width // 2)
    return util.clip(re.sub(r"\s+", " ", text[a:b]).strip(), width + 20)


def drop(row, jd, cfg):
    found = employment_drop(row, cfg)
    if found:
        return found
    jd = jd or ""
    m = NO_VACANCY.search(jd)
    if m:
        return "vacancy", _evidence(jd, m)
    if config.drop_citizenship_required(cfg):
        lines = [ln for ln in jd.splitlines() if not CITIZEN_OK.search(ln)]
        m = CITIZEN.search("\n".join(lines))
        if m:
            return "citizenship", m.group(0)[:160]
    if config.needs_sponsorship(cfg):
        m = NO_SPONSOR.search(jd)
        if m:
            return "sponsorship", _evidence(jd, m)
    floor = float(cfg["candidate"].get("min_salary_usd") or 0)
    top = row.get("salary_max_usd")
    if floor and isinstance(top, (int, float)) and 0 < top < floor:
        return "salary", "advertised maximum $%s < $%s" % ("{:,.0f}".format(top), "{:,.0f}".format(floor))
    f = cfg["filters"]
    level = str(row.get("experience_level") or "")
    if level and level in {str(x) for x in f.get("drop_experience_levels") or []}:
        return "experience", "experience band %s years" % level
    ck = util.company_key(row.get("company"))
    if ck and ck in {util.company_key(c) for c in f.get("exclude_companies") or []}:
        return "company", "excluded company %s" % row.get("company")
    if f.get("skip_agencies") and row.get("agency"):
        return "company", "recruitment agency %s" % row.get("company")
    if f.get("skip_training_gigs", True):
        m = TRAINING_GIGS.search(row.get("company") or "")
        if m:
            return "company", "AI-training gig platform %s" % m.group(0)
    title = row.get("title") or ""
    bad, keep = _words(f.get("exclude_title_words")), _words(f.get("protect_title_words"))
    if bad:
        m = bad.search(title)
        if m and not (keep and keep.search(title)):
            return "title", "title word %r" % m.group(0)
    return None


def reuse_key(row):
    """Postings with the same employer, title and JD text share one review (a repost, another city)."""
    if not row.get("jd_sha") or not row.get("jd_chars"):
        return None
    return "%s|%s|%s" % (util.company_key(row.get("company")), util.norm_title(row.get("title")), row["jd_sha"])
