"""Tests for Kuakgan: small Claude Code and Codex logs are written to a temporary
folder, indexed, and the answers checked. Run with `python -m unittest discover tests`."""
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="kuakgan-test-"))
os.environ["KUAKGAN_DB"] = str(TMP / "index.db")
os.environ["KUAKGAN_CLAUDE_DIR"] = str(TMP / "claude")
os.environ["KUAKGAN_CODEX_DIR"] = str(TMP / "codex")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
kuakgan = importlib.import_module("kuakgan")

PROJECT = str(TMP / "work" / "shop")
START = datetime.now(timezone.utc) - timedelta(hours=2)


def ts(seconds: float) -> str:
    return (START + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


class ClaudeLog:
    """Writes a Claude Code session log the way Claude Code does."""

    def __init__(self, session: str, title: str = ""):
        self.session, self.lines, self.t, self.n = session, [], 0.0, 0
        if title:
            self.lines.append({"type": "custom-title", "customTitle": title, "sessionId": session})
        self.user("Please fix the checkout page")

    def user(self, text):
        self.t += 1
        self.lines.append({"type": "user", "timestamp": ts(self.t), "cwd": PROJECT, "sessionId": self.session,
                           "message": {"role": "user", "content": text}})

    def tool(self, name, inp, result="ok", error=False, model="claude-opus-5-5"):
        self.t += 2
        self.n += 1
        mid, tid = f"msg_{self.session}_{self.n}", f"toolu_{self.session}_{self.n}"
        usage = {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 1000,
                 "cache_creation_input_tokens": 200, "cache_creation": {"ephemeral_1h_input_tokens": 200}}
        # Claude Code writes one line per content block, each repeating the usage.
        for block in ({"type": "text", "text": "Working on it."},
                      {"type": "tool_use", "id": tid, "name": name, "input": inp}):
            self.lines.append({"type": "assistant", "timestamp": ts(self.t), "cwd": PROJECT,
                               "sessionId": self.session,
                               "message": {"id": mid, "model": model, "role": "assistant",
                                           "content": [block], "usage": usage}})
        self.t += 1
        self.lines.append({"type": "user", "timestamp": ts(self.t), "cwd": PROJECT, "sessionId": self.session,
                           "message": {"role": "user", "content": [
                               {"type": "tool_result", "tool_use_id": tid, "content": result,
                                "is_error": error}]}})

    def write(self):
        folder = TMP / "claude" / "projects" / "C--work-shop"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.session}.jsonl"
        path.write_text("\n".join(json.dumps(line) for line in self.lines) + "\n", encoding="utf-8")
        return path


def write_codex_log():
    folder = TMP / "codex" / "sessions" / "2026" / "11" / "19"
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        {"timestamp": ts(0), "type": "session_meta",
         "payload": {"id": "codex-1", "cwd": PROJECT, "originator": "codex_exec"}},
        {"timestamp": ts(1), "type": "turn_context", "payload": {"model": "gpt-6.2-sol", "cwd": PROJECT}},
        {"timestamp": ts(2), "type": "event_msg", "payload": {"type": "user_message", "message": "Run the tests"}},
    ]
    t = 3
    total = 0
    for i in range(6):
        call_id = f"call_{i}"
        lines.append({"timestamp": ts(t), "type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command", "call_id": call_id,
            "arguments": json.dumps({"cmd": "tail -n 8 build.log"})}})
        total += 600
        usage = {"input_tokens": 500, "cached_input_tokens": 400, "output_tokens": 100}
        event = {"timestamp": ts(t + 1), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"total": total}, "last_token_usage": usage}}}
        lines.append(event)
        lines.append(event)  # Codex repeats the event; it must count once
        lines.append({"timestamp": ts(t + 2), "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": call_id,
            "output": "Process exited with code 0\nOutput:\nbuilding..."}})
        t += 5
    path = folder / "rollout-2026-11-19T10-00-00-codex-1.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def build_logs():
    a = ClaudeLog("s1", "Fix checkout")
    for _ in range(5):  # busy polling
        a.tool("Bash", {"command": f"cd {PROJECT} && tail -n 20 server.log"})
    for _ in range(4):  # the same file, read four times
        a.tool("Read", {"file_path": f"{PROJECT}/src/cart.ts"})
    a.tool("Bash", {"command": "git grep -n foo -A3 origin/main"},
           result="fatal: unable to resolve revision: -A3", error=True)
    a.tool("Bash", {"command": "git grep -n -A3 foo origin/main"}, result="src/a.ts:1:foo")
    script = "python - <<'PY'\nimport json\n" + "print(json.dumps({'a': 1}))\n" * 10 + "PY"
    for _ in range(3):
        a.tool("Bash", {"command": script})
    a.write()

    b = ClaudeLog("s2")
    b.tool("Bash", {"command": "git grep -n bar -A5 origin/main"},
           result="fatal: unable to resolve revision: -A5", error=True)
    b.tool("Bash", {"command": "git grep -n -A5 bar origin/main"}, result="src/b.ts:2:bar")
    b.tool("Bash", {"command": "cat big.json"}, result="x" * 80_000)
    b.write()

    sub = ClaudeLog("agent-abc")
    sub.tool("Grep", {"pattern": "foo"}, model="claude-haiku-5-5")
    folder = TMP / "claude" / "projects" / "C--work-shop" / "s2" / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "agent-abc.jsonl").write_text(
        "\n".join(json.dumps(line) for line in sub.lines) + "\n", encoding="utf-8")
    write_codex_log()


class KuakganTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_logs()
        kuakgan.update_index(quiet=True)
        cls.summary = kuakgan.summary(days=30)

    def finding(self, kind):
        return next((f for f in self.summary["findings"] if f["kind"] == kind), None)

    def test_sessions_and_subagents_found(self):
        ids = {s["id"] for s in kuakgan.list_sessions(days=30, limit=10)}
        self.assertEqual(ids, {"s1", "s2", "s2/agent-abc", "codex-1"})

    def test_a_day_is_shown_by_the_hour(self):
        day = kuakgan.summary(days=1)
        self.assertEqual(day["step"], "hour")
        self.assertTrue(all(len(r["day"]) == 13 for r in day["days"]))
        self.assertEqual(kuakgan.summary(days=7)["step"], "day")
        self.assertTrue(all(f.get("short") for f in day["findings"]))

    def test_period_is_at_most_thirty_days(self):
        for days in (0, None, 90):
            self.assertEqual(kuakgan.summary(days=days)["period_days"], 30)
        self.assertEqual(kuakgan.summary(days=7)["period_days"], 7)

    def test_claude_usage_counted_once_per_message(self):
        db = kuakgan.connect()
        row = db.execute("SELECT * FROM sessions WHERE id = 's1'").fetchone()
        calls = row["calls"]
        self.assertEqual(calls, 5 + 4 + 2 + 3)
        self.assertEqual(row["output"], 100 * calls)
        self.assertEqual(row["cache_read"], 1000 * calls)
        # 10 input at $4, 100 output at $20, 1000 cache reads at $0.20, 200 one-hour writes at $8.
        per_call = (10 * 4 + 100 * 20 + 1000 * 0.20 + 200 * 8) / 1e6
        self.assertAlmostEqual(row["cost"], per_call * calls, places=6)

    def test_codex_usage_without_double_counting(self):
        db = kuakgan.connect()
        row = db.execute("SELECT * FROM sessions WHERE id = 'codex-1'").fetchone()
        self.assertEqual(row["requests"], 6)
        self.assertEqual(row["input"], 6 * 100)       # cached input is not input
        self.assertEqual(row["cache_read"], 6 * 400)
        self.assertEqual(row["output"], 6 * 100)
        # 100 uncached input at $2, 400 cached at $0.10 and 100 output at $10, six times.
        self.assertAlmostEqual(row["cost"], 6 * (100 * 2 + 400 * 0.10 + 100 * 10) / 1e6, places=9)
        self.assertIsNone(kuakgan.price_for("made-up-model-1", kuakgan.load_prices()))

    def test_polling(self):
        f = self.finding("polling")
        self.assertIsNotNone(f)
        # Claude: five checks, two allowed; Codex: six checks, two allowed.
        self.assertEqual(f["calls"], 3 + 4)
        self.assertIn("codex", f["fix"])
        self.assertIn("background_terminal_max_timeout", f["fix"]["codex"])

    def test_rereads(self):
        f = self.finding("rereads")
        self.assertEqual(f["calls"], 3)
        self.assertEqual(f["worst"][0]["detail"]["files"][0]["reads"], 4)

    def test_repeated_failure_with_fix(self):
        f = self.finding("failures")
        repeated = f["repeated"][0]
        self.assertEqual(repeated["program"], "git")
        self.assertEqual(repeated["sessions"], 2)
        self.assertIn("-A", repeated["worked_instead"])
        self.assertTrue(repeated["worked_instead"].startswith("git grep -n -A"))

    def test_repeated_scripts(self):
        f = self.finding("scripts")
        self.assertEqual(f["worst"][0]["times"], 3)

    def test_big_output(self):
        f = self.finding("big_outputs")
        self.assertEqual(f["calls"], 1)

    def test_check_for_agents(self):
        answer = kuakgan.check(PROJECT)
        self.assertEqual(answer["project"], "shop")
        self.assertTrue(any("git" in tip and "worked" in tip for tip in answer["tips"]))
        text = kuakgan.check_text(answer)
        self.assertIn("Kuakgan, project shop", text)
        self.assertLess(len(text), 4000)

    def test_index_reads_only_changed_files(self):
        self.assertEqual(kuakgan.update_index(quiet=True)["read"], 0)
        path = TMP / "claude" / "projects" / "C--work-shop" / "s1.jsonl"
        os.utime(path, (time.time(), time.time() + 5))
        self.assertEqual(kuakgan.update_index(quiet=True)["read"], 1)
        db = kuakgan.connect()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions WHERE id = 's1'").fetchone()[0], 1)

    def test_history_kept_and_repriced(self):
        # Prices change: costs are worked out again, from the tokens already counted.
        db = kuakgan.connect()
        before = db.execute("SELECT cost FROM sessions WHERE id = 'codex-1'").fetchone()[0]
        db.close()
        prices = TMP / "prices.json"
        prices.write_text(json.dumps({"gpt-6.2-sol": {"input": 4, "output": 20, "cache_read": 0.2}}))
        os.environ["KUAKGAN_PRICES"] = str(prices)
        try:
            db = kuakgan.connect()
            after = db.execute("SELECT cost FROM sessions WHERE id = 'codex-1'").fetchone()[0]
            self.assertAlmostEqual(after, 2 * before, places=9)
            db.close()
        finally:
            del os.environ["KUAKGAN_PRICES"]
            kuakgan.connect().close()
        # A deleted log keeps its sessions in the index.
        extra = ClaudeLog("gone-1")
        extra.tool("Bash", {"command": "ls"})
        path = extra.write()
        kuakgan.update_index(quiet=True)
        path.unlink()
        kuakgan.update_index(quiet=True)
        db = kuakgan.connect()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions WHERE id = 'gone-1'").fetchone()[0], 1)
        db.execute("DELETE FROM sessions WHERE id = 'gone-1'")
        db.commit()

    def test_summary_shape(self):
        s = self.summary
        self.assertEqual(s["totals"]["sessions"], 4)
        self.assertTrue(s["days"])
        self.assertEqual(s["projects"][0]["project"], "shop")
        self.assertIn("claude-opus-5-5", {m["model"] for m in s["models"]})
        json.dumps(s)  # the dashboard serves it as JSON


class MachinesTest(unittest.TestCase):
    """Another machine, played by this one: the SSH command is replaced by running
    Python here, so the source travels over stdin exactly as it would over SSH."""

    def setUp(self):
        self.original = kuakgan.ssh_command
        os.environ["KUAKGAN_MACHINES"] = str(TMP / "machines.json")
        kuakgan.MACHINES_PATH = Path(os.environ["KUAKGAN_MACHINES"])

        def local(machine, remote):
            if kuakgan.REMOTE_BOOT in remote:
                return [sys.executable, "-c", kuakgan.REMOTE_BOOT]
            return [sys.executable, "-c", "import sys;print(sys.version_info[:2]>=(3,9))"]
        kuakgan.ssh_command = local

    def tearDown(self):
        kuakgan.ssh_command = self.original

    def test_add_pull_and_remove(self):
        build_logs()
        kuakgan.update_index(quiet=True)
        self.assertEqual(kuakgan.main(["machines", "add", "twin", "example.invalid"]), 0)
        db = kuakgan.connect()
        rows = db.execute("SELECT id, parent FROM sessions WHERE machine = 'twin'").fetchall()
        self.assertEqual({r["id"] for r in rows}, {"twin:s1", "twin:s2", "twin:s2/agent-abc", "twin:codex-1"})
        self.assertIn("twin:s2", {r["parent"] for r in rows})
        machines = {m["machine"]: m for m in kuakgan.summary(days=30)["machines"]}
        self.assertEqual(machines["twin"]["tokens"], machines["this computer"]["tokens"])
        # A mistake made on both machines now counts as made in four sessions.
        self.assertEqual(kuakgan.repeated_failures(db, 30, None)[0]["sessions"], 4)
        # Nothing changed there, so nothing is read again.
        self.assertEqual(kuakgan.pull_machine(db, kuakgan.load_machines()[0], quiet=True)["read"], 0)
        db.close()
        kuakgan.main(["machines", "remove", "twin"])
        db = kuakgan.connect()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions WHERE machine = 'twin'").fetchone()[0], 0)
        self.assertEqual(kuakgan.load_machines(), [])


class ClassifyTest(unittest.TestCase):
    def kind(self, cmd):
        return kuakgan.classify_command(cmd)[0]

    def test_kinds(self):
        self.assertEqual(self.kind("cd /x && tail -n 8 run.log; tail inbox.md"), "observe")
        self.assertEqual(self.kind("ssh build-pc 'tail -n 20 /tmp/lane.log'"), "observe")
        self.assertEqual(self.kind("cd /x && git status"), "run")
        self.assertEqual(self.kind("sleep 120; tail -5 a.log"), "wait")
        self.assertEqual(self.kind("until grep -q done x.log; do sleep 5; done"), "wait")
        self.assertEqual(self.kind("cat README.md"), "read")
        self.assertEqual(self.kind("Get-Content -Raw C:/a/b.md"), "read")
        self.assertEqual(self.kind("python3 - <<'PY'\n" + "x = 1\n" * 40 + "PY"), "script")
        self.assertEqual(self.kind("git commit -m 'run node -e later'"), "run")

    def test_prefix_and_errors(self):
        self.assertEqual(kuakgan.strip_prefix("cd C:/a && K=x; ssh h ls"), "ssh h ls")
        self.assertEqual(kuakgan.error_line("Exit code 1\nTraceback\nValueError: bad"), "ValueError: bad")
        a = kuakgan.failure_key("python", "UnicodeEncodeError: 'charmap' codec can't encode character '\\u2212' in position 73")
        b = kuakgan.failure_key("python", "UnicodeEncodeError: 'charmap' codec can't encode character '\\u00e9' in position 5")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
