# agentboard

**One live page for a fleet of coding agents building in parallel.**

A year ago, "parallel development" meant several people on several branches. Now it often means five agents
(Claude Code, Cortex, Gemini/Antigravity, OpenCode, Codex...) each in its own terminal pane and git worktree,
running for hours or days. The hard part is no longer writing the code. It's *seeing* the work: who is
actually busy, who stalled with uncommitted edits, which lane finished, which is waiting on another, and
whether the merged tip is still green.

agentboard is the smallest tool that answers that. You give it a list of lanes (one per work package), and
it serves a single auto-refreshing page with one card per lane:

- **live output** from wherever the agent runs: a herdr pane, a tmux pane, a log file
  or any read-only command;
- **git state** of the lane's worktree: branch, last commit and its age, commits ahead of base, uncommitted files;
- **a derived state**: `working`, `idle`, `blocked` (its source is broken), `waiting` (on another lane)
  or `done` (its done file exists);
- **self-maintenance for long runs**: finished lanes hide after `hide_done_after_s` (default 10 min, 0 = never),
  timed by the done file itself so it survives restarts. A **show completed** checkbox brings them back. A board
  left up for a week shows what is still moving, not a wall of old green cards.

It is **read-only by design**. There is no button that types into a pane, merges a branch or kills a process:
the board is for the human deciding what to do next, not another agent with hands.

## Why it's built this way

- **Zero dependencies.** The Python standard library only. Copy the folder to a build box and run it.
- **Many viewers, one round of reads.** Snapshots are cached per refresh period, so ten open tabs do not mean
  ten `git status` storms across your worktrees.
- **Fails per lane, not per board.** A dead pane or a missing repo turns one card red with the error. The page
  still renders.
- **No shell strings.** Every source is a fixed argv with a timeout, so a config typo can't become a command.
- **Loopback by default.** Bind elsewhere only on purpose; otherwise tunnel (`ssh -L 8790:127.0.0.1:8790 host`).
- **Escapes everything.** Agent output is untrusted text and is rendered as text.

## Run it

```bash
python -m agentboard examples/board.json            # http://127.0.0.1:8790/
python -m agentboard examples/board.json --once     # one JSON snapshot to stdout (for scripts and other agents)
curl -s 127.0.0.1:8790/api/snapshot                 # same, from a running board
```

## Config

```json
{
  "title": "Feature X: parallel build",
  "refresh_s": 10,
  "lines": 14,
  "hide_done_after_s": 600,
  "lanes": [
    {"id": "P-01", "title": "Rust core", "agent": "agent A",
     "output": {"herdr": "w1:p2"}, "repo": "~/work/proj-p01", "base": "main", "done_file": "DONE-P-01.md"},
    {"id": "P-02", "title": "API", "agent": "agent B", "output": {"tmux": "build:p02"}},
    {"id": "P-03", "title": "UI", "output": {"file": "~/work/logs/p03.log"}},
    {"id": "P-04", "title": "Suite", "output": {"command": ["tail", "-n", "40", "/tmp/suite.log"]}},
    {"id": "P-05", "title": "Deploy", "after": "P-01, P-02"}
  ]
}
```

| Lane key | Meaning |
|---|---|
| `output` | one of `herdr` (pane id; also gives working/idle from herdr's agent status), `tmux` (target), `file` (tail), `command` (argv list) |
| `repo`, `base` | worktree to read git state from; `base` enables "N ahead" |
| `done_file` | path relative to `repo`; when it exists the lane is `done` (agents write it as their last step) |
| `after` | free text; a lane with nothing to show yet and an `after` is `waiting` |

## The convention that makes it work

Give each agent one worktree, one branch and one brief, and make its last step "write `DONE-<id>.md` with what
you did and how you tested it." The board needs nothing else from the agent.

## Tests

```bash
python -m pytest -q
```

MIT licensed.
