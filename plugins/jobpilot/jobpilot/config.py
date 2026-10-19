"""The workspace configuration (jobpilot.toml): defaults, loading and checks.

The file is written by /jobpilot:setup (or by hand) and only read here; everything the code itself records lives in
data/. Unknown keys are kept, so newer configs keep working with older code.
"""
import copy
import re
import tomllib

from .util import JobPilotError

ACTOR_JOBS = "fantastic-jobs/advanced-linkedin-job-search-api"
ACTOR_PEOPLE = "harvestapi/linkedin-company-employees"
WORK_AUTH = ("us_citizen", "permanent_resident", "work_visa", "needs_sponsorship", "other")
TRACK_ID = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
GROUP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,47}$")
# The actor matches every one of these as PostgreSQL "simple" full-text search terms (see docs/search-plan.md).
TEXT_FILTERS = ("titleSearch", "titleExclusionSearch", "descriptionSearch", "descriptionExclusionSearch",
                "organizationSearch", "organizationExclusionSearch", "locationSearch", "locationExclusionSearch")
SEARCH_FILTERS = ("titleSearch", "descriptionSearch", "organizationSearch", "organizationSlugFilter")
# Set by the collector for every group; a group may not override them.
RESERVED_INPUT = ("timeRange", "datePostedAfter", "limit", "descriptionType")

DEFAULTS = {
    "candidate": {
        "name": "",
        "resume": "",
        "work_authorization": "",
        "seniority": [],
        "locations": ["United States"],
        "work_arrangements": [],
        "min_salary_usd": 0,
        "target_salary_usd": 0,        # roles advertising less (but at least min_salary_usd) are practice roles
        "languages": ["English"],
        "schools": [],
        "open_to_internships": False,
        "open_to_contract": False,
    },
    "app": {"language": "en", "timezone": ""},
    "tracks": [],
    "search": {
        "provider": "apify",
        "actor": ACTOR_JOBS,
        "time_range": "7d",
        "lookback_hours": 36,
        "first_lookback_hours": 72,
        "max_lookback_days": 7,
        "daily_budget_usd": 1.0,
        "monthly_budget_usd": 25.0,
        "run_time": "07:30",
        "max_concurrent_runs": 3,
        "run_timeout_seconds": 900,
        "limit": 1000,
        "memory_mb": 1024,
        "common": {},
        "groups": {},
    },
    "filters": {
        "exclude_employment_types": ["INTERN", "PART_TIME", "TEMPORARY", "VOLUNTEER", "PER_DIEM"],
        "exclude_title_words": [],
        "protect_title_words": [],
        "exclude_companies": [],
        "drop_experience_levels": [],
        "skip_training_gigs": True,
        "skip_agencies": False,
        "drop_citizenship_required": None,   # None: follow candidate.work_authorization
        "drop_no_sponsorship": None,         # None: follow candidate.work_authorization
    },
    "review": {
        "model": "sonnet",
        "effort": "",
        "batch_size": 8,
        "max_batches": 25,
        "timeout_seconds": 600,
        "max_attempts": 3,
        "max_jd_chars": 14000,
        "rescore_per_run": 40,
    },
    "pool": {"stale_after_days": 45},
    "tailor": {"master": "", "model": "opus", "audit_model": "", "max_changed_bullets": 6, "pdf": True,
               "timeout_seconds": 900},
    "claims": {"never_claim": [], "banned_phrases": []},
    "referrals": {
        "actor": ACTOR_PEOPLE,
        "scraper_mode": "Full ($8 per 1k)",
        "locations": ["United States"],
        "profiles_per_company": 30,
        "cards_per_company": 5,
        "budget_usd_per_company": 0.40,
        "linkedin_plan": "free",
        "note_limit_free": 200,
        "note_limit_premium": 300,
        "message_limit": 700,
        "email_subject_limit": 80,     # the cold email on each card (you send it yourself, resume attached)
        "email_body_limit": 1500,
        "email_followup_limit": 600,
        "email_signature": "",         # lines after "Best regards," ("" = candidate.name)
        "sign_name": "",
        "model": "sonnet",
        "writer_model": "opus",
        "weights": {"role": 0.30, "alumni": 0.25, "path": 0.15, "reply": 0.15, "help": 0.15},
        "school_aliases": {},          # {"Lakeside State University": ["Lakeside State", "LSU"]}: other names of your schools
        "followup_limit": 300,
        "max_profiles": 100,
        "poll_seconds": 10,
        "timeout_seconds": 900,        # how long to wait for one Apify people search
        "ai_timeout_seconds": 600,     # one net-review / net-writer run
    },
    "inbox": {"lookback_days": 14, "label": "", "my_addresses": []},   # my_addresses: mail from them is yours
    "followup": {"after_days": 10},
    "dashboard": {"port": 8765},
    "notify": {"command": ""},
}


def _merge(base, extra):
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key not in ("groups", "common",
                                                                                     "school_aliases"):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load(path):
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise JobPilotError("no jobpilot.toml at %s; run `jobpilot init` or /jobpilot:setup" % path) from None
    except tomllib.TOMLDecodeError as exc:
        raise JobPilotError("jobpilot.toml is not valid TOML: %s" % exc) from None
    return _merge(DEFAULTS, raw)


def tracks(cfg):
    """Tracks in preference order (priority 1 first; file order breaks ties)."""
    items = [t for t in cfg.get("tracks") or [] if isinstance(t, dict) and t.get("id")]
    return sorted(items, key=lambda t: (float(t.get("priority", 9)), items.index(t)))


def track(cfg, track_id):
    return next((t for t in tracks(cfg) if t["id"] == track_id), None)


def track_ids(cfg):
    return [t["id"] for t in tracks(cfg)]


def tier(cfg, track_id):
    t = track(cfg, track_id)
    return float(t.get("priority", 9)) if t else 99.0


def min_score(cfg, track_id):
    t = track(cfg, track_id)
    return float((t or {}).get("min_score", 5.0))


def categories(cfg, track_id=None):
    if track_id:
        t = track(cfg, track_id) or {}
        return list(t.get("categories") or ["other"])
    seen = []
    for t in tracks(cfg):
        for c in t.get("categories") or ["other"]:
            if c not in seen:
                seen.append(c)
    return seen or ["other"]


def groups(cfg):
    """[(name, group dict)] in track preference order, then file order."""
    raw = (cfg.get("search") or {}).get("groups") or {}
    order = {tid: i for i, tid in enumerate(track_ids(cfg))}
    names = list(raw)
    return sorted(raw.items(), key=lambda kv: (order.get(kv[1].get("track"), 99), names.index(kv[0])))


def group_filters(cfg, group):
    """The actor input of one group without the collector's own keys: search.common merged with the group's filters."""
    out = dict((cfg.get("search") or {}).get("common") or {})
    for key, value in group.items():
        if key in ("track", "budget_usd", "note", "enabled"):
            continue
        out[key] = value
    return out


def drop_citizenship_required(cfg):
    value = cfg["filters"].get("drop_citizenship_required")
    if value is not None:
        return bool(value)
    return cfg["candidate"].get("work_authorization") not in ("us_citizen", "")


def needs_sponsorship(cfg):
    value = cfg["filters"].get("drop_no_sponsorship")
    if value is not None:
        return bool(value)
    return cfg["candidate"].get("work_authorization") == "needs_sponsorship"


def language(cfg):
    lang = (cfg.get("app") or {}).get("language") or "en"
    return "zh" if str(lang).lower().startswith("zh") else "en"


def check(cfg, workspace=None):
    """[(level, message)] with level 'error' or 'warn'. Errors stop paid or AI work; warnings are advice."""
    out = []
    err = lambda m: out.append(("error", m))  # noqa: E731
    warn = lambda m: out.append(("warn", m))  # noqa: E731
    cand = cfg.get("candidate") or {}
    if not cand.get("name"):
        warn("candidate.name is empty (used for resume file names and outreach sign-offs)")
    try:
        floor, target = float(cand.get("min_salary_usd") or 0), float(cand.get("target_salary_usd") or 0)
        if target and target <= floor:
            warn("candidate.target_salary_usd (%s) is not above min_salary_usd (%s): no role will be a practice role"
                 % ("{:,.0f}".format(target), "{:,.0f}".format(floor)))
    except (TypeError, ValueError):
        err("candidate.min_salary_usd and target_salary_usd must be numbers")
    wa = cand.get("work_authorization")
    if wa and wa not in WORK_AUTH:
        err("candidate.work_authorization must be one of %s" % ", ".join(WORK_AUTH))
    elif not wa:
        warn("candidate.work_authorization is empty: eligibility filters cannot tell citizenship or sponsorship needs")
    ts = tracks(cfg)
    if not ts:
        err("no [[tracks]]: run /jobpilot:setup to design them from your resume")
    seen = set()
    for t in ts:
        tid = t.get("id")
        if not TRACK_ID.match(str(tid)):
            err("track id %r: use 1-24 lowercase letters, digits, '-' or '_', starting with a letter" % tid)
        if tid in seen:
            err("track id %r appears twice" % tid)
        seen.add(tid)
        if tid == "none":
            err("track id 'none' is reserved")
        if not t.get("name"):
            warn("track %s has no name" % tid)
        if not t.get("description"):
            warn("track %s has no description (the reviewer uses it to place roles on tracks)" % tid)
        if not t.get("categories"):
            warn("track %s has no categories; 'other' is used" % tid)
        try:
            ms = float(t.get("min_score", 5.0))
            if not 0 <= ms <= 10:
                err("track %s: min_score must be between 0 and 10" % tid)
        except (TypeError, ValueError):
            err("track %s: min_score must be a number" % tid)
    search = cfg.get("search") or {}
    gs = groups(cfg)
    if ts and not gs:
        err("no [search.groups.*]: run /jobpilot:setup or /jobpilot:tune to write the search plan")
    total = 0.0
    for name, g in gs:
        if not GROUP_ID.match(name):
            err("group name %r: use letters, digits, '.', '-' or '_'" % name)
        if g.get("track") not in seen:
            err("group %s: track %r is not one of the [[tracks]]" % (name, g.get("track")))
        f = group_filters(cfg, g)
        if not any(f.get(k) for k in SEARCH_FILTERS):
            err("group %s has no titleSearch, descriptionSearch or organizationSearch: it would fetch everything" % name)
        for key in RESERVED_INPUT:
            if key in g:
                err("group %s sets %s, which the collector controls" % (name, key))
        for key in TEXT_FILTERS:
            value = f.get(key)
            if value is not None and not (isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)):
                err("group %s: %s must be a list of non-empty strings" % (name, key))
        if len(f.get("descriptionSearch") or []) > 13:
            warn("group %s: %d descriptionSearch terms; the actor asks for a handful (<= 13) or runs may time out"
                 % (name, len(f["descriptionSearch"])))
        if f.get("descriptionSearch") and not (f.get("titleSearch") or f.get("organizationSearch")):
            warn("group %s: descriptionSearch without titleSearch is slow and broad; pair it with titleSearch" % name)
        if f.get("descriptionExclusionSearch"):
            warn("group %s: descriptionExclusionSearch has no observable effect upstream; use titleExclusionSearch "
                 "or the coarse filters instead" % name)
        try:
            budget = float(g.get("budget_usd", 0))
        except (TypeError, ValueError):
            budget = -1
        if budget <= 0:
            err("group %s: budget_usd must be a positive number (the run's maxTotalChargeUsd)" % name)
        total += max(budget, 0)
    daily = float(search.get("daily_budget_usd") or 0)
    monthly = float(search.get("monthly_budget_usd") or 0)
    if gs and daily <= 0:
        err("search.daily_budget_usd must be positive")
    if gs and monthly <= 0:
        err("search.monthly_budget_usd must be positive")
    if gs and total > daily + 1e-9:
        warn("group budgets add up to $%.2f, above the daily cap $%.2f: the last groups will be skipped on busy days"
             % (total, daily))
    if daily and monthly and daily * 28 > monthly * 1.5:
        warn("daily cap $%.2f x 30 days is far above the monthly cap $%.2f: late-month runs will be cut short"
             % (daily, monthly))
    if not re.fullmatch(r"\d{2}:\d{2}", str(search.get("run_time", ""))):
        err("search.run_time must look like 07:30")
    if workspace is not None:
        if not workspace.profile_md.exists():
            err("profile/profile.md is missing (the fact source every AI step quotes)")
        if not workspace.rubric_md.exists():
            err("profile/rubric.md is missing (the scoring rubrics)")
        resume = cand.get("resume")
        if resume and not (workspace.root / resume).exists():
            warn("candidate.resume %s does not exist" % resume)
    return out
