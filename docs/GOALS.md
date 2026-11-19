# Kuakgan goals

This is the target: what Kuakgan must achieve and what it deliberately leaves
out. `DESIGN.md` explains the choices behind it.

## Purpose

People who build with Claude Code or Codex spend a lot of tokens and cannot see
where they go, or how much of it is waste: agents polling a log, re-reading
the same files, rewriting the same helper script, and repeating mistakes that
earlier sessions already made and fixed. Kuakgan reads the logs both tools
already keep and makes this visible, with a concrete fix for each kind of
waste. It also gives the agents the lessons of earlier sessions, so they waste
less on their own.

## Who it is for

Someone who builds with AI agents, often without much experience of terminals
or configuration. They should not need to read code, edit config files or
create accounts.

## The outcome

1. **Setup from a link.** The user gives this repository's link to Claude or
   Codex. The agent reads the README, installs Kuakgan, reads the logs once and
   tells the user what it found.
2. **The user sees where tokens go.** A dashboard shows tokens and estimated
   cost over time per model, project and session, the waste found, and how to
   fix it.
3. **Agents use it regularly.** With the user's consent, the agent adds a short
   block to the user's own `CLAUDE.md` / `AGENTS.md` (and, for Claude Code, a
   SessionStart hook). From then on agents run `kuakgan check` at the start of
   a task and get the earlier mistakes in that project with what worked
   instead.
4. **Something to share.** A card with the user's totals, without project or
   session names.

## What Kuakgan does

- **Read the logs.** Claude Code's `~/.claude/projects/**/*.jsonl` (subagents
  included) and Codex's `~/.codex/sessions/**/rollout-*.jsonl`, into a local
  index that is updated incrementally.
- **Count usage.** Tokens by kind (input, cache read, cache write, output),
  model calls, tool calls and estimated cost at list prices, per hour, model,
  session and project.
- **Find waste.** Busy polling, files re-read without change, repeated inline
  scripts, failed commands (and mistakes repeated across sessions, with the
  fix that worked), very large tool outputs. Each with its cost and a fix.
- **Answer agents.** `kuakgan check` for the current project and session;
  `summary`, `findings` and `sessions` as JSON.
- **Show a dashboard** on localhost, with a share card.

## Left out

- **Changing anything by itself.** Kuakgan reports; the agent applies a fix
  (a config line, an instruction, a saved script) when the user agrees.
- **Live tracking or a background service.** The index is brought up to date
  whenever a command or the dashboard asks.
- **Model evaluation.** Measuring a new model on the user's own code is a
  separate tool.
- **Accounts, telemetry or a cloud.** Nothing leaves the computer.

## Constraints

- One Python 3.9+ file plus the dashboard page and logo, standard library only.
- Windows, macOS and Linux.
- Local only: the dashboard listens on localhost.
- MIT licence.

## Done when

- A fresh Claude Code session and a fresh Codex session, given only the link,
  each get Kuakgan working and tell the user what it found, with no help
  beyond the user's yes to the optional steps.
- With the instruction block in place, an agent runs `kuakgan check` at the
  start of a task and avoids a mistake an earlier session made.
- Several gigabytes of logs are read in a few minutes the first time and in a
  few seconds afterwards.
