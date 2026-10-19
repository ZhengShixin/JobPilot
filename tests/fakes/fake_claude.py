#!/usr/bin/env python3
"""A fake `claude` CLI for tests: answers `claude -p` with a scripted structured output.

The role is read from the system prompt's "# Role: <name>" line. $FAKE_CLAUDE_DIR/<role>.json is returned as the
structured output, or $FAKE_CLAUDE_DIR/<role>.py is executed and its respond(task, system, schema) result returned.
`--help` and `--version` print enough for capability checks. Every call is logged to $FAKE_CLAUDE_DIR/calls.jsonl.
"""
import json
import os
import re
import sys


def main(argv):
    if "--version" in argv:
        print("9.9.9 (Claude Code, fake)")
        return 0
    if "--help" in argv:
        print("--effort --exclude-dynamic-system-prompt-sections --no-chrome --json-schema")
        return 0
    args = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--") and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            args[a] = argv[i + 1]
            i += 2
        elif a == "-p" and i + 1 < len(argv):
            args["-p"] = argv[i + 1]
            i += 2
        else:
            args[a] = True
            i += 1
    system = args.get("--append-system-prompt", "")
    m = re.search(r"#\s*Role:\s*([a-z0-9-]+)", system)
    role = m.group(1) if m else "unknown"
    d = os.environ.get("FAKE_CLAUDE_DIR", "")
    schema = json.loads(args.get("--json-schema") or "{}")
    with open(os.path.join(d, "calls.jsonl"), "a") as f:
        f.write(json.dumps({"role": role, "model": args.get("--model"), "task_chars": len(args.get("-p", ""))}) + "\n")
    out = None
    if os.path.exists(os.path.join(d, role + ".json")):
        out = json.load(open(os.path.join(d, role + ".json")))
    elif os.path.exists(os.path.join(d, role + ".py")):
        scope = {}
        exec(open(os.path.join(d, role + ".py")).read(), scope)  # noqa: S102 - test fixture
        out = scope["respond"](args.get("-p", ""), system, schema)
    if out is None:
        print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                          "result": "no fake response for role %s" % role}))
        return 1
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": out,
                      "total_cost_usd": 0.0123, "num_turns": 1,
                      "usage": {"input_tokens": 1000, "output_tokens": 200}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
