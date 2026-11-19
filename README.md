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
| **To share** | The whole dashboard fits in one window. The Screenshot button copies a picture of it; Private hides project, session and machine names first. |

Costs are estimates at API list prices. If you use a subscription you pay a flat
fee instead; the estimate shows what the same work would cost through the API.
Models without a known price count tokens only, and the totals say how many.

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

Kuakgan needs **Python 3.9 or newer** and nothing else. You, the agent, check
for it and install it for the user if it is missing; the user should not have
to do anything but say yes. Try `python3 --version`,
then `python --version` and `py -3 --version`. On Windows, `python3` and
sometimes `python` are a Microsoft Store placeholder that prints a message
instead of a version; use whichever command prints a real version.

- Missing on Windows: tell the user this installs Python and accepts the
  winget/Microsoft terms, and with their OK run
  `winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements`.
  A new install is often not on the PATH until the terminal or app restarts,
  so do not ask for a restart: try `py -3 --version` (the launcher usually
  works at once), or call Python by its full path,
  `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` (in Git Bash
  `"$LOCALAPPDATA/Programs/Python/Python312/python.exe"`). Use that command
  or full path in every later step, including the hook in `settings.json`.
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
Codex logs; 10 to 20 GB of logs take between half a minute and a few minutes, and it prints
its progress. Later runs read only what is new, in a second or two. Then run
`kuakgan summary`, which gives the main points in a few kilobytes of JSON, and
tell the user in a few plain sentences what it shows: tokens and estimated
cost in the last 30 days, the main models, and the biggest waste with its fix.
If much of the usage comes from models without a price, say so: the dollar
figure then leaves that part out (`cost_note` says how much).

Kuakgan finds the logs in `~/.claude/projects`, `~/.codex/sessions` and
`~/.codex/archived_sessions` (or under `CLAUDE_CONFIG_DIR` and `CODEX_HOME`
when those are set). If a folder is missing, that tool has simply not been
used on this computer.

Kuakgan looks back at most 30 days: Claude Code deletes conversations older
than that by default, so a longer period would only look empty.

### 3. Other computers (Agent, after asking the user)

Ask the user whether they also run Claude Code or Codex on other computers,
such as a second laptop or build machines. If they do, this computer can
include those logs too, so the dashboard shows all of their usage and
`kuakgan check` learns from mistakes made on every machine. Nothing is
installed on the other computers: Kuakgan reads their logs over SSH, and only
the results travel.

Each other computer needs:

- **SSH from this computer without a password.** Check with
  `ssh -o BatchMode=yes USER@ADDRESS echo ok`, which must print `ok` without
  asking for anything. If the user has
  [Girdish](https://github.com/Kullevi/girdish) set up, its machines already
  work. Otherwise help the user turn on SSH and copy this computer's key
  there; the [Girdish README](https://github.com/Kullevi/girdish#setup-for-the-agent-doing-it)
  has the exact steps for Linux, macOS and Windows. Any step that asks for a
  password is for the user to run in their own terminal, never in chat.
- **Python 3.9 or newer** there (usual on Linux and macOS; on Windows see
  step 1).

Then add each computer:

```
kuakgan machines add NAME USER@ADDRESS
```

`NAME` is a short name the user recognises, such as `build-pc`. Instead of
`USER@ADDRESS` you can give a `Host` alias from `~/.ssh/config`, and `--key`
and `--port` when needed. The first time, that computer reads all of its own
logs, which takes as long as step 2 did here. Afterwards `summary`, `findings`
and the dashboard fetch what is new from every machine at most every ten
minutes, and `kuakgan index` does it at once. `kuakgan machines` lists them;
a machine that is off is skipped and tried again next time.

### 4. Show the user (Agent)

Start the dashboard:

```
kuakgan dashboard --background
```

It starts as a process of its own, so it keeps running after your command
returns, and answers once it is ready. It opens `http://127.0.0.1:8766/` in a
window of its own (with Edge or Chrome installed; otherwise in a normal browser
tab). On a computer without a screen, such as one you reach over SSH, it
prints the address instead: give it to the user. Tell the user that:

- the period buttons and the project menu change what is shown;
- "Private" replaces project, session and machine names with numbers, so
  the window can be shown or shared;
- "Screenshot" copies a picture of the window, ready to paste into a post;
- they can ask you to "open the Kuakgan dashboard" again later.

To stop it: `kuakgan dashboard --stop`.

### 5. Let the agents use it (Agent, after asking the user)

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

### 6. Act on what it found (Agent, with the user)

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
| `summary [--days N] [--project P] [--full]` | The main points: tokens, cost, models, machines, projects, top sessions and waste. Default and longest: the last 30 days. `--full` adds every day and where each finding happened. |
| `findings [--days N] [--project P]` | The waste patterns, each with what it is, what it cost, a fix, and where it happened most. |
| `sessions [--days N] [--project P] [--limit N]` | Recent sessions with their tokens, cost and tool calls. |
| `check [--dir DIR] [--hook]` | For agents: earlier mistakes in this project and what worked instead, files often re-read, repeated scripts, and the current session. `--hook` gives short plain text. |
| `instructions` | The block to add to `CLAUDE.md` or `AGENTS.md`, with this computer's command filled in. |
| `machines [add NAME ADDRESS [--user U] [--key PATH] [--port P] \| remove NAME]` | Other computers whose logs to include, read over SSH. Without arguments, lists them. |
| `index [--rebuild]` | Read new and changed logs now, here and on the other machines. Every other command does this by itself. |
| `dashboard [--background] [--stop] [--port P] [--no-browser]` | The dashboard on `http://127.0.0.1:8766/`. `--background` starts it as a process of its own; `--stop` stops it. |

`--project` takes a project's folder name (as shown in `summary`) or its full
path. Tokens and cost count the model calls made in the period; sessions, tool
calls and waste count the sessions that were active in it.

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

Kuakgan knows Anthropic's and OpenAI's list prices (standard tier). To add or change a price, put a
`prices.json` next to `kuakgan.py`, in dollars per million tokens:

```json
{ "gpt-6.2-sol": { "input": 2.5, "output": 15, "cache_read": 0.25 } }
```

`cache_write` defaults to 1.25 times the input price. A model gets the price of
the longest name in the table that it is or starts with as a whole part
(`gpt-5` prices `gpt-5-mini-2025-08-07` but not `gpt-5.7`). When prices change,
all costs are worked out again from the counted tokens.

## Add another tool

Kuakgan reads Claude Code and Codex, because those are the tools we use and
can test. Others, such as Gemini CLI, Antigravity, Grok or Cursor, are welcome
as pull requests. Reading a tool takes three things in `kuakgan.py`:

1. **Where its logs are:** a few lines in `log_files()`, with a `source` name.
2. **A reader:** a function like `parse_claude()` or `parse_codex()` that turns
   one log file into sessions: usage per model call (`add_usage`) and tool
   calls (`make_call`). Everything after that, from findings to the dashboard,
   works unchanged. Add the source to `parse_file()`.
3. **A test:** a small, made-up log in the format the tool really writes, in
   `tests/test_kuakgan.py`, checking tokens and a finding.

Please build it from real logs of a current version of the tool, and say
which version in the pull request.

## Privacy

Kuakgan only reads files on your computer, and on the other computers you add
over SSH, and writes its index (`.kuakgan.db`) next to `kuakgan.py`. The list
of other computers is in `kuakgan.json` next to it. The dashboard listens on `127.0.0.1` only. The index
keeps the first 400 characters of each command and the line of an error, so
that it can show you what failed; it keeps no file contents, and of the
conversation only each session's title or the start of its first prompt. Delete `.kuakgan.db`
to forget it all; it is rebuilt from the logs. In Private mode the dashboard and its
screenshots show no project, session or machine names, commands or file names.

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
