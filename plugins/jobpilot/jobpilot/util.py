"""Shared helpers: time, atomic files, hash-chained JSONL ledgers, file locks and text normalization."""
import contextlib
import fcntl
import hashlib
import html
import json
import os
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class JobPilotError(Exception):
    """An expected problem with a message meant for the user (the CLI prints it without a traceback)."""


# ---------------------------------------------------------------- time

def system_timezone():
    """The IANA name of this machine's time zone, or UTC."""
    if os.environ.get("TZ") and "/" in os.environ["TZ"]:
        return os.environ["TZ"]
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def zone(name=None):
    try:
        return ZoneInfo(name or system_timezone())
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def now(tz=None):
    """Aware current time in `tz`; JOBPILOT_NOW (ISO, naive = UTC) fakes the clock for tests and demos."""
    fake = os.environ.get("JOBPILOT_NOW")
    if fake:
        dt = datetime.fromisoformat(fake.replace("Z", "+00:00"))
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    return dt.astimezone(zone(tz) if isinstance(tz, (str, type(None))) else tz)


def stamp(dt=None):
    return (dt or now()).isoformat(timespec="seconds")


def parse_time(value):
    """Aware datetime from an ISO string (naive = UTC), a date, or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text += "T00:00:00"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def days_between(a, b):
    a, b = parse_time(a), parse_time(b)
    if not a or not b:
        return None
    return (b - a).total_seconds() / 86400.0


# ---------------------------------------------------------------- files

def atomic_write_text(path, text, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_json(path, obj, mode=0o644):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", mode)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


@contextlib.contextmanager
def file_lock(path, wait=True, timeout=None):
    """Exclusive fcntl lock on `path` (created if missing). Raises JobPilotError when busy and wait=False."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | (0 if wait and deadline is None else fcntl.LOCK_NB))
                break
            except BlockingIOError:
                if not wait or (deadline is not None and time.monotonic() > deadline):
                    raise JobPilotError("another JobPilot process is busy (%s); try again later" % path.name) from None
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---------------------------------------------------------------- append-only ledgers

def _dumps(rec):
    return json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _line_sha(line):
    return hashlib.sha256(line.rstrip(b"\n")).hexdigest()


def jsonl_append(path, rec):
    """Append `rec` with seq, ts and prev (sha256 of the previous line) under an exclusive lock; returns it.
    A torn last line (a crashed writer) is moved to <file>.torn first."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        size = os.fstat(fd).st_size
        data = os.pread(fd, size, 0) if size else b""
        if data and not data.endswith(b"\n"):
            cut = data.rfind(b"\n") + 1
            with open(str(path) + ".torn", "ab") as torn:
                torn.write(data[cut:] + b"\n")
            os.ftruncate(fd, cut)
            data = data[:cut]
        last = data.rstrip(b"\n").rsplit(b"\n", 1)[-1] if data.strip() else b""
        seq = 0
        if last:
            try:
                seq = int(json.loads(last).get("seq") or 0)
            except ValueError:
                raise JobPilotError("%s: the last line is not JSON; fix the file before writing" % path) from None
        out = {k: v for k, v in rec.items() if k not in ("seq", "ts", "prev")}
        out = {"seq": seq + 1, "ts": stamp(), "prev": _line_sha(last) if last else "", **out}
        os.write(fd, (_dumps(out) + "\n").encode())
        os.fsync(fd)
        return out
    finally:
        os.close(fd)


def jsonl_read(path):
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a torn tail; jsonl_append moves it aside
    return out


def jsonl_verify(path):
    """[problems] of a ledger's sequence numbers and hash chain ([] = intact)."""
    problems, prev, seq = [], "", 0
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return problems
    for n, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            problems.append("line %d is not JSON" % n)
            continue
        if rec.get("prev", "") != prev:
            problems.append("line %d: hash chain broken" % n)
        if rec.get("seq") != seq + 1:
            problems.append("line %d: sequence %s after %s" % (n, rec.get("seq"), seq))
        prev, seq = _line_sha(line), rec.get("seq") or seq + 1
    return problems


# ---------------------------------------------------------------- text

def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


_DASHES = re.compile("[‐-―−]")
_SQUOTES = re.compile("[‘’‚‛`´]")
_DQUOTES = re.compile("[“”„‟]")
_BULLETS = re.compile("[•·▪●◦‣⁃■□]")


def evidence_norm(text):
    """Text as compared for verbatim evidence: entities decoded, Unicode compatibility forms folded, dashes, quotes
    and bullets unified, whitespace collapsed, case folded. Quotes must still be the source's own words."""
    text = unicodedata.normalize("NFKC", html.unescape(text or ""))
    # invisible format characters (zero-width spaces, soft hyphens, BOMs) that job boards scatter through text
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = _DASHES.sub("-", text)
    text = _SQUOTES.sub("'", text)
    text = _DQUOTES.sub('"', text)
    text = _BULLETS.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def quote_in(quote, haystack_norm, min_len=8):
    q = evidence_norm(quote).strip(" .;:,")
    return len(q) >= min_len and q in haystack_norm


def slug(text, maxlen=48):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return (text[:maxlen].rstrip("-") or "x")


_LEGAL = {"inc", "llc", "lp", "llp", "ltd", "plc", "corp", "corporation", "co", "the", "gmbh", "sa", "ag", "pllc"}


def company_key(name):
    """'JPMorgan Chase & Co.' and 'JPMorganChase' -> 'jpmorganchase': lower case, punctuation and legal forms gone."""
    s = unicodedata.normalize("NFKD", name or "").casefold()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = [w for w in s.split() if w not in _LEGAL]
    return "".join(words) or "".join(s.split())


def norm_title(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").casefold()).strip()


def clip(text, n):
    text = text or ""
    return text if len(text) <= n else text[: max(0, n - 1)].rstrip() + "…"


def offline():
    """JOBPILOT_OFFLINE=1 (tests, demos): every network client refuses to run."""
    return os.environ.get("JOBPILOT_OFFLINE") == "1"


def annual_usd(amount, unit):
    """An annual USD figure from a salary amount and its unit (HOUR, DAY, WEEK, MONTH, YEAR), or None."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None
    factor = {"HOUR": 2080, "DAY": 260, "WEEK": 52, "MONTH": 12, "YEAR": 1}.get(str(unit or "YEAR").upper())
    return amount * factor if factor else None
