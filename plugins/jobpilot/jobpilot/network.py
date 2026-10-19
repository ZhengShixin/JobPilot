"""Alumni referral finder: people at a target company who share one of your schools, scored for how useful a first
conversation would be, with outreach drafts that you send yourself.

    find(ws, company)    Apify (harvestapi/linkedin-company-employees): the company's people in your locations who
                         studied at one of your schools (candidate.schools) -> data/network/people.json, then a free
                         rule pass: at the company now, in the target country, shares a school, not a student or an
                         intern; recruiters are kept aside as contacts (often the best first contact for new grads)
    score(ws, company)   role net-review (headless Claude, no tools): five 0-5 sub-scores, each with a verbatim quote
                         from the profile that the code checks; total 0-100
    cards(ws, company)   role net-writer: two connection notes, a message, a follow-up and coffee-chat questions,
                         checked for length, sensitive topics, invented numbers, links and placeholders
    mark(ws, pid, event) what you did on LinkedIn (connect_sent, accepted, replied ...); `undo` takes the last one back

Read this before using it: collecting LinkedIn profiles through a third-party scraper (an Apify actor) may violate
LinkedIn's User Agreement, and the people found never agreed to be on your list. You decide whether to use this
module. JobPilot never logs into LinkedIn, never sends invitations or messages, never looks up email addresses, and
keeps everything it finds in your workspace (data/network/), never uploaded anywhere.
"""
import math
import re
import time
import unicodedata
from pathlib import Path

from . import budget, claude, config, ledger, schema as S, util

PROMPTS = Path(__file__).resolve().parent / "prompts"
SUBS = ("role", "alumni", "path", "reply", "help")
WEIGHTS = {"role": 0.30, "alumni": 0.25, "path": 0.15, "reply": 0.15, "help": 0.15}
STAGES = ("new", "card_ready", "connect_sent", "accepted", "messaged", "replied", "chat_scheduled", "chat_done",
          "referred", "declined", "no_response", "dropped")
EVENT_STAGE = {"connect_sent": "connect_sent", "accepted": "accepted", "message_sent": "messaged",
               "replied": "replied", "chat_scheduled": "chat_scheduled", "chat_done": "chat_done",
               "referred": "referred", "declined": "declined", "no_response": "no_response", "dropped": "dropped"}
MARKS = tuple(EVENT_STAGE) + ("undo",)
TRIAGE_ORDER = {"kept": 0, "recruiter": 1, "skip": 2}
START_FEE = 0.02
PRICE = {"short": 0.004, "full": 0.008}
MIN_CHARGE = 0.05            # the actor's minimal maxTotalChargeUsd
MIN_PROFILES = 5
MAX_PROFILES = 100
MAX_ATTEMPTS = 3
COUNTRIES = {"united states": "US", "united states of america": "US", "usa": "US", "canada": "CA",
             "united kingdom": "GB", "ireland": "IE", "germany": "DE", "france": "FR", "netherlands": "NL",
             "switzerland": "CH", "singapore": "SG", "hong kong": "HK", "china": "CN", "japan": "JP", "india": "IN",
             "australia": "AU", "israel": "IL", "spain": "ES", "italy": "IT", "sweden": "SE", "south korea": "KR"}
INTERN = re.compile(r"(?i)\b(intern|interns|internship|co-?op|student|apprentice\w*)\b")
RECRUITER = re.compile(r"(?i)\b(recruit\w*|talent acquisition|talent partner|talent sourcer|sourcer|human resources|"
                       r"hr|people partner|people operations|university relations|campus relations|early careers?)\b")
SKIP_FUNCTION = re.compile(r"(?i)\b(legal|counsel|attorney|paralegal|facilities|receptionist|office manager|"
                           r"executive assistant|administrative assistant|sales|account executive|business development|"
                           r"marketing|public relations|communications|procurement|real estate|payroll|tax|audit|"
                           r"accounting|customer service|customer support)\b")
SEPARATOR = re.compile(r"\s+[-–—|]\s+|\s*\(")
SENSITIVE = re.compile(r"(?i)\b(visas?|sponsor\w*|green card|citizen\w*|work authori[sz]ation|immigration|h-?1b|opt|"
                       r"cpt|salary|salaries|compensation)\b")
LINKS = re.compile(r"(?i)https?://|www\.|linkedin\.com/")
PLACEHOLDER = re.compile(r"\[[^\]\n]{1,40}\]|\{\{|\}\}|<[A-Za-z][^>\n]{0,40}>|\bX{3,}\b|\bTBD\b|\bTODO\b|lorem ipsum", re.I)
FAMILIAR = re.compile(r"(?i)\b(as we discussed|as discussed|we met|great (?:meeting|to see) you again|nice to see you "
                      r"again|i (?:have )?read your (?:paper|thesis|work|publication|article|post)s?)\b")
CJK = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯]")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}


# ---------------------------------------------------------------- storage

def _dir(ws):
    d = ws.network
    d.mkdir(parents=True, exist_ok=True)
    return d


def _people_path(ws):
    return _dir(ws) / "people.json"


def _companies_path(ws):
    return _dir(ws) / "companies.json"


def _card_path(ws, pid):
    return _dir(ws) / "cards" / ("%s.json" % pid)


def _load_people(ws):
    return util.read_json(_people_path(ws), {}) or {}


def _load_companies(ws):
    return util.read_json(_companies_path(ws), {}) or {}


def _save_people(ws, people):
    util.atomic_write_json(_people_path(ws), people, mode=0o600)


def _save_companies(ws, companies):
    util.atomic_write_json(_companies_path(ws), companies, mode=0o600)


def _update_people(ws, updates):
    """Apply {pid: {field: value}} under the network lock, re-reading the file so concurrent marks are never lost."""
    with ws.lock("network"):
        people = _load_people(ws)
        for pid, fields in updates.items():
            if pid in people:
                people[pid].update(fields)
                people[pid]["updated_at"] = util.stamp(ws.now())
        _save_people(ws, people)
        return people


def card(ws, pid):
    return util.read_json(_card_path(ws, pid))


def _cfg(ws):
    return ws.cfg.get("referrals") or {}


def weights(ws):
    w = dict(WEIGHTS)
    w.update({k: float(v) for k, v in (_cfg(ws).get("weights") or {}).items() if k in WEIGHTS})
    return w


def note_limit(ws):
    r = _cfg(ws)
    if str(r.get("linkedin_plan") or "free").lower() == "premium":
        return int(r.get("note_limit_premium") or 300)
    return int(r.get("note_limit_free") or 200)


def sign_name(ws):
    r, cand = _cfg(ws), ws.cfg.get("candidate") or {}
    return (r.get("sign_name") or (cand.get("name") or "").split(" ")[0] or "").strip()


def _language_name(ws):
    return "Simplified Chinese" if config.language(ws.cfg) == "zh" else "English"


# ---------------------------------------------------------------- profiles

def _month(m):
    if isinstance(m, int):
        return m if 1 <= m <= 12 else None
    if isinstance(m, str):
        if m.isdigit():
            return int(m) if 1 <= int(m) <= 12 else None
        return MONTHS.get(m[:3].lower())
    return None


def ym(d):
    """'2026-07', '2026' or '' from an Apify date ({year, month}) or a date string."""
    if isinstance(d, dict) and d.get("year"):
        m = _month(d.get("month"))
        return "%d-%02d" % (int(d["year"]), m) if m else str(int(d["year"]))
    if isinstance(d, str):
        m = re.match(r"(\d{4})(?:-(\d{1,2}))?", d.strip())
        if m:
            return "%s-%02d" % (m.group(1), int(m.group(2))) if m.group(2) else m.group(1)
    return ""


def _slug(url, kind="company"):
    m = re.search(r"linkedin\.com/%s/([^/?#]+)" % kind, url or "", re.I)
    return m.group(1).lower() if m else ""


def _position(p):
    end = ym(p.get("endDate"))
    return {"title": (p.get("position") or p.get("title") or "").strip(),
            "company": (p.get("companyName") or "").strip(),
            "company_slug": (p.get("companyUniversalName") or _slug(p.get("companyLinkedinUrl"))).lower(),
            "start": ym(p.get("startDate")), "end": end or ("" if p.get("endDate") else "present"),
            "duration": (p.get("duration") or "").strip()}


def whitelist(item):
    """Only the fields this module uses; everything else (photos, about, skills, emails, counts) is dropped."""
    loc = item.get("location") or {}
    if isinstance(loc, str):
        loc = {"linkedinText": loc}
    parsed = loc.get("parsed") or {}
    edu = [{"school": (e.get("schoolName") or "").strip(), "degree": (e.get("degree") or "").strip(),
            "field": (e.get("fieldOfStudy") or "").strip(), "start": ym(e.get("startDate")), "end": ym(e.get("endDate"))}
           for e in item.get("education") or [] if isinstance(e, dict)]
    first, last = (item.get("firstName") or "").strip(), (item.get("lastName") or "").strip()
    url = (item.get("linkedinUrl") or "").strip().split("?")[0].rstrip("/")
    return {"first_name": first, "last_name": last, "name": " ".join(x for x in (first, last) if x),
            "linkedin_url": url, "public_id": (item.get("publicIdentifier") or _slug(url, "in")).lower(),
            "headline": (item.get("headline") or "").strip(),
            "location": (loc.get("linkedinText") or parsed.get("text") or "").strip(),
            "country": (parsed.get("countryCode") or loc.get("countryCode") or "").upper(),
            "current": [_position(p) for p in item.get("currentPosition") or [] if isinstance(p, dict)],
            "experience": [_position(p) for p in item.get("experience") or [] if isinstance(p, dict)],
            "education": edu}


def profile_lines(person):
    """The text the scorer and the writer see; every evidence quote must come from these lines."""
    lines = ["name: %s" % person.get("name", ""), "headline: %s" % person.get("headline", ""),
             "location: %s" % person.get("location", "")]
    for c in person.get("current") or []:
        lines.append("now: %s @ %s since %s (%s)" % (c.get("title", ""), c.get("company", ""), c.get("start") or "?",
                                                     c.get("duration") or "?"))
    for e in person.get("education") or []:
        lines.append("education: %s | %s | %s | %s-%s" % (e.get("school", ""), e.get("degree", ""), e.get("field", ""),
                                                          e.get("start", ""), e.get("end", "")))
    for x in (person.get("experience") or [])[:10]:
        lines.append("experience: %s @ %s | %s-%s" % (x.get("title", ""), x.get("company", ""), x.get("start", ""),
                                                      x.get("end", "")))
    return lines


def _profile_sha(person):
    return util.sha256_text("\n".join(profile_lines(person)))[:16]


# ---------------------------------------------------------------- triage

def norm_school(name):
    s = unicodedata.normalize("NFKC", name or "").casefold().replace("&", " and ")
    s = re.sub(r"[.,'’`]", "", s)
    s = re.sub(r"[^\w]+", " ", s).strip()
    s = re.sub(r"^the ", "", s)
    return re.sub(r"\s+", " ", s)


def school_names(schools, aliases=None):
    """{normalized name or alias: the candidate's school as written in the config}."""
    out = {}
    for school in schools or []:
        if str(school).strip():
            out[norm_school(school)] = school
    for school, alts in (aliases or {}).items():
        canon = next((s for s in schools or [] if norm_school(s) == norm_school(school)), school)
        for alt in [school] + list(alts if isinstance(alts, (list, tuple)) else [alts]):
            if str(alt).strip():
                out[norm_school(alt)] = canon
    return out


def school_match(name, names):
    """The candidate school a school name stands for, or None. A whole-name match, or the part before a sub-school
    separator (' - ', ' | ', '('): "Lakeside State University - School of Engineering" matches "Lakeside State
    University", but "Lakeside State University Online" and "Lakeside State University, Eastport" (another campus) do
    not; add those under referrals.school_aliases if they count for you."""
    full = norm_school(name)
    if full in names:
        return names[full]
    base = norm_school(SEPARATOR.split(name or "", 1)[0])
    return names.get(base) if base else None


def matched_schools(person, names):
    """(schools from degrees, schools the person only worked at)."""
    degree = []
    for e in person.get("education") or []:
        hit = school_match(e.get("school"), names)
        if hit and hit not in degree:
            degree.append(hit)
    worked = []
    for x in (person.get("experience") or []) + (person.get("current") or []):
        hit = school_match(x.get("company"), names)
        if hit and hit not in degree and hit not in worked:
            worked.append(hit)
    return degree, worked


def _end_key(end):
    if not end:
        return ""
    return end + "-06" if len(end) == 4 else end[:7]   # a year alone: assume a June graduation


def _months_between(start, today_ym):
    try:
        y0, m0 = (int(x) for x in (start + "-01")[:7].split("-"))
        y1, m1 = (int(x) for x in today_ym[:7].split("-"))
    except ValueError:
        return None
    return (y1 - y0) * 12 + (m1 - m0)


def at_company(person, target):
    """The person's current position at the target ({key, slug}) or None."""
    for c in person.get("current") or []:
        if target.get("slug") and c.get("company_slug") == target["slug"]:
            return c
        if target.get("key") and util.company_key(c.get("company")) == target["key"]:
            return c
    return None


def allowed_countries(locations):
    """Country codes for the configured locations, or None when one of them is not a country we know (the Apify
    location filter then stands alone)."""
    codes = set()
    for loc in locations or []:
        code = COUNTRIES.get(str(loc).strip().casefold())
        if not code:
            return None
        codes.add(code)
    return codes or None


def _track_words(cfg):
    return " ".join("%s %s" % (t.get("name", ""), t.get("description", "")) for t in config.tracks(cfg)).casefold()


def triage(person, target, schools, today, aliases=None, locations=None, track_words=""):
    """(kept | recruiter | skip, reason). `schools` are the candidate's schools, `today` an ISO date."""
    today_ym = str(today)[:7]
    pos = at_company(person, target)
    if not pos:
        return "skip", "not at %s now" % (target.get("name") or target.get("key"))
    codes = allowed_countries(locations)
    if codes and person.get("country") and person["country"] not in codes:
        return "skip", "outside %s" % ", ".join(locations)
    degree, worked = matched_schools(person, school_names(schools, aliases))
    if not degree and not worked:
        return "skip", "no school in common"
    title = pos.get("title") or person.get("headline") or ""
    if INTERN.search(title):
        return "skip", "intern or student"
    studying = [e for e in person.get("education") or [] if _end_key(e.get("end")) > today_ym]
    tenure = _months_between(pos.get("start") or "", today_ym)
    if studying and (tenure is None or tenure < 12):
        return "skip", "still studying (%s, until %s)" % (studying[0].get("school") or "?", studying[0].get("end"))
    if RECRUITER.search(title):
        return "recruiter", "recruiting or university relations"
    hits = [m.group(0) for m in SKIP_FUNCTION.finditer(title)]
    if hits and not any(h.casefold() in track_words for h in hits):   # a track about that function keeps them
        return "skip", "unrelated function: %s" % hits[0]
    return "kept", "" if degree else "worked at %s (no degree there)" % ", ".join(worked)


def _triage_all(ws, people, target):
    cfg = ws.cfg
    r = cfg.get("referrals") or {}
    schools = cfg["candidate"].get("schools") or []
    names = school_names(schools, r.get("school_aliases"))
    words = _track_words(cfg)
    for p in people:
        status, reason = triage(p, target, schools, ws.today(), r.get("school_aliases"), r.get("locations"), words)
        degree, worked = matched_schools(p, names)
        pos = at_company(p, target) or {}
        p.update(triage=status, triage_reason=reason, schools=degree + [w for w in worked if w not in degree],
                 school_link="degree" if degree else ("worked" if worked else ""),
                 title=pos.get("title") or p.get("title") or "")


# ---------------------------------------------------------------- companies

def _target_from_url(url):
    m = re.search(r"linkedin\.com/company/([^/?#]+)", url or "", re.I)
    if not m:
        raise util.JobPilotError("not a LinkedIn company URL: %s (expected https://www.linkedin.com/company/<name>)" % url)
    slug = m.group(1).lower()
    return slug, "https://www.linkedin.com/company/%s" % slug


def resolve(ws, company, url=None):
    """{key, name, slug, url} for a company name or URL: an explicit URL, a known company, or a pool posting of that
    company (the search stores each employer's LinkedIn page)."""
    if not url and re.search(r"linkedin\.com/company/", company or "", re.I):
        url, company = company, ""
    companies = _load_companies(ws)
    if url:
        slug, url = _target_from_url(url)
        name = (company or "").strip()
        if not name:
            known = next((c for c in companies.values() if c.get("slug") == slug), None)
            rows = ws.db.find("1=1") if not known else []
            row = next((r for r in rows if (r.get("company_slug") or "").lower() == slug), None)
            name = (known or {}).get("name") or (row or {}).get("company") or slug.replace("-", " ").title()
        return {"key": util.company_key(name), "name": name, "slug": slug, "url": url}
    key = util.company_key(company)
    if not key:
        raise util.JobPilotError("name a company (or pass --url https://www.linkedin.com/company/<name>)")
    known = companies.get(key)
    if known and known.get("url"):
        return {"key": key, "name": known.get("name") or company, "slug": known.get("slug"), "url": known["url"]}
    rows = [r for r in ws.db.find("company_key=?", (key,)) if r.get("company_slug")]
    if rows:
        slug = rows[0]["company_slug"].lower()
        return {"key": key, "name": rows[0].get("company") or company, "slug": slug,
                "url": "https://www.linkedin.com/company/%s" % slug}
    raise util.JobPilotError("no LinkedIn page known for %s. Find the company page on LinkedIn and run: jobpilot net "
                             "find \"%s\" --url https://www.linkedin.com/company/<name>" % (company, company))


def _key_of(ws, company):
    key = util.company_key(company)
    companies = _load_companies(ws)
    if key in companies:
        return key
    if re.search(r"linkedin\.com/company/", company or "", re.I):
        slug = _target_from_url(company)[0]
        for k, c in companies.items():
            if c.get("slug") == slug:
                return k
    for k, c in companies.items():
        if util.company_key(c.get("name")) == key or c.get("slug") == (company or "").lower():
            return k
    raise util.JobPilotError("no network search for %s yet: run `jobpilot net find \"%s\"` first" % (company, company))


def companies(ws, include_removed=True):
    """Every company searched, with counts: people found, kept, recruiters, cards, contacted."""
    people = _load_people(ws)
    out = []
    for key, c in _load_companies(ws).items():
        if c.get("removed") and not include_removed:
            continue
        mine = [p for p in people.values() if p.get("company_key") == key]
        row = dict(c, key=key)
        row["counts"] = {"people": len(mine), "kept": sum(1 for p in mine if p.get("triage") == "kept"),
                         "recruiters": sum(1 for p in mine if p.get("triage") == "recruiter"),
                         "cards": sum(1 for p in mine if p.get("card_at")),
                         "contacted": sum(1 for p in mine if p.get("stage") not in ("new", "card_ready", "dropped"))}
        out.append(row)
    out.sort(key=lambda c: (bool(c.get("removed")), (c.get("name") or "").casefold()))
    return out


def _order(p):
    return (TRIAGE_ORDER.get(p.get("triage"), 3), -(p.get("score") if p.get("score") is not None else -1),
            (p.get("name") or "").casefold())


def people(ws, company_key=None):
    """People best first (kept, then recruiters, then skipped; by score). Without a company, removed companies'
    people are left out."""
    rows = list(_load_people(ws).values())
    if company_key:
        rows = [p for p in rows if p.get("company_key") == company_key]
    else:
        removed = {k for k, c in _load_companies(ws).items() if c.get("removed")}
        rows = [p for p in rows if p.get("company_key") not in removed]
    return sorted(rows, key=_order)


def person(ws, pid):
    p = _load_people(ws).get(pid)
    if p is None:
        return None
    out = dict(p)
    out["card"] = card(ws, pid)
    out["profile_lines"] = profile_lines(p)
    return out


def remove_company(ws, company, reason=""):
    key = _key_of(ws, company)
    with ws.lock("network"):
        cs = _load_companies(ws)
        cs[key].update(removed=True, removed_at=util.stamp(ws.now()), removed_reason=reason or "")
        _save_companies(ws, cs)
    ws.event("net", "removed %s from the network list" % cs[key].get("name"))
    return cs[key]


def restore_company(ws, company):
    key = _key_of(ws, company)
    with ws.lock("network"):
        cs = _load_companies(ws)
        cs[key]["removed"] = False
        _save_companies(ws, cs)
    return cs[key]


# ---------------------------------------------------------------- find

def _client(ws, client):
    if client is not None:
        return client
    from .apify import Apify, load_token
    from .search import paused
    return Apify(load_token(ws), guard=lambda: paused(ws))


def _mode(props, wanted):
    enum = (props.get("profileScraperMode") or {}).get("enum") or []
    if "email" in (wanted or "").lower():
        raise util.JobPilotError("referrals.scraper_mode %r searches email addresses; JobPilot never does that" % wanted)
    if not enum or wanted in enum:
        return wanted
    kind = "short" if (wanted or "").lower().startswith("short") else "full"   # the actor renamed its modes
    for value in enum:
        if value.lower().startswith(kind) and "email" not in value.lower():
            return value
    raise util.JobPilotError("no profile mode like %r in the actor's schema: %s" % (wanted, ", ".join(enum)))


def _price(mode):
    return PRICE["short"] if (mode or "").lower().startswith("short") else PRICE["full"]


def _merge(ws, items, target):
    """Merge raw actor items into people.json. Known people (same public id or URL) keep their pid, stage, marks and
    card; their score is cleared only when their profile changed. Returns (people of this company touched, new)."""
    now = util.stamp(ws.now())
    with ws.lock("network"):
        allp = _load_people(ws)
        index = {}
        for pid, p in allp.items():
            for k in (p.get("public_id"), (p.get("linkedin_url") or "").lower()):
                if k:
                    index[k] = pid
        n = max([int(pid[1:]) for pid in allp if re.fullmatch(r"P\d+", pid)] or [0])
        touched, new = [], 0
        for item in items:
            if not isinstance(item, dict):
                continue
            w = whitelist(item)
            if not (w["public_id"] or w["linkedin_url"]) or not w["name"]:
                continue
            pid = index.get(w["public_id"]) or index.get(w["linkedin_url"].lower())
            if pid:
                p = allp[pid]
                old_sha = p.get("profile_sha")
                p.update(w)
                p["profile_sha"] = _profile_sha(p)
                if old_sha and old_sha != p["profile_sha"]:
                    for k in ("score", "sub", "evidence", "why", "team_hint", "scored_at", "score_attempts", "score_error"):
                        p.pop(k, None)
            else:
                n += 1
                pid = "P%04d" % n
                p = dict(w, pid=pid, stage="new", found_at=now, history=[], profile_sha=_profile_sha(w))
                allp[pid] = p
                new += 1
                for k in (w["public_id"], w["linkedin_url"].lower()):
                    if k:
                        index[k] = pid
            p["company_key"], p["company"] = target["key"], target["name"]
            p["updated_at"] = now
            touched.append(p)
        _triage_all(ws, touched, target)
        _save_people(ws, allp)
    return touched, new


def _record_search(ws, target, entry):
    with ws.lock("network"):
        cs = _load_companies(ws)
        c = cs.setdefault(target["key"], {"key": target["key"], "name": target["name"], "slug": target.get("slug"),
                                          "url": target.get("url"), "searches": [], "removed": False})
        c.update(name=c.get("name") or target["name"], slug=target.get("slug") or c.get("slug"),
                 url=target.get("url") or c.get("url"), removed=False)
        found = next((s for s in c["searches"] if entry.get("run_id") and s.get("run_id") == entry["run_id"]), None)
        if found:
            found.update(entry)
        else:
            c["searches"].append(entry)
        _save_companies(ws, cs)
        return c


def _pending(ws, key):
    c = _load_companies(ws).get(key) or {}
    return next((s for s in c.get("searches") or [] if s.get("status") in ("READY", "RUNNING") and s.get("run_id")),
                None)


def _wait(ws, client, run, deadline, poll):
    from .apify import TERMINAL
    while run.get("status") not in TERMINAL and time.monotonic() < deadline:
        time.sleep(poll)
        run = client.run(run["id"])
    return run


def _collect(ws, client, target, run, entry, log):
    from .apify import run_cost
    items = client.dataset(run["defaultDatasetId"]) if run.get("defaultDatasetId") else []
    cost = run_cost(run) or round(START_FEE + len(items) * _price(entry.get("mode")), 4)
    cost = min(cost, entry.get("cap") or cost)
    raw = _dir(ws) / "raw" / ("%s-%s.json" % (util.slug(target.get("slug") or target["key"]),
                                              ws.now().strftime("%Y-%m-%dT%H%M%S")))
    util.atomic_write_json(raw, items, mode=0o600)
    touched, new = _merge(ws, items, target)
    counts = {k: sum(1 for p in touched if p.get("triage") == k) for k in ("kept", "recruiter", "skip")}
    if entry.get("slot"):
        budget.settle(ws, entry["slot"], cost, run_id=run.get("id"))
    entry.update(status=run.get("status") or "SUCCEEDED", profiles=len(items),
                 kept=counts["kept"], recruiters=counts["recruiter"], cost=cost, finished_at=util.stamp(ws.now()),
                 raw=ws.rel(raw))
    _record_search(ws, target, entry)
    ws.event("net", "%s: %d people found, %d share a school, %d recruiters ($%.2f)" % (
        target["name"], len(items), counts["kept"], counts["recruiter"], cost), company=target["key"], cost=cost)
    log("%s: %d profiles, %d new, %d kept, %d recruiters, $%.3f" % (target["name"], len(items), new, counts["kept"],
                                                                   counts["recruiter"], cost))
    return {"company": target["name"], "key": target["key"], "url": target.get("url"), "status": entry["status"],
            "profiles": len(items), "new": new, "kept": counts["kept"], "recruiters": counts["recruiter"],
            "skipped": counts["skip"], "cost": cost, "run_id": run.get("id")}


def find(ws, company, url=None, n=None, titles=None, client=None, dry_run=False, log=print, poll_seconds=None):
    """Search one company's people who share your schools. Returns a summary (see the module docstring)."""
    from .apify import ApifyError, TERMINAL
    cfg = ws.cfg
    r = _cfg(ws)
    schools = [s for s in cfg["candidate"].get("schools") or [] if str(s).strip()]
    if not schools:
        raise util.JobPilotError("candidate.schools is empty in jobpilot.toml: add the schools you studied at "
                                 "(as LinkedIn names them, e.g. \"Lakeside State University\")")
    target = resolve(ws, company, url)
    cap_n = int(r.get("max_profiles") or MAX_PROFILES)
    n = max(1, min(int(n or r.get("profiles_per_company") or 30), cap_n))
    actor = r.get("actor") or config.ACTOR_PEOPLE
    inp = {"companies": [target["url"]], "schools": schools, "profileScraperMode": r.get("scraper_mode") or
           "Full ($8 per 1k)", "maxItems": n}
    if r.get("locations"):
        inp["locations"] = list(r["locations"])
    if titles:
        inp["jobTitles"] = [t for t in titles if str(t).strip()]
    if "email" in inp["profileScraperMode"].lower():
        raise util.JobPilotError("referrals.scraper_mode must not search email addresses")
    price = _price(inp["profileScraperMode"])
    estimate = round(START_FEE + n * price, 4)
    available = budget.available(ws)
    cap = math.floor(min(float(r.get("budget_usd_per_company") or 0.40), available) * 100) / 100
    if dry_run:
        return {"dry_run": True, "company": target, "input": inp, "estimate_usd": estimate, "cap_usd": cap,
                "available_usd": available}
    poll = float(poll_seconds if poll_seconds is not None else r.get("poll_seconds") or 10)
    timeout = float(r.get("timeout_seconds") or 900)
    with ws.lock("network-find", wait=False):
        client = _client(ws, client)
        last = ((_load_companies(ws).get(target["key"]) or {}).get("searches") or [{}])[-1]
        age = util.days_between(last.get("at"), ws.now())
        if last.get("status") == "LAUNCH_UNKNOWN" and age is not None and age * 1440 < 30:
            raise util.JobPilotError("the last search of %s may still be running (its launch outcome is unknown). Check "
                                     "the Apify console; JobPilot waits 30 minutes before it launches again."
                                     % target["name"])
        pending = _pending(ws, target["key"])
        if pending:
            log("collecting the earlier search of %s (run %s)" % (target["name"], pending["run_id"]))
            run = _wait(ws, client, client.run(pending["run_id"]), time.monotonic() + timeout, poll)
            if run.get("status") not in TERMINAL:
                return {"company": target["name"], "key": target["key"], "status": "running",
                        "run_id": pending["run_id"], "message": "still running; run the same command again later"}
            return _collect(ws, client, target, run, pending, log)
        props = client.input_schema(actor)
        unknown = sorted(k for k in inp if k not in props)
        if unknown:
            raise util.JobPilotError("the actor's input schema does not know %s; nothing was started or paid"
                                     % ", ".join(unknown))
        inp["profileScraperMode"] = _mode(props, inp["profileScraperMode"])
        if cap < MIN_CHARGE:
            raise util.JobPilotError("Apify budget left today: $%.2f; a company search needs at least $%.2f "
                                     "(about $%.2f for %d profiles). Try tomorrow." % (available, MIN_CHARGE, estimate, n))
        if estimate > cap:
            fit = int((cap - START_FEE) / price)
            if fit < MIN_PROFILES:
                raise util.JobPilotError("Apify budget left today: $%.2f, too little for a useful search" % available)
            log("asking for %d profiles instead of %d to stay within $%.2f" % (fit, n, cap))
            n = inp["maxItems"] = fit
        slot = "referrals:%s:%s" % (target["key"], ws.now().strftime("%Y%m%dT%H%M%S"))
        budget.reserve(ws, slot, cap, kind="referrals", note=target["name"])
        entry = {"at": util.stamp(ws.now()), "schools": schools, "requested": n, "cap": cap, "slot": slot,
                 "mode": inp["profileScraperMode"], "titles": inp.get("jobTitles") or []}
        try:
            run = client.start(actor, inp, max_charge_usd=cap, timeout_s=int(timeout), memory_mb=1024,
                               label=target["key"])
        except ApifyError as exc:
            if exc.definitive:
                budget.release(ws, slot)
                raise util.JobPilotError("Apify refused the search (nothing was paid): %s" % exc) from None
            entry.update(status="LAUNCH_UNKNOWN", note=str(exc)[:200])
            _record_search(ws, target, entry)
            raise util.JobPilotError("the launch outcome is unknown (%s). JobPilot never retries a paid launch: check "
                                     "the Apify console before searching %s again." % (exc, target["name"])) from None
        entry.update(run_id=run["id"], status=run.get("status") or "READY")
        _record_search(ws, target, entry)
        log("%s: searching %d profiles (cap $%.2f)" % (target["name"], n, cap))
        run = _wait(ws, client, client.run(run["id"]) if run.get("status") not in TERMINAL else run,
                    time.monotonic() + timeout, poll)
        if run.get("status") not in TERMINAL:
            entry["status"] = "RUNNING"
            _record_search(ws, target, entry)
            return {"company": target["name"], "key": target["key"], "status": "running", "run_id": run["id"],
                    "message": "still running; run the same command again later to collect it"}
        return _collect(ws, client, target, run, entry, log)


# ---------------------------------------------------------------- context shared by the AI roles

def candidate_block(ws):
    cfg = ws.cfg
    tracks = "\n".join("- %s (priority %s): %s" % (t.get("name", t["id"]), t.get("priority", 1),
                                                   (t.get("description") or "").strip()) for t in config.tracks(cfg))
    try:
        profile = ws.profile_md.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        profile = ""
    return ("Target tracks:\n%s\n\nSchools: %s\n\nProfile:\n%s" % (
        tracks or "-", "; ".join(cfg["candidate"].get("schools") or []) or "-", util.clip(profile, 6000)))


def context_text(ws, key):
    """The candidate's applications and open postings at the company (written by code: trusted)."""
    lines = []
    for st in ledger.states(ws).values():
        if not st["voided"] and util.company_key(st.get("company")) == key:
            lines.append("application %s: %s (stage %s%s)" % (st["id"], st["role"], st["stage"],
                                                               ", applied %s" % st["applied_date"]
                                                               if st.get("applied_date") else ""))
    for row in ws.db.find("company_key=? AND status IN ('ready','hold','applied')", (key,)):
        lines.append("open posting %s: %s (track %s%s)" % (row["id"], row.get("title"), row.get("track") or "?",
                                                           ", score %.1f" % row["score"] if row.get("score") is not None
                                                           else ""))
    return "\n".join(lines) or "no applications or open postings at this company yet"


def _prompt(name):
    return (PROMPTS / ("%s.md" % name)).read_text(encoding="utf-8")


# ---------------------------------------------------------------- score

def review_schema(n):
    item = S.obj({"pid": S.string(2, 12, r"^P\d{3,6}$"),
                  **{k: {"type": "integer", "minimum": 0, "maximum": 5} for k in SUBS},
                  "evidence": S.obj({k: S.string(0, 200) for k in SUBS}),
                  "why": S.string(4, 300), "team_hint": S.string(0, 80), "injection_suspected": {"type": "boolean"}})
    return S.obj({"people": S.array(item, n, 1)})


def total(sub, w):
    return int(round(100 * sum(float(w.get(k, 0)) * float(sub.get(k, 0)) for k in SUBS) / 5))


def check_review(entry, person, ctx):
    """[problems] of one net-review entry: every positive sub-score needs a verbatim quote from the person's
    profile lines (for role and help, the context block also counts)."""
    hay = util.evidence_norm("\n".join(profile_lines(person)))
    ctx_hay = util.evidence_norm(ctx)
    out = []
    for k in SUBS:
        quote = (entry.get("evidence") or {}).get(k) or ""
        if not quote.strip():
            if entry.get(k):
                out.append("%s: a score above 0 needs a quote" % k)
            continue
        if not (util.quote_in(quote, hay, min_len=4) or (k in ("role", "help") and util.quote_in(quote, ctx_hay, 4))):
            out.append("%s: quote not found in the profile: %s" % (k, util.clip(quote, 60)))
    return out


def score(ws, company, log=print, max_batches=None):
    """Score the kept people and recruiters of one company who have no valid score yet."""
    key = _key_of(ws, company)
    r = _cfg(ws)
    w = weights(ws)
    cands = [p for p in people(ws, key) if p.get("triage") in ("kept", "recruiter") and p.get("score") is None
             and int(p.get("score_attempts") or 0) < MAX_ATTEMPTS and p.get("stage") != "dropped"]
    summary = {"company": key, "candidates": len(cands), "scored": 0, "failed": 0, "batches": 0, "errors": [],
               "cost_usd": 0.0}
    if not cands:
        log("no one to score at %s" % key)
        return summary
    ctx = context_text(ws, key)
    system = _prompt("net-review")
    cand = candidate_block(ws)
    name = (_load_companies(ws).get(key) or {}).get("name") or key
    item_schema = review_schema(1)["properties"]["people"]["items"]
    with ws.lock("network-ai", wait=False):
        for start in range(0, len(cands), 10):
            if max_batches is not None and summary["batches"] >= max_batches:
                break
            batch = cands[start:start + 10]
            task = ("Score these %d people at %s for the candidate. Write `why` in %s.\n\n<candidate>\n%s\n</candidate>"
                    "\n\n<context>\n%s\n</context>\n\n%s" % (
                        len(batch), name, _language_name(ws), cand, ctx,
                        "\n\n".join(claude.untrusted("linkedin", "\n".join(profile_lines(p)), pid=p["pid"])
                                    for p in batch)))
            res = claude.run(ws, "net-review", task, system=system, schema=review_schema(len(batch)),
                             model=r.get("model") or "sonnet", timeout=int(r.get("ai_timeout_seconds") or 600))
            summary["batches"] += 1
            summary["cost_usd"] = round(summary["cost_usd"] + (res.cost_usd or 0), 4)
            if not res.ok:
                summary["errors"].append(res.error)
                log("net-review: %s" % res.error)
            entries = res.output.get("people") if isinstance(res.output, dict) else None
            by_pid = {}
            for e in entries if isinstance(entries, list) else []:
                if isinstance(e, dict) and not S.validate(e, item_schema):
                    by_pid.setdefault(e["pid"], e)
            updates = {}
            for p in batch:
                e = by_pid.get(p["pid"])
                problems = check_review(e, p, ctx) if e else ["missing from the answer"]
                if problems:
                    updates[p["pid"]] = {"score_attempts": int(p.get("score_attempts") or 0) + 1,
                                         "score_error": "; ".join(problems)[:300]}
                    summary["failed"] += 1
                    continue
                sub = {k: int(e[k]) for k in SUBS}
                updates[p["pid"]] = {"sub": sub, "evidence": {k: e["evidence"].get(k, "") for k in SUBS},
                                     "score": total(sub, w), "why": e.get("why", ""), "team_hint": e.get("team_hint", ""),
                                     "injection": bool(e.get("injection_suspected")), "scored_at": util.stamp(ws.now()),
                                     "score_model": r.get("model") or "sonnet", "score_error": None}
                summary["scored"] += 1
            _update_people(ws, updates)
    if summary["scored"]:
        ws.event("net", "%s: scored %d people" % (name, summary["scored"]), company=key)
    log("%s: %d scored, %d failed" % (name, summary["scored"], summary["failed"]))
    return summary


# ---------------------------------------------------------------- cards

def card_schema(note_max, message_max, followup_max):
    return S.obj({"note": S.string(30, note_max), "note_alumni": S.string(30, note_max),
                  "message": S.string(80, message_max), "followup": S.string(30, followup_max),
                  "questions": S.array(S.string(15, 300), 6, 4), "why": S.string(4, 400),
                  "hooks": S.array(S.string(4, 200), 4, 1), "avoid": S.array(S.string(2, 200), 4)})


def fact_pack(ws):
    parts = []
    for p in (ws.profile_md, ws.outreach_md):
        try:
            parts.append(p.read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            continue
    return "\n\n".join(x for x in parts if x)


def _numbers(text):
    return {m.group(0).rstrip(",.") for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text or "")}


def _bare(text, person):
    t = (text or "").casefold()
    for part in (person.get("first_name"), person.get("name")):
        if part:
            t = t.replace(part.casefold(), "")
    return re.sub(r"\W+", " ", t).strip()


def lint_card(ws, c, person, ctx, facts, others=()):
    """[problems] of a writer output; empty means it can be shown with copy buttons."""
    r = _cfg(ws)
    limits = {"note": note_limit(ws), "note_alumni": note_limit(ws), "message": int(r.get("message_limit") or 700),
              "followup": int(r.get("followup_limit") or 300)}
    texts = [(k, c.get(k) or "", limits[k]) for k in ("note", "note_alumni", "message", "followup")]
    texts += [("question %d" % i, q, 300) for i, q in enumerate(c.get("questions") or [], 1)]
    allowed = _numbers("\n".join([facts, "\n".join(profile_lines(person)), ctx]))
    allowed |= {n.replace(",", "") for n in allowed}
    never = [t for t in (ws.cfg.get("claims") or {}).get("never_claim") or [] if str(t).strip()]
    out = []
    for field, text, lim in texts:
        if len(text) > lim:
            out.append("%s: %d characters, the limit is %d" % (field, len(text), lim))
        checks = ((SENSITIVE, "mentions a sensitive topic (visa, sponsorship, citizenship, salary)"),
                  (LINKS, "contains a link"), (PLACEHOLDER, "contains a placeholder"),
                  (FAMILIAR, "pretends to know the person"), (CJK, "is not in English"), (EMOJI, "contains an emoji"))
        for rx, why in checks:
            m = rx.search(text)
            if m:
                out.append("%s %s: %r" % (field, why, m.group(0)))
        for term in never:
            if re.search(r"(?i)(?<!\w)%s(?!\w)" % re.escape(term), text):
                out.append("%s claims %r, which the profile does not back" % (field, term))
        for num in sorted(_numbers(text)):
            plain = num.replace(",", "")
            if plain.replace(".", "").isdigit() and float(plain) <= 60:
                continue  # minutes, days, small counts
            if num not in allowed and plain not in allowed:
                out.append("%s: the number %s is not in the profile or the fact pack" % (field, num))
    if c.get("note") and _bare(c.get("note"), person) == _bare(c.get("note_alumni"), person):
        out.append("note and note_alumni say the same thing")
    for other, oper in others:
        for k in ("note", "note_alumni"):
            if c.get(k) and _bare(c[k], person) == _bare(other.get(k), oper):
                out.append("%s repeats another card of this batch" % k)
    return out


def _writer_task(ws, p, ctx, facts, rank, n, limits, sign):
    sub = p.get("sub") or {}
    scores = ["total %s / 100" % p.get("score")]
    scores += ["%s %s: %s" % (k, sub.get(k, "?"), (p.get("evidence") or {}).get(k, "")) for k in SUBS]
    if p.get("team_hint"):
        scores.append("team: %s" % p["team_hint"])
    plan = "Premium" if str(_cfg(ws).get("linkedin_plan") or "free").lower() == "premium" else "free account"
    shared = ", ".join(p.get("schools") or []) or "none"
    head = ("Write the networking card for the person below.\nLimits: each connection note at most %d characters "
            "(LinkedIn %s); message at most %d characters; follow-up at most %d characters.\nSign-off name: %s. "
            "School(s) the person shares with the candidate: %s. Write why, hooks and avoid in %s.\nThis person is "
            "number %d of %d chosen at this company; they may all get a note on the same day, so make the wording "
            "specific to this person.\n" % (limits[0], plan, limits[1], limits[2], sign or "(the candidate's first name)",
                                            shared, _language_name(ws), rank, n))
    return (head + "\n<scores>\n%s\n</scores>\n\n<context>\n%s\n</context>\n\n<fact_pack>\n%s\n</fact_pack>\n\n%s" % (
        "\n".join(scores), ctx, facts, claude.untrusted("linkedin", "\n".join(profile_lines(p)), pid=p["pid"])))


def cards(ws, company, n=None, log=print):
    """Write cards for the n best-scored people of one company who have none yet (default referrals.cards_per_company)."""
    key = _key_of(ws, company)
    r = _cfg(ws)
    n = max(1, int(n or r.get("cards_per_company") or 5))
    chosen = [p for p in people(ws, key) if p.get("triage") in ("kept", "recruiter") and p.get("score") is not None
              and not p.get("injection") and p.get("stage") == "new" and not card(ws, p["pid"])]
    chosen = sorted(chosen, key=lambda p: -p["score"])[:n]
    summary = {"company": key, "cards": 0, "warnings": 0, "failed": 0, "people": [], "errors": [], "cost_usd": 0.0}
    if not chosen:
        log("no scored people without a card at %s (run `jobpilot net score` first)" % key)
        return summary
    ctx = context_text(ws, key)
    facts = fact_pack(ws)
    limits = (note_limit(ws), int(r.get("message_limit") or 700), int(r.get("followup_limit") or 300))
    schema = card_schema(*limits)
    system = _prompt("net-writer")
    sign = sign_name(ws)
    done = []
    with ws.lock("network-ai", wait=False):
        for rank, p in enumerate(chosen, 1):
            task = _writer_task(ws, p, ctx, facts, rank, len(chosen), limits, sign)
            c, issues, error = None, [], ""
            for attempt in (1, 2):
                res = claude.run(ws, "net-writer", task, system=system, schema=schema,
                                 model=r.get("writer_model") or "opus", timeout=int(r.get("ai_timeout_seconds") or 600))
                summary["cost_usd"] = round(summary["cost_usd"] + (res.cost_usd or 0), 4)
                if res.ok:
                    c = res.output   # a valid card is kept even if the retry fails
                    issues = feedback = lint_card(ws, c, p, ctx, facts, done)
                    if not issues:
                        break
                else:
                    error = res.error
                    feedback = [res.error] + (lint_card(ws, res.output, p, ctx, facts, done)
                                              if isinstance(res.output, dict) else [])
                if attempt == 1:
                    task += ("\n\nYour previous draft broke these checks. Fix exactly these and keep everything "
                             "else true:\n" + "\n".join("- " + i for i in feedback))
            if c is None:
                summary["failed"] += 1
                summary["errors"].append("%s: %s" % (p["pid"], error))
                _update_people(ws, {p["pid"]: {"card_error": error[:300]}})
                continue
            stored = {"pid": p["pid"], "note": c["note"], "note_alumni": c["note_alumni"], "message": c["message"],
                      "followup": c["followup"], "questions": list(c["questions"]), "why": c["why"],
                      "hooks": list(c["hooks"]), "avoid": list(c.get("avoid") or []), "warnings": issues,
                      "created_at": util.stamp(ws.now()), "model": r.get("writer_model") or "opus",
                      "note_limit": limits[0], "sign_name": sign}
            util.atomic_write_json(_card_path(ws, p["pid"]), stored, mode=0o600)
            with ws.lock("network"):   # a mark made while the card was being written wins over card_ready
                allp = _load_people(ws)
                q = allp[p["pid"]]
                q.update(card_at=stored["created_at"], card_error=None, updated_at=stored["created_at"])
                if q.get("stage") == "new":
                    q["stage"] = "card_ready"
                _save_people(ws, allp)
            done.append((c, p))
            summary["cards"] += 1
            summary["warnings"] += bool(issues)
            summary["people"].append(p["pid"])
    if summary["cards"]:
        ws.event("net", "%s: %d outreach cards ready" % ((_load_companies(ws).get(key) or {}).get("name") or key,
                                                         summary["cards"]), company=key)
    log("%s: %d cards (%d with warnings), %d failed" % (key, summary["cards"], summary["warnings"], summary["failed"]))
    return summary


def run(ws, company, url=None, n=None, n_cards=None, titles=None, client=None, log=print):
    found = find(ws, company, url=url, n=n, titles=titles, client=client, log=log)
    if found.get("status") == "running":
        return {"find": found}
    scored = score(ws, found["key"], log=log)
    made = cards(ws, found["key"], n=n_cards, log=log)
    return {"find": found, "score": scored, "cards": made}


# ---------------------------------------------------------------- marks and next steps

def initials(name):
    parts = [w for w in re.split(r"\s+", (name or "").strip()) if w and w[0].isalpha()]
    return ".".join(w[0].upper() for w in parts[:3]) + "." if parts else ""


def mark(ws, pid, event, note="", by="user"):
    """Record what you did; returns the person. `undo` reverts the last mark."""
    if event not in MARKS:
        raise util.JobPilotError("event must be one of %s" % ", ".join(MARKS))
    with ws.lock("network"):
        allp = _load_people(ws)
        p = allp.get(pid)
        if not p:
            raise util.JobPilotError("no person %s" % pid)
        history = p.setdefault("history", [])
        now = util.stamp(ws.now())
        last = None
        if event == "undo":
            if not history:
                raise util.JobPilotError("%s has no mark to undo" % pid)
            last = history.pop()
            p["stage"] = last.get("from") or "new"
        else:
            history.append({"event": event, "from": p.get("stage") or "new", "to": EVENT_STAGE[event], "at": now,
                            "note": note or ""})
            p["stage"] = EVENT_STAGE[event]
        p["updated_at"] = now
        _save_people(ws, allp)
    util.jsonl_append(ws.network / "events.jsonl", {"pid": pid, "event": event, "note": note or "", "by": by})
    if event == "referred":
        app = _ledger_referral(ws, p)
        if app:
            p = _update_people(ws, {pid: {"referral_app": app}})[pid]
    elif last and last.get("event") == "referred" and p.get("referral_app"):
        # the ledger is append-only: the referral stays on the application, with a note that it was taken back
        ledger.record(ws, p["referral_app"], "note", note="referral via %s was undone" % pid, by="network")
        p = _update_people(ws, {pid: {"referral_app": None}})[pid]
    ws.event("net", "%s %s (%s)" % (p.get("name"), event, p.get("company")), pid=pid)
    return p


def _ledger_referral(ws, p):
    """Record a referral on the company's only open application, if there is exactly one."""
    open_apps = [s for s in ledger.states(ws).values() if not s["voided"] and not s["closed"]
                 and util.company_key(s.get("company")) == p.get("company_key")]
    if len(open_apps) != 1:
        return None
    ledger.record(ws, open_apps[0]["id"], "referral", referrer=initials(p.get("name")), by="network",
                  note="referral via %s" % p["pid"])
    return open_apps[0]["id"]


ACTIONS = {
    "card_ready": "send a connection note (copy it from the card)",
    "accepted": "send the message from the card",
    "replied": "suggest a time for a short chat",
    "chat_done": "send a thank-you note; ask about a referral if the chat went well",
}


def due(ws, now=None):
    """People whose stage needs a next step from you, most urgent first."""
    now = now or ws.now()
    out = []
    for p in people(ws):
        stage = p.get("stage") or "new"
        hist = p.get("history") or []
        since = hist[-1]["at"] if hist else (p.get("card_at") or p.get("found_at"))
        days = util.days_between(since, now)
        days = int(days) if days is not None else 0
        action = ACTIONS.get(stage)
        if stage == "connect_sent" and days >= 21:
            action = "no answer for %d days: mark no_response or try someone else" % days
        elif stage == "messaged" and days >= 7:
            action = "no reply for %d days: send the follow-up from the card" % days
        if action:
            out.append({"pid": p["pid"], "name": p.get("name"), "company": p.get("company"), "stage": stage,
                        "days": days, "action": action, "score": p.get("score")})
    rank = {"chat_done": 0, "replied": 1, "accepted": 2, "messaged": 3, "card_ready": 4, "connect_sent": 5}
    out.sort(key=lambda d: (rank.get(d["stage"], 9), -(d["score"] or 0)))
    return out


# ---------------------------------------------------------------- CLI

def _line(p):
    return "%-6s %4s %-9s %-11s %-26s %-34s %s" % (
        p["pid"], p.get("score") if p.get("score") is not None else "-", p.get("triage") or "", p.get("stage") or "",
        util.clip(p.get("name"), 26), util.clip(p.get("title") or p.get("headline"), 34),
        ", ".join(p.get("schools") or []))


def cmd_net(ws, args):
    from .cli import out
    a = args.net_action
    log = (lambda *_: None) if args.json else print
    if a == "find":
        res = find(ws, args.company, url=args.url, n=args.n, titles=args.titles, dry_run=args.dry_run, log=log)
        return out(args, res, None if res.get("dry_run") else res.get("message") or
                   "%(company)s: %(profiles)d profiles, %(kept)d share a school, %(recruiters)d recruiters, $%(cost).3f"
                   % res)
    if a == "score":
        res = score(ws, args.company, log=log)
        return out(args, res, "%(scored)d scored, %(failed)d failed" % res)
    if a == "cards":
        res = cards(ws, args.company, n=args.n, log=log)
        return out(args, res, "%(cards)d cards, %(warnings)d with warnings, %(failed)d failed" % res)
    if a == "run":
        res = run(ws, args.company, url=args.url, n=args.n, n_cards=args.cards, titles=args.titles, log=log)
        return out(args, res, "done: see `jobpilot net list \"%s\"`" % args.company)
    if a == "list":
        key = _key_of(ws, args.company) if args.company else None
        rows = [p for p in people(ws, key) if args.all or p.get("triage") != "skip"]
        return out(args, rows, "\n".join(_line(p) for p in rows) or "no people yet")
    if a == "companies":
        rows = companies(ws)
        return out(args, rows, "\n".join("%-28s %3d people, %3d kept, %2d recruiters, %2d cards%s" % (
            util.clip(c.get("name"), 28), c["counts"]["people"], c["counts"]["kept"], c["counts"]["recruiters"],
            c["counts"]["cards"], " (removed)" if c.get("removed") else "") for c in rows) or "no companies yet")
    if a == "show":
        p = person(ws, args.pid)
        if not p:
            raise util.JobPilotError("no person %s" % args.pid)
        return out(args, p)
    if a == "mark":
        p = mark(ws, args.pid, args.event, note=args.note or "")
        return out(args, p, "%s is now %s" % (p["pid"], p["stage"]))
    if a == "remove":
        c = remove_company(ws, args.company, args.reason or "")
        return out(args, c, "removed %s" % c.get("name"))
    if a == "restore":
        c = restore_company(ws, args.company)
        return out(args, c, "restored %s" % c.get("name"))
    if a == "due":
        rows = due(ws)
        return out(args, rows, "\n".join("%s %-24s %-22s %s" % (d["pid"], util.clip(d["name"], 24),
                                                                util.clip(d["company"], 22), d["action"])
                                         for d in rows) or "nothing to do")
    raise AssertionError(a)


def register(sub, add):
    sp = add("net", cmd_net, help="alumni referral finder: find, score, cards, run, list, show, mark, due")
    nsub = sp.add_subparsers(dest="net_action", required=True)

    def q(name, **kw):
        p = nsub.add_parser(name, **kw)
        p.add_argument("--json", action="store_true", help="machine-readable output")
        return p

    p = q("find", help="search a company's people who share your schools (paid, capped)")
    p.add_argument("company")
    p.add_argument("--url", help="the company's LinkedIn page, https://www.linkedin.com/company/<name>")
    p.add_argument("--n", type=int, help="profiles to fetch (default referrals.profiles_per_company)")
    p.add_argument("--titles", nargs="*", help="only people with these job titles")
    p.add_argument("--dry-run", action="store_true", help="print the input and the cap; pay nothing")
    p = q("score", help="AI scores for the people found (five sub-scores with quotes)")
    p.add_argument("company")
    p = q("cards", help="outreach drafts for the best-scored people")
    p.add_argument("company")
    p.add_argument("--n", type=int)
    p = q("run", help="find, score and write cards in one go")
    p.add_argument("company")
    p.add_argument("--url")
    p.add_argument("--n", type=int)
    p.add_argument("--cards", type=int)
    p.add_argument("--titles", nargs="*")
    p = q("list", help="people at one company (or all), best first")
    p.add_argument("company", nargs="?")
    p.add_argument("--all", action="store_true", help="include people the rules skipped")
    q("companies", help="companies searched so far")
    p = q("show", help="one person with their card")
    p.add_argument("pid")
    p = q("mark", help="record what you did on LinkedIn")
    p.add_argument("pid")
    p.add_argument("event", choices=list(MARKS))
    p.add_argument("--note")
    p = q("remove", help="hide a company from the network lists")
    p.add_argument("company")
    p.add_argument("--reason")
    p = q("restore", help="bring a removed company back")
    p.add_argument("company")
    q("due", help="people waiting for your next step")
