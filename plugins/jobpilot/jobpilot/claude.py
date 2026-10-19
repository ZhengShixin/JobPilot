"""One-shot headless Claude runs (`claude -p`) whose answer must fit a JSON schema.

Every run starts in an empty temporary directory with no tools, no MCP servers and no project settings, so the only
thing the model can do is answer through StructuredOutput. Inputs go into the prompt inside <untrusted> tags; the
answer is validated here again. Each run appends one line to data/claude-runs.jsonl (role, model, cost, seconds).

    res = claude.run(ws, "jd-review", task, system=prompt, schema=SCHEMA, model="sonnet")
    res.ok, res.output, res.error, res.cost_usd

Tests and demos point JOBPILOT_CLAUDE at a fake binary.
"""
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

from . import schema as schemalib, util

DROP_ENV_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_PLUGIN_", "CLAUDE_PROJECT_")
KEEP_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
_HELP = {}


@dataclass
class Result:
    ok: bool
    output: object = None
    error: str = ""
    cost_usd: float = None
    seconds: float = 0.0
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def binary():
    path = os.environ.get("JOBPILOT_CLAUDE") or shutil.which("claude")
    if not path:
        for cand in (os.path.expanduser("~/.local/bin/claude"), os.path.expanduser("~/.claude/local/claude"),
                     "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
            if os.path.exists(cand):
                return cand
    return path


def supports(flag):
    """Whether this Claude Code version knows a CLI flag (read once from `claude --help`)."""
    claude = binary()
    if not claude:
        return False
    if claude not in _HELP:
        try:
            _HELP[claude] = subprocess.run([claude, "--help"], capture_output=True, text=True, timeout=30,
                                           env=_env()).stdout
        except (OSError, subprocess.SubprocessError):
            _HELP[claude] = ""
    return flag in _HELP[claude]


def _env():
    env = {}
    for k, v in os.environ.items():
        if k == "CLAUDECODE" or (k.startswith(DROP_ENV_PREFIXES) and k not in KEEP_ENV):
            continue
        env[k] = v
    return env


def _parse(stdout):
    """The final result event of `--output-format json` (one object, or a list of events in some versions)."""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        for line in reversed(text.splitlines()):
            try:
                data = json.loads(line)
                break
            except ValueError:
                continue
        else:
            return None
    if isinstance(data, list):
        data = next((e for e in reversed(data) if isinstance(e, dict) and e.get("type") == "result"), None)
    return data if isinstance(data, dict) else None


def run(ws, role, task, *, system, schema, model="sonnet", effort="", max_turns=4, timeout=600):
    claude = binary()
    t0 = time.monotonic()
    res = Result(ok=False)
    if not claude:
        res.error = "claude_not_found: install Claude Code (https://claude.com/claude-code) and log in"
        return _log(ws, role, model, res, t0)
    if not isinstance(task, str) or not task.strip() or task.lstrip().startswith("-"):
        raise ValueError("task must be non-empty text that does not start with '-'")
    schema_text = json.dumps(schema, separators=(",", ":"), sort_keys=True)
    cmd = [claude, "-p", task, "--output-format", "json", "--json-schema", schema_text, "--model", str(model),
           "--max-turns", str(int(max_turns)), "--permission-mode", "dontAsk", "--no-session-persistence",
           "--append-system-prompt", system, "--tools", "", "--strict-mcp-config", "--mcp-config",
           '{"mcpServers":{}}', "--setting-sources", "project"]
    if effort and supports("--effort"):
        cmd += ["--effort", str(effort)]
    if supports("--exclude-dynamic-system-prompt-sections"):
        cmd.append("--exclude-dynamic-system-prompt-sections")  # batches of one role then share the prompt cache
    if supports("--no-chrome"):
        cmd.append("--no-chrome")
    workdir = tempfile.mkdtemp(prefix="jobpilot-%s-" % role)
    try:
        proc = subprocess.Popen(cmd, cwd=workdir, env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                start_new_session=True)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            out, err = proc.communicate()
            res.error = "timeout after %ds" % timeout
            return _log(ws, role, model, res, t0)
    except OSError as exc:
        res.error = "launch_failed: %s" % exc
        return _log(ws, role, model, res, t0)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    data = _parse(out) or {}
    res.raw = {k: data.get(k) for k in ("subtype", "is_error", "num_turns", "session_id") if k in data}
    res.cost_usd = data.get("total_cost_usd")
    res.usage = data.get("usage") or {}
    if proc.returncode != 0 and not data:
        tail = (err or "").strip().splitlines()[-3:]
        res.error = "exit_%s: %s" % (proc.returncode, " | ".join(tail)[:300])
    elif data.get("is_error") or data.get("subtype") not in (None, "success"):
        res.error = "claude_error: %s %s" % (data.get("subtype"), util.clip(str(data.get("result") or ""), 200))
    elif data.get("structured_output") is None:
        res.error = "no_structured_output"
    else:
        problems = schemalib.validate(data["structured_output"], schema)
        res.output = data["structured_output"]
        if problems:
            res.error = "invalid_output: " + "; ".join(problems[:5])
        else:
            res.ok = True
    return _log(ws, role, model, res, t0)


def _kill(proc):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _log(ws, role, model, res, t0):
    res.seconds = round(time.monotonic() - t0, 2)
    if ws is not None:
        try:
            util.jsonl_append(ws.claude_runs_path, {
                "role": role, "model": model, "ok": res.ok, "error": res.error or None, "cost_usd": res.cost_usd,
                "seconds": res.seconds,
                "tokens": {k: res.usage.get(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                         "cache_creation_input_tokens") if k in res.usage}})
        except OSError:
            pass
    return res


def untrusted(source, text, **attrs):
    """Wrap external text (job descriptions, profiles, emails) so the role prompt can tell data from instructions."""
    body = (text or "").replace("</untrusted", "&lt;/untrusted")
    extra = "".join(' %s="%s"' % (k, str(v).replace('"', "'")) for k, v in attrs.items())
    return '<untrusted source="%s"%s>\n%s\n</untrusted>' % (source, extra, body)
