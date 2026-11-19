#!/usr/bin/env python3
"""Kuakgan: see where your Claude Code and Codex tokens go, and what your agents waste.

Reads the session logs Claude Code and Codex already write on this computer, keeps
a small local index of them, and answers as JSON: tokens and estimated cost over
time per model, project and session; waste patterns such as busy polling, files
read again and again, the same helper script rewritten, and commands that keep
failing; and, for an agent at work, the mistakes earlier sessions made in the
same project. `kuakgan dashboard` shows it all in the browser. Nothing leaves
the computer. Standard library only.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import difflib
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import sys
import time
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

# On another machine the source runs without a file of its own (see REMOTE_BOOT).
HERE = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
DB_PATH = Path(os.environ.get("KUAKGAN_DB", HERE / ".kuakgan.db"))
MACHINES_PATH = Path(os.environ.get("KUAKGAN_MACHINES", HERE / "kuakgan.json"))
PULL_EVERY = 600  # seconds between fetching from other machines, except for `index`
INDEX_VERSION = 9  # bump when parsing changes; the index is then rebuilt


def claude_root() -> Path:
    base = os.environ.get("KUAKGAN_CLAUDE_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "projects"


def codex_roots() -> list[Path]:
    base = Path(os.environ.get("KUAKGAN_CODEX_DIR") or os.environ.get("CODEX_HOME")
                or Path.home() / ".codex")
    return [base / "sessions", base / "archived_sessions"]


ABOUT = ("Kuakgan is open source, built by the team behind Kullevi "
         "(https://kullevi.com), which builds AI that runs your company for you.")

# ---------- prices ----------

# Dollars per million tokens: input, output, cache write (5 minutes), cache write
# (1 hour), cache read, at Anthropic's and OpenAI's list prices; the longest matching
# prefix wins. Models without a price count tokens but no cost.
# Add or override prices in prices.json next to this file (see README).
PRICES = {
    "claude-fable-5-2": (8, 40, 10, 16, 0.20), "claude-mythos-5-2": (8, 40, 10, 16, 0.20),
    "claude-fable-5": (10, 50, 12.5, 20, 1.0), "claude-mythos-5": (10, 50, 12.5, 20, 1.0),
    "claude-opus-5-5": (4, 20, 5, 8, 0.20), "claude-opus-5": (5, 25, 6.25, 10, 0.5),
    "claude-opus-4-8": (5, 25, 6.25, 10, 0.5), "claude-opus-4-7": (5, 25, 6.25, 10, 0.5),
    "claude-opus-4-6": (5, 25, 6.25, 10, 0.5), "claude-opus-4-5": (5, 25, 6.25, 10, 0.5),
    "claude-opus-4": (15, 75, 18.75, 30, 1.5),
    "claude-sonnet-5-5": (2, 10, 2.5, 4, 0.10), "claude-sonnet-5": (2, 10, 2.5, 4, 0.2),
    "claude-sonnet-4": (3, 15, 3.75, 6, 0.3), "claude-3-7-sonnet": (3, 15, 3.75, 6, 0.3),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.2, 0.01), "claude-haiku-4-5": (1, 5, 1.25, 2, 0.1),
    "claude-3-5-haiku": (0.8, 4, 1, 1.6, 0.08),
    # OpenAI, from developers.openai.com/api/docs/pricing (standard tier, up to 272K input
    # tokens). OpenAI has no separate cache-write price, so writes count as input.
    "gpt-6.2-astra": (8, 40, 8, 8, 0.40), "gpt-6-astra": (10, 50, 10, 10, 1.0),
    "gpt-6.2-sol": (2, 10, 2, 2, 0.10), "gpt-6.1-sol": (2, 10, 2, 2, 0.10),
    "gpt-6-sol": (2, 10, 2, 2, 0.20), "gpt-6.1-terra": (1, 5, 1, 1, 0.05),
    "gpt-6-luna": (0.10, 0.50, 0.10, 0.10, 0.01),
    "gpt-5.6-sol": (4, 20, 4, 4, 0.40), "gpt-5.6-terra": (2, 12, 2, 2, 0.20),
    "gpt-5.6-luna": (0.20, 1.20, 0.20, 0.20, 0.02),
    "gpt-5.5": (5, 30, 5, 5, 0.50), "gpt-5.4-mini": (0.75, 4.5, 0.75, 0.75, 0.075),
    "gpt-5.4-nano": (0.20, 1.25, 0.20, 0.20, 0.02), "gpt-5.4": (2.5, 15, 2.5, 2.5, 0.25),
    "gpt-5.2": (1.75, 14, 1.75, 1.75, 0.175), "gpt-5.1": (1.25, 10, 1.25, 1.25, 0.125),
    "gpt-5-mini": (0.25, 2, 0.25, 0.25, 0.025), "gpt-5-nano": (0.05, 0.40, 0.05, 0.05, 0.005),
    "gpt-5.3-codex": (1.75, 14, 1.75, 1.75, 0.175), "gpt-5-codex": (1.25, 10, 1.25, 1.25, 0.125),
    "gpt-5": (1.25, 10, 1.25, 1.25, 0.125),
}


def load_prices() -> dict:
    prices = dict(PRICES)
    with contextlib.suppress(OSError, ValueError, TypeError):
        extra = json.loads(Path(os.environ.get("KUAKGAN_PRICES", HERE / "prices.json")).read_text(encoding="utf-8"))
        for model, p in extra.items():
            if isinstance(p, dict):
                inp = float(p["input"])
                prices[model] = (inp, float(p["output"]), float(p.get("cache_write", inp * 1.25)),
                                 float(p.get("cache_write_1h", inp * 2)),
                                 float(p.get("cache_read", inp * 0.1)))
    return dict(sorted(prices.items(), key=lambda kv: -len(kv[0])))


def price_for(model: str, prices: dict):
    """The price of the longest name in the table that `model` is or starts with, as a
    whole part: gpt-5 prices gpt-5-mini-2025 but not gpt-5.7. A provider prefix such as
    `anthropic/` and dots in Claude versions (claude-opus-5.5) are ignored."""
    model = (model or "").lower().rsplit("/", 1)[-1]
    if model.startswith("claude-"):
        model = model.replace(".", "-")
    return next((p for name, p in prices.items() if model == name or model.startswith(name + "-")), None)


def cost_of(model: str, u: dict, prices: dict):
    """Estimated dollars for one model call's usage, or None without a price."""
    p = price_for(model, prices)
    if p is None:
        return None
    return (u["input"] * p[0] + u["output"] * p[1] + u["cache_write"] * p[2]
            + u.get("cache_write_1h", 0) * p[3] + u["cache_read"] * p[4]) / 1e6


# ---------- reading commands ----------

OBSERVE_PROGS = {"tail", "cat", "head", "less", "get-content", "gc", "type", "wc", "ls", "dir",
                 "sleep", "start-sleep", "ps", "tasklist", "get-process", "pgrep", "grep",
                 "select-string", "echo", "date", "stat", "test", "jobs", "get-job",
                 "receive-job", "wait", "true", "sed", "awk", "nvidia-smi", "uptime", "free",
                 "get-childitem", "find", "rg", "cut", "sort", "uniq", "tr", "kill"}
# Steps that only set the scene, such as changing folder; a command is judged by the rest.
NEUTRAL_PROGS = {"cd", "set-location", "sl", "pushd", "popd", "export", "set", "source", ".",
                 "chcp", "$erroractionpreference", "setlocal", ""}
READ_PROGS = {"cat", "get-content", "gc", "type", "head", "less", "sed", "bat"}
SCRIPT_RE = re.compile(r"(?:^|[;&|(]\s*|\n\s*)(?:\S*[/\\])?(python3?|py|node|deno|bun|ruby|perl|php|pwsh|powershell)(\.exe)?\s+"
                       r"(?:-(?![ce]\s)\w+\s+)*(-c|-e|--eval|(?i:-command)|-)?\s*(?:<<-?\s*['\"]?(\w+)['\"]?)?")
HEREDOC_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\s*\1\s*$", re.DOTALL | re.MULTILINE)
SPLIT_RE = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
LOG_RE = re.compile(r"\.(log|out|err|jsonl|output)\b|/tasks/|\\tasks\\|inbox|last\.md|progress",
                    re.IGNORECASE)
FLAG_TAIL_RE = re.compile(r"-tail\b|-wait\b|\btail\b|-f\b", re.IGNORECASE)


PREFIX_RE = re.compile(r"""^\s*(?:(?:cd|set-location|pushd|export)\s+(?:"[^"]*"|'[^']*'|\S+)"""
                       r"""|[A-Za-z_]\w*=(?:"[^"]*"|'[^']*'|\S*))\s*(?:&&|;|\n)\s*""", re.IGNORECASE)
ERROR_WORDS = re.compile(r"error|exception|not found|not recognized|denied|no such|cannot|can't|"
                         r"failed|invalid|unknown|refused|timed out|fatal|usage:", re.IGNORECASE)


def strip_prefix(cmd: str) -> str:
    """The command without leading `cd DIR &&` and `VAR=value;` steps, which say little."""
    while True:
        m = PREFIX_RE.match(cmd)
        if not m or m.end() >= len(cmd):
            return cmd.strip()
        cmd = cmd[m.end():]


def error_line(out: str) -> str:
    """The line of a failed tool's output that best says what went wrong."""
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not re.match(r"(Exit code|Process exited|Wall time|Output:|Chunk ID|"
                                               r"Script (completed|running))", ln)]
    hits = [ln for ln in lines if ERROR_WORDS.search(ln)]
    return ((hits or lines or [""])[-1 if hits else 0])[:200]


def first_word(segment: str) -> str:
    words = [w for w in segment.strip().split() if "=" not in w.split("/")[0] or w.startswith("-")]
    for w in words:
        w = w.strip("'\"()&").lower()
        if w in ("sudo", "env", "time", "nohup", "timeout", "then", "do", "{", "(", "!", "&"):
            continue
        if re.fullmatch(r"\d+[smh]?", w):  # the duration after `timeout`
            continue
        return w.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].removesuffix(".exe")
    return ""


def file_args(segment: str) -> list[str]:
    out = []
    for w in re.findall(r"""'[^']*'|"[^"]*"|\S+""", segment)[1:]:
        w = w.strip("'\"")
        if w.startswith("-") or not w or w in (">", "2>&1", "<", "|"):
            continue
        if "/" in w or "\\" in w or re.search(r"\.\w{1,5}$", w):
            out.append(w.replace("\\", "/"))
    return out


def classify_command(cmd: str) -> tuple[str, str, str, str | None]:
    """Sorts a shell command into kind, target, program and script hash.

    kind is 'script' (an inline program such as `python - <<PY`), 'read' (showing
    one whole file), 'observe' (only looking: tail, cat, ls, ps and the like, as
    when checking on something running), 'wait' (a loop or a long sleep that waits
    by itself) or 'run'."""
    text = cmd.strip()
    prog = first_word(text.split("\n", 1)[0])
    m = SCRIPT_RE.search(text)
    if m and (m.group(3) or m.group(4)):
        body = ""
        rest = text[m.start():]
        doc = HEREDOC_RE.search(rest)
        if doc:
            body = doc.group(2)
        else:
            q = re.search(r"""(?:-c|-e|--eval|(?i:-command))\s+(['"])(.*)\1""", rest, re.DOTALL)
            body = q.group(2) if q else ""
        if len(body) > 120:
            norm = re.sub(r"\s+", " ", re.sub(r"(?m)#.*$", "", body)).strip()
            return "script", body.strip()[:200], m.group(1).lower(), hashlib.sha1(norm.encode()).hexdigest()[:16]
    segments = [s for s in SPLIT_RE.split(text) if s.strip()]
    pairs = [(s, first_word(s)) for s in segments]
    pairs = [(s, p) for s, p in pairs if p not in NEUTRAL_PROGS]
    segments, progs = [s for s, _ in pairs], [p for _, p in pairs]
    if not progs:
        return "observe", re.sub(r"\s+", " ", text)[:200], prog, None
    prog = progs[0]
    if prog == "ssh":  # judge the command run on the other machine
        remote = re.findall(r"""'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)\"""", text)
        inner = max((a or b for a, b in remote), key=len, default="")
        if inner:
            kind, target, _, script = classify_command(inner)
            return kind, target, "ssh", script
    if progs[0] in ("until", "while", "for") and "sleep" in text.lower() or any(
            p in ("sleep", "start-sleep") and int((re.findall(r"\d+", s) or ["0"])[0]) >= 30
            for s, p in zip(segments, progs)):
        return "wait", "", prog, None  # waits by itself: not polling
    if all(p in OBSERVE_PROGS for p in progs) and not re.search(r"(?<![\d&>])>>?\s*(?!&)[^\s|]", text):
        files = [f for s in segments for f in file_args(s)]
        if (len(segments) <= 2 and progs[0] in READ_PROGS and len(files) == 1
                and not FLAG_TAIL_RE.search(text) and not LOG_RE.search(files[0])
                and not re.search(r"\bsed\s+-n\s+'?\d+,\d+p", text)):
            return "read", files[0], prog, None
        return "observe", re.sub(r"\s+", " ", text)[:200], prog, None
    return "run", "", prog, None


def norm_path(p: str) -> str:
    return (p or "").replace("\\", "/").rstrip("/")


# ---------- parsing Claude Code logs ----------

def parse_ts(s) -> float | None:
    if not s:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    return None


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    if isinstance(content, dict):
        return json.dumps(content)
    return ""


def new_session(sid: str, source: str, path: str) -> dict:
    return {"id": sid, "source": source, "file": path, "parent": None, "cwd": "", "title": "",
            "first_prompt": "", "first_ts": None, "last_ts": None, "user_msgs": 0,
            "compactions": 0, "client": "", "calls": [], "usage": defaultdict(lambda: [0, 0, 0, 0, 0, 0.0]),
            "unpriced": 0, "last_context": 0}


def touch(s: dict, ts):
    if ts is None:
        return
    s["first_ts"] = ts if s["first_ts"] is None else min(s["first_ts"], ts)
    s["last_ts"] = ts if s["last_ts"] is None else max(s["last_ts"], ts)


def hour_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H")


def add_usage(s: dict, ts, model: str, u: dict, prices: dict) -> float:
    cost = cost_of(model, u, prices)
    row = s["usage"][(hour_of(ts or s["last_ts"] or 0), model or "unknown")]
    row[0] += u["input"]; row[1] += u["cache_read"]; row[2] += u["cache_write"] + u.get("cache_write_1h", 0)
    row[3] += u["output"]; row[4] += 1
    if cost is None:
        s["unpriced"] += u["input"] + u["output"] + u["cache_read"] + u["cache_write"]
    else:
        row[5] += cost
    s["last_context"] = u["input"] + u["cache_read"] + u["cache_write"] + u.get("cache_write_1h", 0)
    return cost or 0.0


def make_call(s: dict, ts, tool: str, inp) -> dict:
    kind, target, prog, script = "other", "", "", None
    text = ""
    t = tool.lower()
    if t in ("read", "notebookread", "view"):
        kind, target = "read", norm_path(inp.get("file_path") or inp.get("path") or "")
    elif t in ("edit", "write", "multiedit", "notebookedit", "apply_patch", "str_replace_based_edit_tool"):
        kind = "edit"
        target = norm_path(inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or "")
        if t == "apply_patch":
            paths = re.findall(r"\*\*\* (?:Update|Add|Delete) File: (.+)", inp.get("input", "") if isinstance(inp, dict) else str(inp))
            target = norm_path(paths[0]) if paths else ""
    elif t in ("bash", "powershell", "exec_command", "shell", "local_shell", "shell_command", "run_command"):
        text = inp.get("command") or inp.get("cmd") or ""
        if isinstance(text, list):
            text = text[-1] if len(text) >= 3 and text[0] in ("bash", "sh", "zsh", "powershell", "pwsh") else " ".join(text)
        kind, target, prog, script = classify_command(str(text))
    elif t in ("bashoutput", "taskoutput", "wait", "write_stdin", "monitor", "read_terminal"):
        kind = "poll"
        target = str(inp.get("bash_id") or inp.get("task_id") or inp.get("session_id")
                     or inp.get("cell_id") or "")
        if t == "write_stdin" and inp.get("chars"):
            kind = "run"
    elif t in ("grep", "glob", "search", "ls"):
        kind = "search"
    call = {"seq": len(s["calls"]), "ts": ts, "tool": tool, "kind": kind, "target": target[:300],
            "prog": prog, "text": strip_prefix(str(text))[:400], "dur": None, "err": 0, "err_text": "", "out_tok": 0,
            "turn_cost": 0.0, "turn_tok": 0, "script": script}
    s["calls"].append(call)
    return call


def parse_claude(path: str, prices: dict) -> list[dict]:
    p = Path(path)
    parent = None
    if p.parent.name == "subagents":
        parent = p.parent.parent.name
        sid = f"{parent}/{p.stem}"
    else:
        sid = p.stem
    s = new_session(sid, "claude", path)
    s["parent"] = parent
    by_tool_id = {}
    msg_calls = defaultdict(list)
    msg_cost = {}
    seen_usage = set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith('{"type":"api-request') or line.startswith('{"type":"queue'):
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            kind = o.get("type")
            if kind == "custom-title":
                s["title"] = o.get("customTitle") or s["title"]
                continue
            if kind == "agent-name" and not s["title"]:
                s["title"] = o.get("agentName") or ""
                continue
            if kind == "system" and o.get("subtype") == "compact_boundary":
                s["compactions"] += 1
            if kind not in ("user", "assistant"):
                continue
            ts = parse_ts(o.get("timestamp"))
            touch(s, ts)
            if not s["cwd"] and o.get("cwd"):
                s["cwd"] = o["cwd"]
                s["client"] = o.get("entrypoint") or ""
            msg = o.get("message") or {}
            content = msg.get("content")
            if kind == "assistant":
                mid = msg.get("id") or o.get("uuid")
                usage = msg.get("usage")
                if usage and mid not in seen_usage:
                    seen_usage.add(mid)
                    cc = usage.get("cache_creation") or {}
                    one_hour = cc.get("ephemeral_1h_input_tokens", 0) or 0
                    u = {"input": usage.get("input_tokens", 0) or 0,
                         "output": usage.get("output_tokens", 0) or 0,
                         "cache_read": usage.get("cache_read_input_tokens", 0) or 0,
                         "cache_write": (usage.get("cache_creation_input_tokens", 0) or 0) - one_hour,
                         "cache_write_1h": one_hour}
                    if msg.get("model") != "<synthetic>":
                        msg_cost[mid] = (add_usage(s, ts, msg.get("model"), u, prices),
                                         u["input"] + u["output"] + u["cache_read"] + u["cache_write"] + one_hour)
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        call = make_call(s, ts, b.get("name", ""), b.get("input") or {})
                        by_tool_id[b.get("id")] = call
                        msg_calls[mid].append(call)
            else:
                if isinstance(content, str):
                    if not o.get("isMeta") and not content.startswith("<"):
                        s["user_msgs"] += 1
                        s["first_prompt"] = s["first_prompt"] or content[:120]
                    continue
                for b in content if isinstance(content, list) else []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_result":
                        call = by_tool_id.get(b.get("tool_use_id"))
                        if call:
                            out = result_text(b.get("content"))
                            call["out_tok"] = len(out) // 4
                            call["err"] = 1 if b.get("is_error") else 0
                            if call["err"]:
                                call["err_text"] = error_line(out)
                            if ts and call["ts"]:
                                call["dur"] = int((ts - call["ts"]) * 1000)
                    elif b.get("type") == "text" and not o.get("isMeta") and o.get("origin", {}).get("kind", "human") == "human":
                        text = b.get("text", "")
                        if text and not text.startswith("<"):
                            s["user_msgs"] += 1
                            s["first_prompt"] = s["first_prompt"] or text[:120]
    for mid, calls in msg_calls.items():
        cost, tok = msg_cost.get(mid, (0.0, 0))
        for c in calls:
            c["turn_cost"], c["turn_tok"] = cost / len(calls), tok // len(calls)
    return [s]


# ---------- parsing Codex logs ----------

EXIT_RE = re.compile(r"(?:exited with code|Exit code:|\"exit_code\":)\s*(-?\d+)")
JS_CMD_RE = re.compile(r"""tools\.(\w+)\(\{\s*(?:cmd|command)\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`[^`]*`)""")


def codex_calls_from(payload: dict) -> list[tuple[str, dict]]:
    """The tool calls inside one Codex response item, as (tool, input) pairs."""
    kind = payload.get("type")
    name = payload.get("name") or ""
    if kind == "local_shell_call":
        return [("shell", (payload.get("action") or {}))]
    if kind == "function_call":
        try:
            args = json.loads(payload.get("arguments") or "{}")
        except ValueError:
            args = {}
        return [(name, args if isinstance(args, dict) else {})]
    if kind == "custom_tool_call":
        src = payload.get("input") or ""
        if name != "exec":
            return [(name, {"input": src})]
        found = []
        for tool, raw in JS_CMD_RE.findall(src):
            with contextlib.suppress(ValueError):
                raw = json.loads(raw) if raw.startswith('"') else raw[1:-1]
            found.append((tool, {"cmd": raw}))
        if re.search(r"tools\.(wait|write_stdin)\(", src) and not found:
            found.append(("wait", {}))
        return found or [("exec", {"input": src[:400]})]
    return []


def parse_codex(path: str, prices: dict) -> list[dict]:
    s = new_session(Path(path).stem.replace("rollout-", ""), "codex", path)
    model = ""
    pending = []
    by_call_id = {}
    last_total = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            kind = o.get("type")
            p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
            ts = parse_ts(o.get("timestamp"))
            touch(s, ts)
            if kind == "session_meta":
                s["id"] = p.get("id") or s["id"]
                s["cwd"] = p.get("cwd") or s["cwd"]
                s["client"] = p.get("originator") or ""
            elif kind == "turn_context":
                model = p.get("model") or model
                s["cwd"] = s["cwd"] or p.get("cwd") or ""
            elif kind == "compacted":
                s["compactions"] += 1
            elif kind == "event_msg":
                t = p.get("type")
                if t == "thread_settings_applied":
                    model = (p.get("thread_settings") or {}).get("model") or model
                elif t == "token_count":
                    info = p.get("info") or {}
                    total = info.get("total_token_usage")
                    last = info.get("last_token_usage")
                    if not last or total == last_total:
                        continue
                    last_total = total
                    cached = last.get("cached_input_tokens", 0) or 0
                    u = {"input": max((last.get("input_tokens", 0) or 0) - cached, 0),
                         "output": last.get("output_tokens", 0) or 0, "cache_read": cached,
                         "cache_write": last.get("cache_write_input_tokens", 0) or 0}
                    cost = add_usage(s, ts, model, u, prices)
                    tok = u["input"] + u["output"] + u["cache_read"] + u["cache_write"]
                    for c in pending:
                        c["turn_cost"], c["turn_tok"] = cost / len(pending), tok // len(pending)
                    pending = []
                elif t == "user_message":
                    text = p.get("message") or ""
                    if text and not text.startswith("<"):
                        s["user_msgs"] += 1
                        s["first_prompt"] = s["first_prompt"] or text[:120]
            elif kind == "response_item":
                t = p.get("type")
                if t in ("function_call", "custom_tool_call", "local_shell_call"):
                    for tool, inp in codex_calls_from(p):
                        call = make_call(s, ts, tool, inp)
                        pending.append(call)
                        by_call_id.setdefault(p.get("call_id") or p.get("id"), []).append(call)
                elif t in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
                    calls = by_call_id.get(p.get("call_id"), [])
                    out = p.get("output")
                    out = result_text(out) if not isinstance(out, str) else out
                    codes = [int(c) for c in EXIT_RE.findall(out or "")]
                    for i, call in enumerate(calls):
                        call["out_tok"] = len(out or "") // 4 // len(calls)
                        code = codes[i] if i < len(codes) else (codes[-1] if codes else 0)
                        call["err"] = 1 if code != 0 else 0
                        if call["err"]:
                            call["err_text"] = error_line(out)
                        if ts and call["ts"]:
                            call["dur"] = int((ts - call["ts"]) * 1000)
    return [s]


def parse_file(job: tuple[str, str, dict]) -> tuple[str, list[dict] | str]:
    path, source, prices = job
    try:
        sessions = (parse_claude if source == "claude" else parse_codex)(path, prices)
        for s in sessions:
            s["findings"] = session_findings(s)
            s["usage"] = dict(s["usage"])
        return path, sessions
    except Exception as error:  # one broken file must not stop the rest
        return path, f"{type(error).__name__}: {error}"


# ---------- findings in one session ----------

POLL_MIN = 4      # the same check this many times in a row counts as polling
REREAD_MIN = 3    # a file read this many times without being changed
BIG_OUTPUT = 10_000  # tokens in one tool result


CHECK_RE = re.compile(r"\b(tail|ps|pgrep|tasklist|get-process|get-job|receive-job|jobs|"
                      r"nvidia-smi|uptime|docker ps|docker logs|kill -0|test -e|wc -l)\b", re.IGNORECASE)


def is_check(c: dict) -> bool:
    """A look at running work (a log, a process, a background task), as opposed to
    browsing code; only these make up polling."""
    if c["kind"] == "poll":
        return True
    return c["kind"] == "observe" and bool(LOG_RE.search(c["target"]) or CHECK_RE.search(c["target"]))


def session_findings(s: dict) -> list[dict]:
    calls = s["calls"]
    found = []

    # Busy polling: the same check repeated while something runs.
    run = []

    def close(run):
        if len(run) >= POLL_MIN:
            wasted = run[2:]  # a check or two is fine
            found.append({"kind": "polling", "calls": len(wasted),
                          "cost": sum(c["turn_cost"] for c in wasted),
                          "tokens": sum(c["turn_tok"] for c in wasted),
                          "seconds": int(((run[-1]["ts"] or 0) - (run[0]["ts"] or 0))),
                          "detail": {"example": run[0]["text"] or run[0]["tool"], "repeats": len(run),
                                     "tool": run[0]["tool"]}})

    for c in calls:
        if is_check(c):
            key = c["target"] if c["kind"] == "poll" else re.sub(r"\d+", "N", c["target"])
            if run and run[-1]["_key"] == key:
                run.append(c)
            elif run and c["kind"] == run[-1]["kind"] == "observe":
                run.append(c)  # checking on the same work in a slightly different way
            else:
                close(run)
                run = [c]
            c["_key"] = key
        else:
            close(run)
            run = []
    close(run)
    for c in calls:
        c.pop("_key", None)

    # Files read again without having changed.
    extra = {}
    seen = defaultdict(int)
    wasted = []
    for c in calls:
        if c["kind"] == "edit" and c["target"]:
            seen[c["target"]] = 0
        elif c["kind"] == "read" and c["target"]:
            seen[c["target"]] += 1
            if seen[c["target"]] >= 2:
                wasted.append(c)
                extra[c["target"]] = extra.get(c["target"], 1) + 1
    top = sorted(((n, p) for p, n in extra.items() if n >= REREAD_MIN), reverse=True)
    if top:
        counted = [c for c in wasted if c["target"] in dict((p, n) for n, p in top)]
        found.append({"kind": "rereads", "calls": len(counted),
                      "cost": sum(c["turn_cost"] for c in counted),
                      "tokens": sum(c["out_tok"] for c in counted),
                      "seconds": 0, "detail": {"files": [{"path": p, "reads": n} for n, p in top[:5]]}})

    # Very large tool results, which every later turn carries along.
    big = [c for c in calls if c["out_tok"] >= BIG_OUTPUT]
    if big:
        found.append({"kind": "big_outputs", "calls": len(big), "cost": None,
                      "tokens": sum(c["out_tok"] for c in big), "seconds": 0,
                      "detail": {"examples": [{"tool": c["tool"], "text": c["text"][:160] or c["target"],
                                               "tokens": c["out_tok"]}
                                              for c in sorted(big, key=lambda c: -c["out_tok"])[:5]]}})

    # Commands that failed, and what worked right after.
    fixes = []
    for i, c in enumerate(calls):
        if not c["err"] or c["kind"] not in ("run", "script", "observe", "read") or not c["text"]:
            continue
        for d in calls[i + 1:i + 4]:
            if (d["tool"] == c["tool"] and d["prog"] == c["prog"] and d["text"] and not d["err"]
                    and d["text"] != c["text"]):
                a, b = c["text"][:200], d["text"][:200]
                if a == b:  # the difference lies past what is kept
                    break
                if difflib.SequenceMatcher(None, a, b).quick_ratio() >= 0.6 and \
                        difflib.SequenceMatcher(None, a, b).ratio() >= 0.5:
                    fixes.append({"failed": a, "worked": b})
                break
    failures = [c for c in calls if c["err"]]
    if failures:
        found.append({"kind": "failures", "calls": len(failures),
                      "cost": sum(c["turn_cost"] for c in failures),
                      "tokens": sum(c["turn_tok"] for c in failures), "seconds": 0,
                      "detail": {"fixes": fixes[:20]}})
    return found


# ---------- the index ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, source TEXT, size INTEGER, mtime REAL, error TEXT);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, file TEXT, source TEXT, parent TEXT,
  cwd TEXT, project TEXT, title TEXT, client TEXT, first_ts REAL, last_ts REAL, user_msgs INTEGER,
  compactions INTEGER, calls INTEGER, errors INTEGER, input INTEGER, cache_read INTEGER,
  cache_write INTEGER, output INTEGER, requests INTEGER, cost REAL, unpriced INTEGER,
  last_context INTEGER, models TEXT, machine TEXT);
CREATE TABLE IF NOT EXISTS usage (session TEXT, hour TEXT, model TEXT, input INTEGER,
  cache_read INTEGER, cache_write INTEGER, output INTEGER, requests INTEGER, cost REAL);
CREATE TABLE IF NOT EXISTS calls (session TEXT, seq INTEGER, ts REAL, tool TEXT, kind TEXT,
  target TEXT, prog TEXT, text TEXT, dur INTEGER, err INTEGER, out_tok INTEGER, turn_cost REAL,
  turn_tok INTEGER, script TEXT, err_text TEXT);
CREATE TABLE IF NOT EXISTS findings (session TEXT, kind TEXT, calls INTEGER, cost REAL,
  tokens INTEGER, seconds INTEGER, detail TEXT);
CREATE INDEX IF NOT EXISTS usage_s ON usage(session);
CREATE INDEX IF NOT EXISTS calls_s ON calls(session);
CREATE INDEX IF NOT EXISTS calls_script ON calls(script);
CREATE INDEX IF NOT EXISTS findings_s ON findings(session);
CREATE INDEX IF NOT EXISTS sessions_file ON sessions(file);
CREATE INDEX IF NOT EXISTS sessions_last ON sessions(last_ts);
CREATE INDEX IF NOT EXISTS files_path ON files(path);
CREATE INDEX IF NOT EXISTS calls_err ON calls(err);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=120)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    version = None
    with contextlib.suppress(sqlite3.Error):
        version = db.execute("SELECT value FROM meta WHERE key='version'").fetchone()
    if version is None or version[0] != str(INDEX_VERSION):
        for table in ("meta", "files", "sessions", "usage", "calls", "findings"):
            db.execute(f"DROP TABLE IF EXISTS {table}")
        db.executescript(SCHEMA)
        db.execute("INSERT INTO meta VALUES ('version', ?)", (str(INDEX_VERSION),))
        db.commit()
    prices = load_prices()
    wanted = hashlib.sha1(json.dumps(prices).encode()).hexdigest()[:12]
    have = db.execute("SELECT value FROM meta WHERE key = 'prices'").fetchone()
    if not have or have[0] != wanted:
        reprice(db, prices)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('prices', ?)", (wanted,))
        db.commit()
    return db


def reprice(db: sqlite3.Connection, prices: dict) -> None:
    """Works out every cost again from the stored token counts after prices change,
    without reading the logs again (some may be gone). Cache writes count at the
    five-minute price, since the index keeps one total for them."""
    for model, in db.execute("SELECT DISTINCT model FROM usage").fetchall():
        p = price_for(model, prices)
        if p is None:
            db.execute("UPDATE usage SET cost = 0 WHERE model = ?", (model,))
        else:
            db.execute("UPDATE usage SET cost = (input * ? + output * ? + cache_write * ? + cache_read * ?) / 1e6 "
                       "WHERE model = ?", (p[0], p[1], p[2], p[4], model))
    old = {r[0]: r[1] or 0 for r in db.execute("SELECT id, cost FROM sessions")}
    db.execute("UPDATE sessions SET cost = (SELECT COALESCE(SUM(cost), 0) FROM usage WHERE session = sessions.id)")
    unpriced = defaultdict(int)
    for session, model, tokens in db.execute(
            "SELECT session, model, SUM(input + cache_read + cache_write + output) FROM usage GROUP BY session, model"):
        if price_for(model, prices) is None:
            unpriced[session] += tokens
    db.execute("UPDATE sessions SET unpriced = 0")
    db.executemany("UPDATE sessions SET unpriced = ? WHERE id = ?", [(n, sid) for sid, n in unpriced.items()])
    # A step's cost is its share of its session's cost, so it changes with the session's.
    for sid, new in db.execute("SELECT id, cost FROM sessions").fetchall():
        before = old.get(sid, 0)
        if before > 0:
            ratio = (new or 0) / before
            db.execute("UPDATE calls SET turn_cost = turn_cost * ? WHERE session = ?", (ratio, sid))
            db.execute("UPDATE findings SET cost = cost * ? WHERE session = ? AND cost IS NOT NULL", (ratio, sid))
        elif new:
            # Before, nothing had a price: spread the cost over the steps by their tokens.
            total = db.execute("SELECT SUM(turn_tok) FROM calls WHERE session = ?", (sid,)).fetchone()[0] or 0
            if total:
                db.execute("UPDATE calls SET turn_cost = ? * turn_tok / ? WHERE session = ?", (new, total, sid))
                db.execute("UPDATE findings SET cost = ? * tokens / ? WHERE session = ? AND cost IS NOT NULL",
                           (new, total, sid))


def log_files() -> dict[str, tuple[str, int, float]]:
    found = {}
    root = claude_root()
    if root.is_dir():
        for p in root.rglob("*.jsonl"):
            with contextlib.suppress(OSError):
                st = p.stat()
                found[str(p)] = ("claude", st.st_size, st.st_mtime)
    for root in codex_roots():
        if root.is_dir():
            for p in root.rglob("rollout-*.jsonl"):
                with contextlib.suppress(OSError):
                    st = p.stat()
                    found[str(p)] = ("codex", st.st_size, st.st_mtime)
    return found


def project_of(cwd: str) -> str:
    """A short project name from a working directory; worktrees under a
    `.claude/worktrees` or `-lanes` folder count as their repository."""
    path = norm_path(cwd)
    m = re.search(r"/([^/]+)/\.(?:claude|codex)/worktrees/", path)
    if m:
        return m.group(1)
    return path.rsplit("/", 1)[-1] or path or "(unknown)"


def store(db: sqlite3.Connection, path: str, source: str, size: int, mtime: float, result,
          machine: str = "") -> None:
    for (sid,) in db.execute("SELECT id FROM sessions WHERE file=?", (path,)).fetchall():
        for table, col in (("usage", "session"), ("calls", "session"), ("findings", "session"),
                           ("sessions", "id")):
            db.execute(f"DELETE FROM {table} WHERE {col}=?", (sid,))
    error = result if isinstance(result, str) else None
    db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)", (path, source, size, mtime, error))
    if error:
        return
    for s in result:
        if machine:
            s["id"] = f"{machine}:{s['id']}"
            s["parent"] = s["parent"] and f"{machine}:{s['parent']}"
        usage = s["usage"]
        tot = [sum(v[i] for v in usage.values()) for i in range(6)]
        models = Counter()
        for (_, model), v in usage.items():
            models[model] += v[0] + v[1] + v[2] + v[3]
        db.execute("DELETE FROM sessions WHERE id=?", (s["id"],))
        for table in ("usage", "calls", "findings"):
            db.execute(f"DELETE FROM {table} WHERE session=?", (s["id"],))
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            s["id"], path, s["source"], s["parent"], s["cwd"], project_of(s["cwd"]),
            s["title"] or s["first_prompt"], s["client"], s["first_ts"], s["last_ts"], s["user_msgs"],
            s["compactions"], len(s["calls"]), sum(c["err"] for c in s["calls"]), tot[0], tot[1],
            tot[2], tot[3], tot[4], tot[5], s["unpriced"], s["last_context"],
            json.dumps(dict(models.most_common())), machine))
        db.executemany("INSERT INTO usage VALUES (?,?,?,?,?,?,?,?,?)",
                       [(s["id"], h, m, *v) for (h, m), v in usage.items()])
        db.executemany("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (s["id"], c["seq"], c["ts"], c["tool"], c["kind"], c["target"], c["prog"], c["text"],
             c["dur"], c["err"], c["out_tok"], c["turn_cost"], c["turn_tok"], c["script"], c["err_text"])
            for c in s["calls"]])
        db.executemany("INSERT INTO findings VALUES (?,?,?,?,?,?,?)", [
            (s["id"], f["kind"], f["calls"], f["cost"], f["tokens"], f["seconds"], json.dumps(f["detail"]))
            for f in s["findings"]])


def update_index(quiet: bool = False, only: list[str] | None = None, pull: bool | None = False) -> dict:
    """Brings the index up to date with the log files; only new or changed files are read.

    pull: also fetch from the other machines (True), only when the last fetch is older
    than PULL_EVERY (None), or not at all (False)."""
    db = connect()
    files = log_files()
    known = {r["path"]: (r["size"], r["mtime"]) for r in db.execute("SELECT path, size, mtime FROM files")
             if MACHINE_SEP not in r["path"]}
    todo = [(p, src, size, mtime) for p, (src, size, mtime) in files.items()
            if known.get(p) != (size, mtime) and (only is None or p in only)]
    # A deleted log keeps its sessions here: often this index is the only history left.
    gone = [p for p in known if p not in files and known[p][0] != -1]
    for p in gone:
        db.execute("UPDATE files SET size = -1 WHERE path=?", (p,))
    started = time.time()
    total_bytes = sum(t[2] for t in todo) or 1
    done_bytes = 0
    show = not quiet and total_bytes > 500_000_000
    prices = load_prices()
    if show:
        print(f"Kuakgan: reading {len(todo)} log files ({total_bytes / 1e9:.1f} GB) for the first time "
              f"or since they changed; later runs only read what is new.", file=sys.stderr, flush=True)
    jobs = [(p, src, prices) for p, src, _, _ in sorted(todo, key=lambda t: -t[2])]
    meta = {p: (src, size, mtime) for p, src, size, mtime in todo}
    workers = min(os.cpu_count() or 2, 8) if total_bytes > 30_000_000 and len(todo) > 1 else 1
    if workers > 1:
        windowless_workers()
    results = (concurrent.futures.ProcessPoolExecutor(workers).map(parse_file, jobs, chunksize=1)
               if workers > 1 else map(parse_file, jobs))
    last_print = time.time()
    for n, (path, result) in enumerate(results, 1):
        src, size, mtime = meta[path]
        store(db, path, src, size, mtime, result)
        done_bytes += size
        if n % 50 == 0:
            db.commit()
        if show and time.time() - last_print > 5:
            last_print = time.time()
            print(f"  {done_bytes / 1e9:.1f} of {total_bytes / 1e9:.1f} GB read ({n} of {len(todo)} files)",
                  file=sys.stderr, flush=True)
    db.commit()
    if show:
        print(f"  done in {time.time() - started:.0f} s", file=sys.stderr, flush=True)
    answer = {"read": len(todo), "removed": len(gone), "files": len(files)}
    machines = load_machines()
    if machines and pull is not False:
        answer["machines"] = {}
        for m in machines:
            last = db.execute("SELECT value FROM meta WHERE key = ?", (f"pulled:{m['name']}",)).fetchone()
            if pull is None and last and time.time() - float(last[0]) < PULL_EVERY:
                continue
            result = answer["machines"][m["name"]] = pull_machine(db, m, quiet)
            db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"error:{m['name']}", result.get("error", "")))
            db.commit()
    db.close()
    return answer


# ---------- other machines ----------

MACHINE_SEP = "::"  # index paths of other machines' files are NAME::PATH
# Runs Kuakgan's own source on the other machine, sent over stdin, so nothing has to be
# installed there. Written without quotes, which shells pass on differently.
REMOTE_BOOT = ("import sys,json;d=json.loads(sys.stdin.read());ns=dict(__name__=d.get(chr(110)));"
               "exec(d.get(chr(115)),ns);ns.get(d.get(chr(102)))(d)")


def load_machines() -> list[dict]:
    with contextlib.suppress(OSError, ValueError):
        return json.loads(MACHINES_PATH.read_text(encoding="utf-8")).get("machines", [])
    return []


def save_machines(machines: list[dict]) -> None:
    MACHINES_PATH.write_text(json.dumps({"machines": machines}, indent=2) + "\n", encoding="utf-8")


def ssh_command(m: dict, remote: str) -> list[str]:
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new",
           "-o", "ServerAliveInterval=15", "-C"]
    if m.get("key"):
        cmd += ["-i", m["key"]]
    if m.get("port"):
        cmd += ["-p", str(m["port"])]
    host = f"{m['user']}@{m['address']}" if m.get("user") else m["address"]
    return cmd + ["--", host, remote]


def run_remote(m: dict, request: dict, python: str, timeout: float | None):
    """Starts Kuakgan's source on machine m with `request`; returns the process, whose
    stdout gives one JSON value per line."""
    payload = json.dumps({"n": "kuakgan_remote", "s": (HERE / "kuakgan.py").read_text(encoding="utf-8"),
                          "f": "remote", **request})
    # While results stream in, nothing reads stderr, so it must not fill a pipe and stall.
    proc = subprocess.Popen(ssh_command(m, f"{python} -c '{REMOTE_BOOT}'"), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE if request["action"] == "list" else subprocess.DEVNULL,
                            **hidden())
    def send():
        with contextlib.suppress(OSError):
            proc.stdin.write(payload.encode())
            proc.stdin.close()
    threading.Thread(target=send, daemon=True).start()
    return proc


def remote(request: dict) -> None:
    """Runs on the other machine: lists its log files, or reads the ones asked for."""
    out = sys.stdout
    if request["action"] == "list":
        out.write(json.dumps({"os": sys.platform, "files": log_files()}) + "\n")
        return
    prices = {k: tuple(v) for k, v in request["prices"].items()}
    for path, source in request["paths"]:
        _, result = parse_file((path, source, prices))
        if not isinstance(result, str):
            for sess in result:
                sess["usage"] = [[h, mo, *v] for (h, mo), v in sess["usage"].items()]
        out.write(json.dumps({"path": path, "result": result}) + "\n")
        out.flush()


def remote_python(m: dict) -> str | None:
    """The Python command that works on machine m, found once and remembered."""
    if m.get("python"):
        return m["python"]
    for python in ("python3", "python"):
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            done = subprocess.run(ssh_command(m, f"{python} -c 'import sys;print(sys.version_info[:2]>=(3,9))'"),
                                  capture_output=True, text=True, timeout=40, stdin=subprocess.DEVNULL,
                                  **hidden())
            if done.stdout.strip() == "True":
                return python
    return None


def pull_machine(db: sqlite3.Connection, m: dict, quiet: bool = False) -> dict:
    """Brings the index up to date with machine m's logs: lists them over SSH and reads
    only new or changed files there, so only the results travel."""
    name = m["name"]
    python = remote_python(m)
    if not python:
        return {"online": False, "error": "no SSH connection, or no Python 3.9+ there"}
    proc = run_remote(m, {"action": "list"}, python, 60)
    try:
        listing, err = proc.stdout.read(), proc.stderr.read()  # SSH's keep-alive ends a hung read
        proc.stdout.close()
        proc.stderr.close()
        proc.wait(timeout=30)
        listing = json.loads(listing.decode() or "null")
    except (subprocess.TimeoutExpired, ValueError):
        proc.kill()
        return {"online": False, "error": "the machine did not answer in time"}
    if not listing:
        return {"online": False, "error": (err.decode(errors="replace").strip().splitlines() or ["no answer"])[-1]}
    files = {f"{name}{MACHINE_SEP}{p}": tuple(v) for p, v in listing["files"].items()}
    prefix = f"{name}{MACHINE_SEP}"
    known = {r["path"]: (r["size"], r["mtime"]) for r in
             db.execute("SELECT path, size, mtime FROM files WHERE substr(path, 1, ?) = ?", (len(prefix), prefix))}
    todo = {k: v for k, v in files.items() if known.get(k) != (v[1], v[2])}
    for k in [k for k in known if k not in files and known[k][0] != -1]:
        db.execute("UPDATE files SET size = -1 WHERE path=?", (k,))
    total = sum(v[1] for v in todo.values())
    if todo and not quiet and total > 200_000_000:
        print(f"Kuakgan: reading {len(todo)} log files ({total / 1e9:.1f} GB) on {name}; only the results "
              f"come over the network.", file=sys.stderr, flush=True)
    read = 0
    if todo:
        prices = {k: list(v) for k, v in load_prices().items()}
        paths = [[k[len(prefix):], v[0]] for k, v in sorted(todo.items(), key=lambda kv: -kv[1][1])]
        proc = run_remote(m, {"action": "parse", "paths": paths, "prices": prices}, python, None)
        last = time.time()
        for line in proc.stdout:
            with contextlib.suppress(ValueError):
                item = json.loads(line)
                result = item["result"]
                if not isinstance(result, str):
                    for sess in result:
                        sess["usage"] = {(h, mo): v for h, mo, *v in sess["usage"]}
                key = prefix + item["path"]
                src, size, mtime = todo[key]
                store(db, key, src, size, mtime, result, name)
                read += 1
                if read % 50 == 0:
                    db.commit()
                if not quiet and total > 200_000_000 and time.time() - last > 10:
                    last = time.time()
                    print(f"  {name}: {read} of {len(todo)} files", file=sys.stderr, flush=True)
        proc.stdout.close()
        if proc.wait() != 0 and read < len(todo):
            db.commit()
            return {"online": True, "read": read, "files": len(files),
                    "error": f"stopped after {read} of {len(todo)} files; the rest is read next time"}
    db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"pulled:{name}", str(time.time())))
    db.commit()
    return {"online": True, "read": read, "files": len(files)}


def machines_status() -> list[dict]:
    db = connect()
    rows = {r["machine"]: dict(r) for r in db.execute(
        "SELECT machine, COUNT(*) sessions, MAX(last_ts) last_ts FROM sessions GROUP BY machine")}
    pulled = {r["key"][7:]: float(r["value"]) for r in db.execute("SELECT key, value FROM meta WHERE key LIKE 'pulled:%'")}
    errors = {r["key"][6:]: r["value"] for r in db.execute("SELECT key, value FROM meta WHERE key LIKE 'error:%'")}
    db.close()
    out = []
    for m in load_machines():
        r = rows.get(m["name"], {})
        out.append({"name": m["name"], "address": m["address"], "sessions": r.get("sessions", 0),
                    "last_fetched": datetime.fromtimestamp(pulled[m["name"]], timezone.utc).isoformat(timespec="seconds")
                    if m["name"] in pulled else None, **({"error": errors[m["name"]]} if errors.get(m["name"]) else {})})
    return out


# ---------- answers ----------

FIXES = {
    "polling": {
        "title": "Busy polling",
        "short": "Wait for long commands; do not keep checking.",
        "what": "The agent checked on running work again and again (tail, cat, ps, wait) "
                "instead of waiting for it. Every check is a full model turn.",
        "claude": "Start long commands with run_in_background and wait for the notification that "
                  "they finished, instead of looking at their output. A line in CLAUDE.md helps: "
                  "\"While a long command runs in the background, wait for its notification; do not "
                  "check its output in between.\"",
        "codex": "Codex returns from a command after 10 seconds by default and the model then "
                 "starts polling. Let it wait longer: add `background_terminal_max_timeout = 3600000` "
                 "to ~/.codex/config.toml, and in AGENTS.md: \"While a long command runs, wait on it; "
                 "do not poll its output more than once a minute.\"",
    },
    "rereads": {
        "title": "Files read again and again",
        "short": "Read each file once; note key facts in CLAUDE.md.",
        "what": "The same unchanged file was read three or more times in one session. Each read "
                "puts the whole file into the context again.",
        "claude": "Read a file once and work from it. If the agent keeps needing the same facts, "
                  "put the few lines that matter in CLAUDE.md or AGENTS.md instead.",
    },
    "big_outputs": {
        "title": "Very large tool output",
        "short": "Ask for less output: tail, --quiet, line ranges.",
        "what": "Single tool results of more than 10,000 tokens. They stay in the context, and "
                "every later turn pays for them again.",
        "claude": "Ask for less: `| tail -n 50`, `--quiet`, a line range when reading a file, or "
                  "grep for what you need before reading. Test runners often have a flag that only "
                  "prints failures.",
    },
    "failures": {
        "title": "Failed commands",
        "short": "Run `kuakgan check` before each task.",
        "what": "Commands that failed. Many failures repeat across sessions: the same wrong "
                "command, path or tool, fixed again each time.",
        "claude": "Run `kuakgan check` at the start of a task: it lists the commands that failed "
                  "before in this project and what worked instead, so the agent gets it right the "
                  "first time.",
    },
    "scripts": {
        "title": "The same helper script, rewritten",
        "short": "Save recurring scripts as files agents run.",
        "what": "Inline scripts (`python - <<PY`, `node -e`) with the same body written in "
                "several sessions. Each time the model types the whole script again.",
        "claude": "Save scripts that recur as files in the repository (for example under "
                  "scripts/) and mention them in CLAUDE.md or AGENTS.md, so agents run them "
                  "instead of writing them anew.",
    },
}


# Claude Code deletes conversations older than 30 days by default, so no period is longer.
MAX_DAYS = 30


def period(days: float | None) -> float:
    return min(days or MAX_DAYS, MAX_DAYS)


def hidden(**options) -> dict:
    """Options for every helper process Kuakgan starts: on Windows without a window of
    its own, however Kuakgan itself was started (a console, a shortcut, pythonw)."""
    if sys.platform == "win32":
        options["creationflags"] = options.get("creationflags", 0) | 0x08000000  # CREATE_NO_WINDOW
    return options


def windowless_workers() -> None:
    """Reader processes on Windows run under pythonw, which has no console, so none of
    them opens a window even when Kuakgan runs without one."""
    if sys.platform == "win32":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            multiprocessing.set_executable(str(pythonw))


def since_ts(days: float | None) -> float:
    return time.time() - period(days) * 86400


def where(days, project, alias="s") -> tuple[str, list]:
    clauses, params = [f"{alias}.last_ts >= ?"], [since_ts(days)]
    if project:
        clauses.append(f"({alias}.project = ? OR {alias}.cwd = ?)")
        params += [project, project]
    return " AND ".join(clauses), params


def r2(x):
    return None if x is None else round(x, 2)


def summarize_findings(db, days, project) -> list[dict]:
    w, params = where(days, project)
    out = []
    totals = db.execute(f"SELECT COUNT(*) n, COALESCE(SUM(s.cost),0) cost FROM calls c "
                        f"JOIN sessions s ON s.id = c.session WHERE {w}", params).fetchone()
    all_calls = totals["n"] or 1
    rows = db.execute(f"SELECT f.*, s.source, s.project, s.title FROM findings f JOIN sessions s "
                      f"ON s.id = f.session WHERE {w}", params).fetchall()
    by_kind = defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(r)
    for kind, rs in by_kind.items():
        fx = FIXES[kind]
        sources = Counter(r["source"] for r in rs)
        worst = sorted(rs, key=lambda r: -(r["cost"] or 0) - r["calls"] * 1e-6)[:5]
        item = {"kind": kind, "title": fx["title"], "short": fx["short"], "what": fx["what"],
                "sessions": len(rs), "calls": sum(r["calls"] for r in rs),
                "share_of_calls": round(sum(r["calls"] for r in rs) / all_calls, 3),
                "cost": None if kind == "big_outputs" else r2(sum(r["cost"] or 0 for r in rs)),
                "tokens": sum(r["tokens"] or 0 for r in rs),
                "fix": {src: fx.get(src, fx["claude"]) for src in sources},
                "worst": [{"session": r["session"], "project": r["project"], "title": r["title"],
                           "calls": r["calls"], "cost": r2(r["cost"]),
                           "detail": json.loads(r["detail"] or "{}")} for r in worst]}
        if kind == "failures":
            item["what"] = fx["what"]
            item["repeated"] = repeated_failures(db, days, project)[:10]
        out.append(item)
    scripts = repeated_scripts(db, days, project)
    if scripts:
        n = sum(g["times"] - 1 for g in scripts)
        out.append({"kind": "scripts", "title": FIXES["scripts"]["title"], "short": FIXES["scripts"]["short"],
                    "what": FIXES["scripts"]["what"],
                    "sessions": len({s for g in scripts for s in g["session_ids"]}), "calls": n,
                    "share_of_calls": round(n / all_calls, 3),
                    "cost": r2(sum(g["cost"] for g in scripts)), "tokens": sum(g["tokens"] for g in scripts),
                    "fix": {"claude": FIXES["scripts"]["claude"]},
                    "worst": [{k: v for k, v in g.items() if k != "session_ids"} for g in scripts[:5]]})
    order = {"polling": 0, "failures": 1, "scripts": 2, "rereads": 3, "big_outputs": 4}
    out.sort(key=lambda f: (-(f["cost"] or 0), order.get(f["kind"], 9)))
    return out


def repeated_scripts(db, days, project) -> list[dict]:
    w, params = where(days, project)
    rows = db.execute(f"SELECT c.script, c.prog, c.target, c.turn_cost, c.turn_tok, c.session, s.project "
                      f"FROM calls c JOIN sessions s ON s.id = c.session WHERE c.script IS NOT NULL "
                      f"AND {w}", params).fetchall()
    groups = defaultdict(list)
    for r in rows:
        groups[r["script"]].append(r)
    out = []
    for h, rs in groups.items():
        if len(rs) >= 3:
            extra = rs[1:]
            out.append({"times": len(rs), "language": rs[0]["prog"], "start": rs[0]["target"][:160],
                        "projects": sorted({r["project"] for r in rs})[:3],
                        "cost": sum(r["turn_cost"] or 0 for r in extra),
                        "tokens": sum(r["turn_tok"] or 0 for r in extra),
                        "session_ids": sorted({r["session"] for r in rs})})
    return sorted(out, key=lambda g: -g["times"])


def failure_key(prog: str, error: str) -> str:
    """Two failures count as the same mistake when the same program fails with the
    same error message, apart from paths, numbers and ids in it."""
    e = re.sub(r"\\u[0-9a-fA-F]{4}|'.'", "C", error or "")
    e = re.sub(r"(?:[A-Za-z]:)?[\\/][^\s'\"`:,)]+", "P", e)
    e = re.sub(r"\b[0-9a-f-]{7,}\b|\d+", "N", e)
    e = re.sub(r"\s+", " ", e).strip()[:120]
    return f"{prog}|{e}"


def repeated_failures(db, days, project) -> list[dict]:
    """Mistakes made in more than one session: the same program failing with the
    same error, and the command that worked right after, where one did."""
    w, params = where(days, project)
    worked_after = {}
    for r in db.execute(f"SELECT f.session, f.detail FROM findings f JOIN sessions s ON s.id = f.session "
                        f"WHERE f.kind = 'failures' AND {w}", params):
        for fx in json.loads(r["detail"] or "{}").get("fixes", []):
            worked_after[(r["session"], fx["failed"])] = fx["worked"]
    groups = defaultdict(list)
    for r in db.execute(f"SELECT c.prog, c.tool, c.text, c.err_text, c.session, c.turn_cost FROM calls c "
                        f"JOIN sessions s ON s.id = c.session WHERE c.err = 1 AND c.text != '' "
                        f"AND c.err_text != '' AND {w}", params):
        groups[failure_key(r["prog"] or r["tool"], r["err_text"])].append(r)
    out = []
    for key, rs in groups.items():
        sessions = {r["session"] for r in rs}
        if len(sessions) < 2:
            continue
        worked = Counter(worked_after[(r["session"], r["text"][:200])] for r in rs
                         if (r["session"], r["text"][:200]) in worked_after)
        example = next((r for r in rs if (r["session"], r["text"][:200]) in worked_after), rs[0])
        out.append({"program": rs[0]["prog"] or rs[0]["tool"], "error": rs[0]["err_text"][:200],
                    "failed": example["text"][:200], "times": len(rs), "sessions": len(sessions),
                    "worked_instead": worked_after.get((example["session"], example["text"][:200])),
                    "cost": r2(sum(r["turn_cost"] or 0 for r in rs[1:]))})
    out.sort(key=lambda g: (-g["sessions"], -g["times"]))
    return out


def model_family(model: str) -> str:
    return re.sub(r"-\d{8}$", "", model or "unknown")


def summary(days=30, project=None, sessions_limit=10) -> dict:
    days = period(days)
    db = connect()
    w, params = where(days, project)
    t = db.execute(f"SELECT COUNT(*) sessions, SUM(input) input, SUM(cache_read) cache_read, "
                   f"SUM(cache_write) cache_write, SUM(output) output, SUM(cost) cost, SUM(calls) calls, "
                   f"SUM(errors) errors, SUM(unpriced) unpriced, SUM(requests) requests, "
                   f"SUM(user_msgs) user_msgs, MIN(first_ts) first_ts FROM sessions s WHERE {w}",
                   params).fetchone()
    hour_from = hour_of(since_ts(days))
    uw = f"u.hour >= ? AND s.id = u.session" + (" AND (s.project = ? OR s.cwd = ?)" if project else "")
    up = [hour_from] + ([project, project] if project else [])
    models = [dict(r) for r in db.execute(
        f"SELECT u.model, s.source, SUM(u.input) input, SUM(u.cache_read) cache_read, "
        f"SUM(u.cache_write) cache_write, SUM(u.output) output, SUM(u.requests) requests, SUM(u.cost) cost "
        f"FROM usage u, sessions s WHERE {uw} GROUP BY u.model, s.source ORDER BY cost DESC, output DESC", up)]
    for m in models:
        m["tokens"] = m["input"] + m["cache_read"] + m["cache_write"] + m["output"]
        m["priced"] = price_for(m["model"], load_prices()) is not None
        m["cost"] = r2(m["cost"]) if m["priced"] else None
    days_rows = db.execute(
        f"SELECT substr(u.hour, 1, {13 if days <= 1 else 10}) day, u.model, SUM(u.input + u.cache_read + u.cache_write + u.output) "
        f"tokens, SUM(u.output) output, SUM(u.cost) cost FROM usage u, sessions s WHERE {uw} "
        f"GROUP BY day, u.model ORDER BY day", up).fetchall()
    projects = [dict(r) for r in db.execute(
        f"SELECT s.project, COUNT(DISTINCT s.id) sessions, "
        f"SUM(u.input + u.cache_read + u.cache_write + u.output) tokens, SUM(u.cost) cost, "
        f"MAX(s.last_ts) last_ts, GROUP_CONCAT(DISTINCT s.source) sources, "
        f"GROUP_CONCAT(DISTINCT s.machine) machines FROM usage u, sessions s WHERE {uw} "
        f"GROUP BY s.project ORDER BY cost DESC, tokens DESC LIMIT 25", up)]
    project_count = db.execute(f"SELECT COUNT(DISTINCT s.project) FROM usage u, sessions s WHERE {uw}", up).fetchone()[0]
    for p in projects:
        p["cost"] = r2(p["cost"])
    top = [session_row(r) for r in db.execute(
        f"SELECT * FROM sessions s WHERE {w} ORDER BY cost DESC, (input + cache_read + output) DESC "
        f"LIMIT ?", params + [sessions_limit])]
    machines = [dict(r) for r in db.execute(
        f"SELECT s.machine, COUNT(DISTINCT s.id) sessions, SUM(u.input + u.cache_read + u.cache_write + u.output) "
        f"tokens, SUM(u.cost) cost FROM usage u, sessions s WHERE {uw} GROUP BY s.machine "
        f"ORDER BY tokens DESC", up)]
    for m in machines:
        m["machine"] = m["machine"] or "this computer"
        m["cost"] = r2(m["cost"])
    findings = summarize_findings(db, days, project)
    db.close()
    sums = {k: sum(m[k] or 0 for m in models) for k in ("input", "cache_read", "cache_write", "output",
                                                         "requests", "tokens")}
    cost = sum(m["cost"] or 0 for m in models if m["priced"])
    unpriced = sum(m["tokens"] for m in models if not m["priced"])
    tokens = sums["tokens"]
    wasted_calls = sum(f["calls"] for f in findings if f["kind"] != "big_outputs")
    return {
        "period_days": days, "project": project,
        "totals": {"sessions": t["sessions"],
                   "projects": project_count, "tokens": tokens, "input": sums["input"],
                   "cache_read": sums["cache_read"], "cache_write": sums["cache_write"],
                   "output": sums["output"], "model_calls": sums["requests"],
                   "tool_calls": t["calls"] or 0, "failed_tool_calls": t["errors"] or 0,
                   "prompts": t["user_msgs"] or 0, "cost": r2(cost),
                   "tokens_without_price": unpriced,
                   "wasted_tool_calls": wasted_calls,
                   "waste_share": round(wasted_calls / (t["calls"] or 1), 3),
                   "waste_cost": r2(sum(f["cost"] or 0 for f in findings if f["kind"] != "big_outputs"))},
        "cost_note": "Estimated at API list prices; on a subscription you pay a flat fee instead."
                     + (f" Leaves out {unpriced:,} tokens from models without a known price "
                        f"(add them in prices.json)." if unpriced else "")
                     + " Tokens and cost count the model calls made in the period; sessions, tool "
                       "calls and waste count the sessions active in it.",
        "models": models,
        "machines": machines,
        "days": [dict(r) for r in days_rows],  # per hour (YYYY-MM-DDTHH, UTC) when the period is a day
        "step": "hour" if days <= 1 else "day",
        "projects": projects,
        "top_sessions": top,
        "findings": findings,
    }


def brief(answer: dict) -> dict:
    """The main points of a summary, small enough for an agent to read whole."""
    keep = lambda d, keys: {k: d[k] for k in keys if k in d}
    return {
        **keep(answer, ("period_days", "project", "totals", "cost_note")),
        "models": [keep(m, ("model", "source", "tokens", "output", "requests", "cost")) for m in answer["models"][:8]],
        "projects": [keep(p, ("project", "sessions", "tokens", "cost")) for p in answer["projects"][:8]],
        "machines": answer["machines"],
        "top_sessions": [keep(s, ("id", "project", "title", "source", "calls", "tokens", "cost", "last_ts"))
                         for s in answer["top_sessions"][:5]],
        "findings": [{**keep(f, ("kind", "title", "short", "sessions", "calls", "share_of_calls", "cost", "tokens", "fix")),
                      **({"repeated": f["repeated"][:3]} if f.get("repeated") else {})}
                     for f in answer["findings"]],
        "more": "Run `summary --full` for every day, all projects and where each finding happened.",
    }


def session_row(r) -> dict:
    d = dict(r)
    d["tokens"] = d["input"] + d["cache_read"] + d["cache_write"] + d["output"]
    d["models"] = json.loads(d["models"] or "{}")
    d["cost"] = r2(d["cost"])
    for k in ("first_ts", "last_ts"):
        d[k] = datetime.fromtimestamp(d[k], timezone.utc).isoformat(timespec="seconds") if d[k] else None
    for k in ("file", "input", "cache_read", "cache_write", "output", "unpriced"):
        d.pop(k, None)
    return d


def find_project_sessions(db, directory: str) -> tuple[str, list]:
    cwd = norm_path(os.path.abspath(directory))
    rows = db.execute("SELECT id, cwd, project FROM sessions").fetchall()
    exact = [r for r in rows if norm_path(r["cwd"]).lower() == cwd.lower()]
    if not exact:
        exact = [r for r in rows if cwd.lower().startswith(norm_path(r["cwd"]).lower() + "/")
                 or norm_path(r["cwd"]).lower().startswith(cwd.lower() + "/")]
    project = exact[0]["project"] if exact else project_of(cwd)
    return project, [r["id"] for r in rows if r["project"] == project]


def check(directory: str = ".") -> dict:
    """What an agent working in `directory` should know: earlier mistakes in this
    project and how the current session is doing. Kept short; it goes into context."""
    started = time.time()
    db = connect()
    # Index only this project's newest files on the hot path, so the answer is quick.
    db.close()
    update_index(quiet=True)
    db = connect()
    project, ids = find_project_sessions(db, directory)
    tips = []
    w = f"s.project = ?"
    rf = repeated_failures(db, 90, project)
    pitfalls = [{"program": f["program"], "error": f["error"][:160], "failed": f["failed"][:160],
                 "worked": f["worked_instead"][:160] if f["worked_instead"] else None,
                 "times": f["times"], "sessions": f["sessions"]} for f in rf[:8]]
    rereads = Counter()
    for r in db.execute(f"SELECT f.detail FROM findings f JOIN sessions s ON s.id = f.session "
                        f"WHERE f.kind = 'rereads' AND {w} AND s.last_ts >= ?", (project, since_ts(30))):
        for f in json.loads(r["detail"]).get("files", []):
            rereads[f["path"]] += f["reads"]
    big = Counter()
    for r in db.execute(f"SELECT c.prog, c.text, c.out_tok FROM calls c JOIN sessions s ON s.id = c.session "
                        f"WHERE {w} AND c.out_tok >= ? AND c.text != '' AND s.last_ts >= ?",
                        (project, BIG_OUTPUT, since_ts(30))):
        big[re.sub(r"\s+", " ", r["text"])[:80]] += 1
    scripts = [g for g in repeated_scripts(db, 60, project)][:3]
    # The session in this project written to most recently is taken to be the caller's.
    current = db.execute("SELECT * FROM sessions WHERE project = ? AND parent IS NULL "
                         "ORDER BY last_ts DESC LIMIT 1", (project,)).fetchone()
    now = None
    if current and current["last_ts"] and time.time() - current["last_ts"] < 1800:
        f = {r["kind"]: dict(r) for r in db.execute("SELECT * FROM findings WHERE session = ?", (current["id"],))}
        tail = db.execute("SELECT kind, target, tool FROM calls WHERE session = ? ORDER BY seq DESC LIMIT 6",
                          (current["id"],)).fetchall()
        polling_now = len(tail) >= 4 and all(r["kind"] in ("observe", "poll") for r in tail[:4])
        now = {"session": current["id"], "title": current["title"], "model_calls": current["requests"],
               "tool_calls": current["calls"], "cost": r2(current["cost"]),
               "context_tokens": current["last_context"], "compactions": current["compactions"],
               "polling_calls": (f.get("polling") or {}).get("calls", 0),
               "reread_calls": (f.get("rereads") or {}).get("calls", 0),
               "polling_now": polling_now}
        if polling_now:
            tips.append("Your last few steps only checked on running work. Wait for it instead: one "
                        "`sleep 60` then a single check, or a background run with a notification.")
        if now["reread_calls"] >= 3:
            tips.append("You have read the same unchanged files several times this session; work from "
                        "what you already read.")
        if (current["last_context"] or 0) > 400_000:
            tips.append("This session's context is very large; consider finishing the current step and "
                        "handing over to a fresh session with a short summary.")
    for p in pitfalls[:4]:
        tips.append(pitfall_tip(p))
    if scripts:
        tips.append(f"An inline {scripts[0]['language']} script starting `{scripts[0]['start'][:60]}` "
                    f"has been written {scripts[0]['times']} times here; save it as a file and reuse it.")
    db.close()
    return {"project": project, "sessions_seen": len(ids), "tips": tips,
            "pitfalls": pitfalls,
            "often_reread": [{"path": p, "reads": n} for p, n in rereads.most_common(5)],
            "big_outputs": [{"command": c, "times": n} for c, n in big.most_common(3)],
            "repeated_scripts": [{"language": g["language"], "times": g["times"], "start": g["start"][:100]}
                                 for g in scripts],
            "this_session": now, "took_seconds": round(time.time() - started, 1)}


def pitfall_tip(p: dict) -> str:
    one_line = lambda t, n: re.sub(r"\s+", " ", t)[:n]
    tip = (f"`{p['program']}` failed {p['times']} times in {p['sessions']} earlier sessions here with "
           f"\"{one_line(p['error'], 120)}\" (for example `{one_line(p['failed'], 90)}`)")
    return tip + (f"; this worked: `{one_line(p['worked'], 90)}`." if p["worked"] else ".")


def check_text(answer: dict) -> str:
    """The short plain-text form, for a SessionStart hook."""
    lines = [f"Kuakgan, project {answer['project']}:"]
    lines += [f"- {t}" for t in answer["tips"]] or ["- No earlier mistakes recorded here."]
    for p in answer["pitfalls"][4:6]:
        lines.append("- " + pitfall_tip(p))
    if answer["often_reread"]:
        lines.append("- Files earlier sessions re-read often: " +
                     ", ".join(Path(f["path"]).name for f in answer["often_reread"][:3]))
    return "\n".join(lines)


def list_sessions(days=7, project=None, limit=30) -> list[dict]:
    db = connect()
    w, params = where(days, project)
    rows = [session_row(r) for r in db.execute(
        f"SELECT * FROM sessions s WHERE {w} ORDER BY last_ts DESC LIMIT ?", params + [limit])]
    db.close()
    return rows


INSTRUCTIONS = """## Kuakgan

Kuakgan reads this computer's Claude Code and Codex logs. At the start of each
task, and before waiting on a long-running command, run:

    {cmd} check

It lists commands that failed before in this project and what worked instead,
files earlier sessions kept re-reading, and how this session is doing. Follow
its tips; mention one to the user only if it changes what you do."""


# ---------- dashboard ----------

def app_browser() -> str | None:
    candidates = {
        "win32": [os.path.expandvars(p) for p in (
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe")],
        "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                   "/Applications/Chromium.app/Contents/MacOS/Chromium"],
    }.get(sys.platform)
    if candidates is None:
        candidates = [shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser",
                                                "microsoft-edge")]
    return next((p for p in candidates if p and os.path.exists(p)), None)


def has_screen() -> bool:
    return sys.platform in ("win32", "darwin") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_window(url: str) -> None:
    if not has_screen():
        print(f"No screen here to open a window on; open {url} in a browser.", flush=True)
        return
    browser = app_browser()
    if browser:
        with contextlib.suppress(OSError):
            # On Linux the window class ties the window to kuakgan.desktop and its icon.
            extra = ["--class=Kuakgan"] if sys.platform.startswith("linux") else []
            subprocess.Popen([browser, f"--app={url}", "--window-size=1440,790", *extra],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def screenshot(url: str, width: int, height: int) -> bytes | None:
    """A sharp PNG of the dashboard at the window's size, drawn by Edge or Chrome
    without a window. A throwaway profile keeps it apart from the open window."""
    import tempfile
    browser = app_browser()
    if not browser:
        return None
    folder = tempfile.mkdtemp(prefix="kuakgan-shot-")
    out = os.path.join(folder, "shot.png")
    try:
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--user-data-dir={os.path.join(folder, 'profile')}",
                        f"--window-size={width},{height}", "--force-device-scale-factor=2",
                        "--virtual-time-budget=8000", f"--screenshot={out}", url],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, **hidden())
        return Path(out).read_bytes() if os.path.exists(out) else None
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def dashboard_answers(url: str) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return b"<title>Kuakgan</title>" in response.read(3000)
    except OSError:
        return False


def serve_dashboard(port: int, open_browser: bool) -> None:
    url = f"http://127.0.0.1:{port}/"
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    cache, lock, one_shot = {}, threading.Lock(), threading.Lock()

    def data(days, project) -> bytes:
        with lock:
            hit = cache.get((days, project))
        if hit is None:
            hit = json.dumps(summary(days, project, 25)).encode()
            with lock:
                cache[(days, project)] = hit
        return hit

    def keep_fresh():
        """Reads new logs every minute and prepares the usual views, so that switching
        between them answers at once."""
        while True:
            with contextlib.suppress(Exception):
                done = update_index(quiet=True, pull=None)
                if done["read"] or done["removed"] or any(m.get("read") for m in done.get("machines", {}).values()):
                    with lock:
                        cache.clear()
                for days in (30, 7, 1):
                    data(days, None)
            time.sleep(60)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Host") not in allowed_hosts:  # stops DNS rebinding
                self.send_error(403)
                return
            parsed = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            if parsed.path == "/data.json":
                try:
                    days = period(float(q.get("days") or 0))
                except ValueError:
                    self.send_error(400)
                    return
                body, kind = data(days, q.get("project") or None), "application/json"
            elif parsed.path == "/screenshot.png":
                # The custom header makes a browser refuse the request from any other
                # site, so only the dashboard itself can start a screenshot.
                if self.headers.get("X-Kuakgan") != "shot":
                    self.send_error(403)
                    return
                try:
                    width, height = (min(4000, max(300, int(q.get(k) or d))) for k, d in (("w", 1200), ("h", 630)))
                except ValueError:
                    self.send_error(400)
                    return
                view = {k: q[k] for k in ("days", "project", "private", "metric", "theme") if q.get(k)}
                with one_shot:
                    body = screenshot(url + "?" + urlencode({**view, "shot": "1"}), width, height)
                if body is None:
                    self.send_error(501, "Screenshots need Edge or Chrome")
                    return
                kind = "image/png"
            elif parsed.path == "/logo.svg":
                body, kind = (HERE / "logo.svg").read_bytes(), "image/svg+xml"
            elif parsed.path in ("/", "/index.html"):
                body, kind = (HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            # Only `kuakgan dashboard --stop` sends this header; a web page cannot without
            # the browser asking first, so other pages cannot stop the dashboard.
            if (self.headers.get("Host") not in allowed_hosts or urlparse(self.path).path != "/stop"
                    or self.headers.get("X-Kuakgan") != "stop"):
                self.send_error(403)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Kuakgan dashboard stopped")
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        allow_reuse_address = sys.platform != "win32"

    try:
        server = Server(("127.0.0.1", port), Handler)
    except OSError as error:
        if dashboard_answers(url):
            print(f"Kuakgan dashboard is already running on {url}")
            if open_browser:
                open_window(url)
            return
        sys.exit(f"Cannot start the dashboard on port {port}: {error}. Try another port with --port.")
    update_index(pull=None)
    threading.Thread(target=keep_fresh, daemon=True).start()
    print(f"Kuakgan dashboard on {url} (Ctrl+C to stop)", flush=True)
    if open_browser:
        open_window(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def start_in_background(port: int, open_browser: bool) -> int:
    """Starts the dashboard detached from this shell, so it outlives the agent's
    command (a plain `cmd &` is often killed with the shell that started it)."""
    url = f"http://127.0.0.1:{port}/"
    if dashboard_answers(url):
        print(f"Kuakgan dashboard is already running on {url}")
        if open_browser:
            open_window(url)
        return 0
    update_index(pull=None)  # the first read can take minutes; do it here, where progress shows
    cmd = [sys.executable, str(HERE / "kuakgan.py"), "dashboard", "--port", str(port), "--no-browser"]
    # A hidden console of its own (CREATE_NO_WINDOW), not none (DETACHED_PROCESS): the
    # processes it starts, such as readers and ssh, inherit it instead of each opening a window.
    options = (hidden(creationflags=0x00000200)  # CREATE_NEW_PROCESS_GROUP
               if sys.platform == "win32" else {"start_new_session": True})
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **options)
    for _ in range(120):
        if dashboard_answers(url):
            print(f"Kuakgan dashboard on {url}; stop it with `kuakgan dashboard --stop"
                  + (f" --port {port}" if port != 8766 else "") + "`.")
            if open_browser:
                open_window(url)
            return 0
        time.sleep(0.5)
    print(f"The dashboard did not answer on {url}; run `kuakgan dashboard` to see why.")
    return 1


# ---------- command line ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kuakgan", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def scope(p, days):
        p.add_argument("--days", type=float, default=days, help=f"look back this many days (default {days}, at most {MAX_DAYS})")
        p.add_argument("--project", help="only this project (its folder name or full path)")

    p = sub.add_parser("summary", help="tokens, cost, models, projects and waste, as JSON")
    scope(p, 30)
    p.add_argument("--full", action="store_true", help="everything the dashboard shows, not just the main points")
    scope(sub.add_parser("findings", help="waste patterns with fixes, as JSON"), 30)
    p = sub.add_parser("sessions", help="recent sessions, as JSON")
    scope(p, 7)
    p.add_argument("--limit", type=int, default=30)
    p = sub.add_parser("check", help="for agents: earlier mistakes here and how this session is doing")
    p.add_argument("--dir", default=".", help="the project folder (default: the current folder)")
    p.add_argument("--hook", action="store_true", help="short plain text, for a SessionStart hook")
    sub.add_parser("instructions", help="the block to add to CLAUDE.md or AGENTS.md")
    p = sub.add_parser("machines", help="other computers whose logs to include (over SSH)")
    msub = p.add_subparsers(dest="action")
    q = msub.add_parser("add", help="add a machine and read its logs")
    q.add_argument("name")
    q.add_argument("address", help="host name or IP address, or a Host alias from ~/.ssh/config")
    q.add_argument("--user")
    q.add_argument("--key", help="path to the SSH private key")
    q.add_argument("--port", type=int)
    q = msub.add_parser("remove", help="remove a machine and its sessions from the index")
    q.add_argument("name")
    p = sub.add_parser("index", help="read new and changed logs now")
    p.add_argument("--rebuild", action="store_true", help="forget the index and read everything again")
    p = sub.add_parser("dashboard", help="open the dashboard in the browser")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--stop", action="store_true", help="stop a running dashboard")
    p.add_argument("--background", action="store_true",
                   help="start it as a process of its own and return once it answers")

    args = parser.parse_args(argv)
    with contextlib.suppress(AttributeError, ValueError):  # consoles that cannot show every character
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out = lambda obj: print(json.dumps(obj, indent=2))

    if args.command in ("summary", "findings", "sessions"):
        update_index(pull=None)
        days = period(args.days)
        if args.command == "summary":
            answer = summary(days, args.project)
            out({**(answer if args.full else brief(answer)), "about": ABOUT})
        elif args.command == "findings":
            db = connect()
            out({"findings": summarize_findings(db, days, args.project), "about": ABOUT})
            db.close()
        else:
            out(list_sessions(days, args.project, args.limit))
    elif args.command == "check":
        answer = check(args.dir)
        if args.hook:
            print(check_text(answer))
        else:
            out(answer)
    elif args.command == "instructions":
        quote = lambda p: f'"{p}"' if " " in p else p
        cmd = f"{quote(Path(sys.executable).as_posix())} {quote((HERE / 'kuakgan.py').as_posix())}"
        print(INSTRUCTIONS.format(cmd=cmd))
    elif args.command == "index":
        if args.rebuild and DB_PATH.exists():
            for suffix in ("", "-wal", "-shm"):
                with contextlib.suppress(OSError):
                    Path(str(DB_PATH) + suffix).unlink()
        out(update_index(pull=True))
    elif args.command == "machines":
        machines = load_machines()
        if args.action == "add":
            if MACHINE_SEP in args.name or not args.name.strip():
                sys.exit("Give the machine a short name without '::'.")
            key = Path(args.key).expanduser().resolve().as_posix() if args.key else None
            m = {k: v for k, v in {"name": args.name, "address": args.address, "user": args.user,
                                   "key": key, "port": args.port}.items() if v is not None}
            m["python"] = remote_python(m)
            if not m["python"]:
                out({"added": None, "error": f"Cannot reach {args.address} over SSH without a password, or "
                     f"it has no Python 3.9+. Check with: ssh {args.address} python3 --version"})
                return 1
            save_machines([x for x in machines if x["name"] != args.name] + [m])
            db = connect()
            result = pull_machine(db, m)
            db.close()
            out({"added": args.name, **result})
        elif args.action == "remove":
            save_machines([x for x in machines if x["name"] != args.name])
            db = connect()
            prefix = f"{args.name}{MACHINE_SEP}"
            for (path,) in db.execute("SELECT path FROM files WHERE substr(path, 1, ?) = ?",
                                      (len(prefix), prefix)).fetchall():
                store(db, path, "", 0, 0, [], args.name)
                db.execute("DELETE FROM files WHERE path=?", (path,))
            db.execute("DELETE FROM meta WHERE key = ?", (f"pulled:{args.name}",))
            db.commit()
            db.close()
            out({"removed": args.name if any(x["name"] == args.name for x in machines) else None})
        else:
            out(machines_status())
    elif args.command == "dashboard":
        if args.stop:
            import urllib.request
            try:
                request = urllib.request.Request(f"http://127.0.0.1:{args.port}/stop", data=b"",
                                                 headers={"X-Kuakgan": "stop"})
                with urllib.request.urlopen(request, timeout=5):
                    print("Kuakgan dashboard stopped.")
            except OSError:
                print(f"No Kuakgan dashboard is running on port {args.port}.")
        elif args.background:
            return start_in_background(args.port, not args.no_browser)
        else:
            serve_dashboard(args.port, not args.no_browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
