# Kuakgan design

The choices behind Kuakgan, and why. `GOALS.md` says what it must achieve.

1. **The logs are the source.** Claude Code, Codex and the Antigravity CLI already write every
   model call and tool call, with token usage, to JSONL files. Kuakgan reads
   those and nothing else: no proxy, no hook that must run during work, no
   provider API. Nothing has to be set up before there is data.
2. **A small local index, updated on demand.** Reading many gigabytes of logs
   for every question would take minutes, so Kuakgan keeps an SQLite index
   (`.kuakgan.db`, standard library) of sessions, hourly usage per model, tool
   calls and per-session findings. Each command first re-reads only the log
   files whose size or modification time changed. A file is always re-read
   whole, so a session's numbers never drift; the first run uses several
   processes. Periods are at most 30 days, since Claude Code deletes
   conversations after that by default. Changing how logs are read bumps an index version, and the index is
   then rebuilt from the logs that are left; an older Kuakgan never rebuilds a newer index, and
   `dashboard` replaces an older copy that is still running. Each write is its own short
   transaction, so a hook's `check` never waits on the dashboard reading logs.
3. **One model call, one cost.** Claude Code writes an assistant message as
   several lines with the same message id, the earlier ones with a partial output
   count; usage is counted once per id, from the largest values. A forked
   sub-agent's log starts with a copy of the conversation it was forked from;
   those messages count for the agent that made them. A Codex sub-agent's log
   repeats its parent's `session_meta` after its own; the first one names it.
   Codex repeats a `token_count` event; one whose running total has not changed
   is skipped. Codex and the Antigravity CLI report cached input inside input
   tokens; Kuakgan counts it as cache read only. The Antigravity CLI's log has
   one line per step: model turns with their model, tokens and tool calls, then
   each result; its desktop app records no tokens and is not read. A price that
   changed (Gemini 4 Argon on 1 December 2026) applies by the hour of each call.
   A tool call carries the cost of the model call that made it, shared between
   the calls in that response. That is the cost of a wasted step.
4. **Commands are classified by their shape, not their meaning.** A shell
   command is a `script` (an inline program with a body), `read` (showing one
   whole file), `observe` (only looking), `wait` (a loop or long sleep) or
   `run`. Leading `cd DIR &&` and `VAR=value` steps are ignored, and an `ssh`
   command is judged by the command it runs remotely. Polling is a run of
   checks on running work: a log, a process, a background task.
5. **Mistakes are matched by their error.** A failed call keeps its most
   telling error line. Two failures are the same mistake when the same program
   fails with the same message apart from paths and numbers. The fix shown is
   the next call of the same program, within three calls, that resembles the
   failed one and worked.
6. **Prices are estimates.** List prices per million tokens are built in for
   Anthropic, OpenAI and Google models and can be added or changed in `prices.json`.
   When prices change, every cost is worked out again from the stored token
   counts, without reading the logs. Models
   without a price count tokens but no cost, and the totals say how many
   tokens that leaves out.
7. **Agents get observations and judge them.** `check` is meant to go into a
   model's context, so it returns a handful of short observations and lists, not
   raw data and not rules. The agent decides what matters and how to solve it,
   and asks the user before a lasting change. Its
   "current session" is the session in that project that was written to most
   recently, within the last half hour.
8. **A project is a repository.** A session belongs to the Git repository of its
   working folder, read from the folder's `.git` where the logs are, so worktrees
   and subfolders count as their repository. A worktree deleted before Kuakgan
   read it joins the repository that at least four in five of the worktrees beside
   it belong to; otherwise it keeps its folder name.
9. **Other machines are read where they are.** `machines add` remembers a
   computer reachable over SSH without a password; `machines seen` lists computers
   the sessions used over SSH, for the agent and user to judge. Kuakgan then sends its own
   source over SSH to that computer's Python, which lists its logs and reads only
   the new or changed ones; only the results come back. Nothing is installed
   there, and the logs never leave it. Other machines are fetched at most every
   ten minutes, except by `index`.
10. **The dashboard is one page.** `kuakgan.py dashboard` serves
   `dashboard.html` and `/data.json` on 127.0.0.1, refusing requests for any
   other host name (DNS rebinding). Days and hours are the viewer's local ones.
   Cost leads, labelled as API prices, since many users pay a flat fee. Each
   model keeps one colour in every view, and models are named as people say them.
   "Ask my agent" copies a request for the user's agent to look into one finding
   and suggest a fix, rather than the dashboard prescribing one. It is designed as one 1200×630
   frame (a link preview's size) and scaled to the window; each list shows as
   many whole rows as fit and counts the rest, so nothing is cut off. A narrow
   window stacks the cards instead. The Screenshot button has Edge or Chrome
   draw that frame without a window, in the current light or dark theme. One
   type scale, spacing grid and palette set the look. Private mode numbers
   projects, sessions and machines instead of naming them. Charts are inline SVG;
   no outside scripts or fonts, so it works offline. A background
   thread reads new logs every minute and prepares the usual periods, so that
   switching between them answers at once.
11. **Installation is a clone.** One Python file using only the standard
   library, plus the page and the logo. No packaging.

## Testing

- Unit tests build small Claude Code, Codex and Antigravity CLI logs in a temporary folder and
  check usage counting, command classification, each finding, `check`, and
  the dashboard's data.
- GitHub Actions runs them on Linux, macOS and Windows with the oldest Python
  we promise (3.9) where available.
