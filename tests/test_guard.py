"""The PreToolUse guard: secrets everywhere; ledgers, outgoing mail and LinkedIn automation inside a workspace."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import support

GUARD = support.PLUGIN / "hooks" / "guard.py"


def run(tool, inp, cwd):
    p = subprocess.run([sys.executable, "-I", str(GUARD)], input=json.dumps(
        {"tool_name": tool, "tool_input": inp, "cwd": cwd, "hook_event_name": "PreToolUse"}),
        capture_output=True, text=True, timeout=20)
    if not p.stdout.strip():
        return "allow"
    return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"]


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.ws = support.make_ws()
        self.root = str(self.ws.root)
        self.outside = tempfile.mkdtemp()

    def test_secrets(self):
        self.assertEqual(run("Read", {"file_path": os.path.expanduser("~/.config/jobpilot/apify-token")},
                             self.outside), "deny")
        self.assertEqual(run("Bash", {"command": "cat ~/.config/jobpilot/apify-token"}, self.outside), "deny")
        self.assertEqual(run("Bash", {"command": "jobpilot token status"}, self.outside), "allow")
        self.assertEqual(run("Read", {"file_path": self.root + "/.env"}, self.root), "deny")
        self.assertEqual(run("Bash", {"command": "cat .env"}, self.root), "deny")
        self.assertEqual(run("Bash", {"command": "ls -la"}, self.root), "allow")

    def test_ledgers(self):
        self.assertEqual(run("Write", {"file_path": self.root + "/data/apps.jsonl", "content": "{}"}, self.root),
                         "deny")
        self.assertEqual(run("Bash", {"command": "echo '{}' >> data/apps.jsonl"}, self.root), "deny")
        self.assertEqual(run("Bash", {"command": "jobpilot app list --json"}, self.root), "allow")
        self.assertEqual(run("Edit", {"file_path": self.root + "/jobpilot.toml"}, self.root), "allow")
        self.assertEqual(run("Write", {"file_path": self.outside + "/data/apps.jsonl"}, self.outside), "allow")

    def test_mail_and_linkedin(self):
        self.assertEqual(run("mcp__claude_ai_Gmail__send_message", {}, self.root), "deny")
        self.assertEqual(run("mcp__claude_ai_Gmail__label_message", {}, self.root), "deny")
        self.assertEqual(run("mcp__claude_ai_Gmail__search_threads", {"query": "after:1"}, self.root), "allow")
        self.assertEqual(run("mcp__claude_ai_Gmail__list_labels", {}, self.root), "allow")
        self.assertEqual(run("mcp__claude_ai_Gmail__send_message", {}, self.outside), "allow")
        self.assertEqual(run("mcp__claude-in-chrome__navigate", {"url": "https://www.linkedin.com/in/x"}, self.root),
                         "deny")
        self.assertEqual(run("mcp__claude-in-chrome__navigate", {"url": "https://example.com"}, self.root), "allow")

    def test_bad_input_allows(self):
        p = subprocess.run([sys.executable, "-I", str(GUARD)], input="not json", capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
