# <img src="logo.svg" width="40" align="top" alt=""> Kuakgan

Kuakgan shows where your Claude Code and Codex tokens go, and what your agents
waste. It reads the session logs both tools already keep on your computer, and
shows tokens and estimated cost over time per model, project and session. It
also finds the patterns that burn tokens without getting work done: polling a
log again and again, reading the same file over and over, rewriting the same
helper script, and repeating a command that failed last week too. Each one
comes with a concrete fix.

It also helps the agents themselves. `kuakgan check` tells an agent, at the
start of a task, which commands failed in this project before and what worked
instead, so it gets them right the first time.

![The Kuakgan dashboard](docs/dashboard.png)

**To get started, give the link to this page to Claude or Codex and ask it to
set up Kuakgan for you.** There is nothing to configure: no account, no API
key, no server. Everything stays on your computer.

[![Tests](https://github.com/Kullevi/kuakgan/actions/workflows/test.yml/badge.svg)](https://github.com/Kullevi/kuakgan/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-0f9d6b.svg)](LICENSE)

What Kuakgan sets out to do is in [docs/GOALS.md](docs/GOALS.md), and the
choices behind it in [docs/DESIGN.md](docs/DESIGN.md). Built by the team behind
[Kullevi](https://kullevi.com), which builds AI that runs your company for you.

## Why we built it

At [Kullevi](https://kullevi.com) we build our product with many Claude Code
and Codex sessions working in parallel, and we wanted to know where the tokens
actually went. So we measured our own logs. In our Codex build lanes, almost
one step in five was the agent checking a log file again while a long command
ran, often a hundred times in a row, because Codex hands control back to the
model every ten seconds by default. Another fifth were inline Python scripts,
many of them the same script written again in another session. In our Claude
Code sessions, the same mistakes came back week after week: a shell quoting
error, a Windows encoding error, a `git` option in the wrong place. Each one
cost a model turn, and each was fixed again from scratch.

None of this shows up in a usage bar. Kuakgan is the tool we built to see it,
taken out of our own setup and made general.

## What it shows

| | |
|---|---|
| **Usage** | Tokens per day by model, cost at API list prices, cache use, and which projects and sessions used the most. |
| **Waste** | Busy polling, files read again without having changed, the same inline script written in several sessions, commands that failed (and those that failed in several sessions), and very large tool outputs. Each with how much it cost and how to fix it. |
| **For agents** | `kuakgan check`: earlier mistakes in this project and what worked instead, files earlier sessions kept re-reading, and how the current session is doing. |
| **To share** | A card with your numbers, without project or session names, to download or copy. |

Costs are estimates at API list prices. If you use a subscription you pay a flat
fee instead; the estimate shows what the same work would cost through the API.
Models without a known price (such as most OpenAI models) count tokens only.

## About Kullevi, the team behind Kuakgan

Kuakgan comes out of our daily work at [Kullevi](https://kullevi.com), where
we build AI that runs companies. If you build with AI, you have watched it
happen: you describe what you want, and working software appears. We are
convinced that companies are next. You would decide what a company should
achieve and where its limits are, and it would sell, deliver, invoice, keep its
books and look after its customers on its own. That is the world we are
building for. **[Join the waitlist at kullevi.com](https://kullevi.com).**

---

## Setup (for the agent doing it)

You are setting this up for a user who may have little experience with
terminals. Do the steps yourself; ask the user only where a step says so.

### 1. Python and the files

Kuakgan needs **Python 3.9 or newer** and nothing else. Try `python3 --version`,
then `python --version` and `py -3 --version`. On Windows, `python3` and
sometimes `python` are a Microsoft Store placeholder that prints a message
instead of a version; use whichever command prints a real version.

- Missing on Windows: tell the user this installs Python and accepts the
  winget/Microsoft terms, and with their OK run
  `winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements`.
  Python is often only found after the terminal or app is restarted.
- Missing on macOS: `xcode-select --install` opens a window where the user
  clicks **Install**.

Clone Kuakgan into the user's home folder:

```
git clone https://github.com/Kullevi/kuakgan.git ~/kuakgan
```

(On Windows, give the full path instead of `~/kuakgan`, such as
`C:/Users/NAME/kuakgan`.) Below, `kuakgan` stands for
`<python> <home>/kuakgan/kuakgan.py` with the Python command that worked.

### 2. Read the logs once

Run `kuakgan index`. The first run reads all of the user's Claude Code and
Codex logs; with a few gigabytes of logs it takes a minute or two and prints
its progress. Later runs read only what is new, in a second or two. Then run
`kuakgan summary` and tell the user, in a few plain sentences, what it shows:
tokens and estimated cost in the last 30 days, the main models, and the biggest
waste with its fix.

Kuakgan finds the logs in `~/.claude/projects` and `~/.codex/sessions` (or
under `CLAUDE_CONFIG_DIR` and `CODEX_HOME` when those are set). If either
folder is missing, that tool has simply not been used on this computer.

### 3. Show the user (Agent)

Start the dashboard **in the background**, since it keeps running until
stopped:

- Bash or zsh: `nohup <python> <path>/kuakgan.py dashboard > /dev/null 2>&1 &`
- PowerShell: `Start-Process <python> -ArgumentList '<path>/kuakgan.py','dashboard' -WindowStyle Hidden`

It opens `http://127.0.0.1:8766/` in a window of its own (with Edge or Chrome
installed; otherwise in a normal browser tab). Tell the user that:

- the period buttons and the project menu change what is shown;
- "Hide names" blurs project and session names, for screenshots;
- the card at the bottom shows only totals, and can be downloaded or copied to
  post;
- they can ask you to "open the Kuakgan dashboard" again later.

To stop it, end the process that listens on port 8766.

### 4. Let the agents use it (Agent, after asking the user)

This is what makes Kuakgan useful every day rather than once. Ask the user
before doing it, since it changes how every future session behaves. Then add
the block that `kuakgan instructions` prints (it already contains the exact
command for this computer) to the user's own instructions, so it applies to
every project: `~/.claude/CLAUDE.md` for Claude Code and/or `~/.codex/AGENTS.md`
for Codex (only for the tools they use).

For Claude Code you can also, with the user's OK, have the tips arrive by
themselves at the start of every session: add a `SessionStart` hook to
`~/.claude/settings.json` (merge it with what is there; do not replace the
file):

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "<python> <path>/kuakgan.py check --hook" } ] }
    ]
  }
}
```

Use the same full Python command and path as above. The hook prints a few
short lines, which Claude Code adds to the session's context.

### 5. Act on what it found (Agent, with the user)

Go through the biggest findings from `kuakgan findings` with the user and offer
to apply the fixes that are theirs to make, one at a time, for example:

- **Busy polling in Codex:** add `background_terminal_max_timeout = 3600000` to
  `~/.codex/config.toml`, so Codex waits for long commands instead of handing
  back to the model every ten seconds.
- **Repeated scripts:** save the script that recurs as a file in the project
  and mention it in the project's `CLAUDE.md` or `AGENTS.md`.
- **Repeated failures:** add a line about the right command to the project's
  `CLAUDE.md` or `AGENTS.md`.

---

## Commands

Every command answers in JSON, except `check --hook` and `instructions`.

| Command | What it does |
|---|---|
| `summary [--days N] [--project P]` | Tokens, cost, models, projects, top sessions and waste. Default: the last 30 days; `--days 0` means all. |
| `findings [--days N] [--project P]` | The waste patterns, each with what it is, what it cost, a fix, and where it happened most. |
| `sessions [--days N] [--project P] [--limit N]` | Recent sessions with their tokens, cost and tool calls. |
| `check [--dir DIR] [--hook]` | For agents: earlier mistakes in this project and what worked instead, files often re-read, repeated scripts, and the current session. `--hook` gives short plain text. |
| `instructions` | The block to add to `CLAUDE.md` or `AGENTS.md`, with this computer's command filled in. |
| `index [--rebuild]` | Read new and changed logs now. Every other command does this by itself. |
| `dashboard [--port P] [--no-browser]` | The dashboard on `http://127.0.0.1:8766/`. |

`--project` takes a project's folder name (as shown in `summary`) or its full
path.

### What counts as waste

| Finding | How Kuakgan sees it |
|---|---|
| Busy polling | Four or more checks in a row on running work (`tail` of a log, `ps`, Codex `wait`, Claude Code's task output) with nothing else in between. The first two are counted as fine. A loop or a `sleep` of 30 seconds or more counts as waiting, not polling. |
| Files read again | The same file read three or more times in one session without being changed in between. |
| Repeated scripts | An inline script (`python - <<PY`, `node -e ...`) with exactly the same body in three or more places. |
| Failed commands | Tool calls that returned an error. A mistake counts as repeated when the same program fails with the same message (apart from paths and numbers) in two or more sessions. When the next similar command of the same program worked, that is shown as the fix. |
| Very large output | One tool result of more than 10,000 tokens. |

The cost of a wasted step is the cost of the model call that made it: every
tool call is a full model turn over the whole context.

### Prices

Kuakgan knows Anthropic's list prices. To add or change a price, put a
`prices.json` next to `kuakgan.py`, in dollars per million tokens:

```json
{ "gpt-6.2-sol": { "input": 2.5, "output": 15, "cache_read": 0.25 } }
```

`cache_write` defaults to 1.25 times the input price. A model matches the
longest name in the table that it starts with.

## Privacy

Kuakgan only reads files on your computer and writes its index (`.kuakgan.db`)
next to `kuakgan.py`. The dashboard listens on `127.0.0.1` only. The index
keeps the first 400 characters of each command and the line of an error, so
that it can show you what failed; it keeps no file contents, and of the
conversation only each session's title or the start of its first prompt. Delete `.kuakgan.db` to remove
it; it is rebuilt from the logs when needed. The share card shows totals and
model names only.

## Troubleshooting

- **No sessions found.** Check that `~/.claude/projects` or `~/.codex/sessions`
  exists and holds `.jsonl` files. If Claude Code or Codex keeps its files
  elsewhere, set `CLAUDE_CONFIG_DIR` or `CODEX_HOME` as they do.
- **Numbers look off after an update.** Run `kuakgan index --rebuild`.
- **The dashboard does not open.** Another program may use port 8766; start it
  with `--port 8780`.

## Why the name

In Elizabeth Moon's *The Deed of Paksenarrion*, a kuakgan is a keeper of a
grove: someone who walks quietly among living things, sees what others
overlook, and knows what each needs. Kuakgan does the same, on a much smaller
scale, for the agents you already have.

— Totte Löfström, Kullevi

## License

MIT
