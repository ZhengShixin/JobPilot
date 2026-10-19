"""A hardened Apify API client (standard library only).

- The token comes from $APIFY_TOKEN, ~/.config/jobpilot/apify-token (mode 600) or the workspace .env; it is never
  printed, logged or sent anywhere but api.apify.com, and redirects are refused so it cannot follow one.
- A POST that starts a paid run is never retried: a timeout may still have started the run, so the caller records
  it as LAUNCHING and later adopts the real run by its start time (see search.reconcile).
- Every paid run carries maxTotalChargeUsd, so one run can never spend more than its cap.
- JOBPILOT_OFFLINE=1 refuses all network access (tests and demos use FakeApify instead).
"""
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .util import JobPilotError, offline, read_json

API = "https://api.apify.com"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
TOKEN_FILE = Path("~/.config/jobpilot/apify-token").expanduser()


class ApifyError(JobPilotError):
    """http_status: the HTTP code of a definitive answer; not_started: no request left this process."""
    def __init__(self, message, http_status=None, not_started=False):
        super().__init__(message)
        self.http_status = http_status
        self.not_started = not_started

    @property
    def definitive(self):
        """Apify refused the request (4xx except 408/429), so no run was created."""
        return self.not_started or (isinstance(self.http_status, int) and 400 <= self.http_status < 500
                                    and self.http_status not in (408, 429))


def _dotenv(path):
    try:
        for line in Path(path).read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?APIFY_(?:API_)?TOKEN\s*=\s*['\"]?([^'\"\s]+)", line)
            if m:
                return m.group(1)
    except OSError:
        pass
    return None


def token_source(workspace=None):
    """Where a token would come from ('env', 'file', '.env') or None, without reading its value out."""
    if os.environ.get("APIFY_TOKEN") or os.environ.get("APIFY_API_TOKEN"):
        return "env"
    if TOKEN_FILE.is_file():
        return "file"
    if workspace is not None and _dotenv(Path(workspace.root) / ".env"):
        return ".env"
    return None


def load_token(workspace=None):
    if offline():
        raise ApifyError("JOBPILOT_OFFLINE=1: the Apify token is not loaded", not_started=True)
    value = os.environ.get("APIFY_TOKEN") or os.environ.get("APIFY_API_TOKEN")
    if not value and TOKEN_FILE.is_file():
        if TOKEN_FILE.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ApifyError("%s must be readable only by you: chmod 600 %s" % (TOKEN_FILE, TOKEN_FILE),
                             not_started=True)
        value = TOKEN_FILE.read_text().strip()
    if not value and workspace is not None:
        value = _dotenv(Path(workspace.root) / ".env")
    if not value:
        raise ApifyError("no Apify token. Get one at https://console.apify.com/settings/integrations, then run "
                         "`./jobpilot token set` in a terminal (or export APIFY_TOKEN).", not_started=True)
    if any(c.isspace() for c in value) or len(value) < 20:
        raise ApifyError("the Apify token looks malformed; set it again with `./jobpilot token set`", not_started=True)
    return value


def save_token(value):
    value = (value or "").strip()
    if len(value) < 20 or any(c.isspace() for c in value):
        raise JobPilotError("that does not look like an Apify API token")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")
    os.chmod(TOKEN_FILE, 0o600)
    return TOKEN_FILE


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


PAID_POST = re.compile(r"/v2/acts/[^/]+/runs")


class Apify:
    def __init__(self, token=None, guard=None):
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect)
        self.guard = guard  # callable -> reason string to refuse a paid launch (pause switch), or None

    def _open(self, method, path, data=None, query=None, raw=False, auth=True):
        if not path.startswith("/v2/") or "?" in path:
            raise ApifyError("invalid Apify endpoint", not_started=True)
        if offline():
            raise ApifyError("JOBPILOT_OFFLINE=1: network access refused", not_started=True)
        if method == "POST" and PAID_POST.fullmatch(path) and self.guard:
            reason = self.guard()
            if reason:
                raise ApifyError("paid launch refused: %s" % reason, not_started=True)
        url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"Content-Type": "application/json", "User-Agent": "JobPilot"}
        if auth and self._token:
            headers["Authorization"] = "Bearer " + self._token
        req = urllib.request.Request(url, method=method, headers=headers,
                                     data=json.dumps(data).encode() if data is not None else None)
        for attempt in range(3 if method == "GET" else 1):
            try:
                with self._opener.open(req, timeout=60) as resp:
                    body = resp.read()
                return body.decode("utf-8", "replace") if raw else json.loads(body)
            except urllib.error.HTTPError as exc:
                if method == "GET" and exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                try:
                    kind = re.sub(r"[^A-Za-z0-9_-]", "", json.loads(exc.read()).get("error", {}).get("type", ""))[:80]
                except (ValueError, AttributeError):
                    kind = ""
                raise ApifyError("Apify %s %s: HTTP %s %s" % (method, path, exc.code, kind), http_status=exc.code) from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                if method == "GET" and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ApifyError("Apify %s %s: network or response error (a POST is never retried; check the Apify "
                                 "console before running again)" % (method, path)) from None

    # ------------------------------------------------------------ calls
    def actor(self, actor):
        return self._open("GET", "/v2/acts/" + actor.replace("/", "~"))["data"]

    def input_schema(self, actor):
        """The actor's input properties (free; public actors need no token)."""
        info = self.actor(actor)
        build = ((info.get("taggedBuilds") or {}).get("latest") or {}).get("buildId")
        if not build:
            raise ApifyError("%s has no latest build" % actor)
        raw = self._open("GET", "/v2/actor-builds/" + build)["data"].get("inputSchema")
        schema = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return schema.get("properties") or {}

    def start(self, actor, run_input, *, max_charge_usd, timeout_s=900, memory_mb=1024, label=None):
        return self._open("POST", "/v2/acts/%s/runs" % actor.replace("/", "~"), run_input,
                          {"maxTotalChargeUsd": "%.2f" % max(0.01, float(max_charge_usd)), "timeout": str(int(timeout_s)),
                           "memory": str(int(memory_mb)), "restartOnError": "false"})["data"]

    def run(self, run_id):
        return self._open("GET", "/v2/actor-runs/" + run_id)["data"]

    def recent_runs(self, actor, limit=20):
        data = self._open("GET", "/v2/acts/%s/runs" % actor.replace("/", "~"), query={"desc": "1", "limit": str(limit)})
        items = (data.get("data") or {}).get("items") if isinstance(data, dict) else None
        return items or []

    def dataset(self, dataset_id, max_items=20000):
        if not re.fullmatch(r"[A-Za-z0-9]+", dataset_id or ""):
            raise ApifyError("invalid dataset id")
        items = []
        for offset in range(0, max_items, 500):
            batch = self._open("GET", "/v2/datasets/%s/items" % dataset_id,
                               query={"offset": offset, "limit": 500, "clean": "true"})
            if not isinstance(batch, list):
                raise ApifyError("unexpected dataset response")
            items.extend(batch)
            if len(batch) < 500:
                return items
        return items

    def log(self, run_id):
        return self._open("GET", "/v2/actor-runs/%s/log" % run_id, raw=True)


def known_schema(actor):
    """The actor's input properties as recorded when this version was released (offline checks and the fake)."""
    return (read_json(Path(__file__).resolve().parent / "data" / "actor-schemas.json", {}) or {}).get(actor, {})


def run_cost(run):
    """USD a finished run cost: Apify's usageTotalUsd, or charged events times their prices when that is higher."""
    usd = float(run.get("usageTotalUsd") or 0)
    prices = (((run.get("pricingInfo") or {}).get("pricingPerEvent") or {}).get("actorChargeEvents") or {})
    events = run.get("chargedEventCounts") or {}
    by_events = sum(float(n or 0) * float((prices.get(k) or {}).get("eventPriceUsd") or 0) for k, n in events.items())
    return round(max(usd, by_events), 4)


def item_price(run):
    prices = (((run.get("pricingInfo") or {}).get("pricingPerEvent") or {}).get("actorChargeEvents") or {})
    return float((prices.get("apify-default-dataset-item") or {}).get("eventPriceUsd") or 0)


class FakeApify:
    """A scripted stand-in for tests and demos: `datasets` maps a group name (matched by run input) to rows."""

    def __init__(self, datasets=None, schema=None, price=0.004, fail=None):
        self.datasets = datasets or {}
        self.schema = schema
        self.price = price
        self.fail = fail or {}
        self.started = []
        self._runs = {}

    def input_schema(self, actor):
        if self.schema is not None:
            return self.schema
        return known_schema(actor)

    def start(self, actor, run_input, *, max_charge_usd, timeout_s=900, memory_mb=1024, label=None):
        group = label
        if group in self.fail:
            raise ApifyError("fake rejection", http_status=self.fail[group])
        rows = list(self.datasets.get(group, []))
        run_id = "fake%04d" % (len(self._runs) + 1)
        paid = int(max(0.0, max_charge_usd - 0.001) / self.price) if self.price else len(rows)
        cost = round(0.001 + min(len(rows), paid) * self.price, 4)
        self._runs[run_id] = {"id": run_id, "status": "SUCCEEDED", "defaultDatasetId": "ds" + run_id,
                              "usageTotalUsd": cost, "startedAt": "", "rows": rows}
        self.started.append({"actor": actor, "input": run_input, "cap": max_charge_usd, "run_id": run_id})
        return {"id": run_id, "status": "READY", "defaultDatasetId": "ds" + run_id}

    def run(self, run_id):
        r = self._runs[run_id]
        return {k: v for k, v in r.items() if k != "rows"}

    def recent_runs(self, actor, limit=20):
        return [self.run(k) for k in self._runs]

    def dataset(self, dataset_id, max_items=20000):
        return list(self._runs[dataset_id[2:]]["rows"])

    def log(self, run_id):
        return ""
