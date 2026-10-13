"""Where a lane's live output and status come from.

Every source is read-only: it runs a fixed argv (never a shell string) or reads a file,
with a timeout, and returns text. A broken source shows up as an error on its card;
it never takes the board down.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
TIMEOUT_S = 5.0


def run(argv: list[str], cwd: str | None = None, timeout: float = TIMEOUT_S) -> str:
    try:
        out = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SourceError(f"{argv[0]}: {exc}") from exc
    if out.returncode != 0:
        raise SourceError(f"{argv[0]} exited {out.returncode}: {out.stderr.strip()[:200]}")
    return out.stdout


class SourceError(RuntimeError):
    pass


def clean(text: str, lines: int) -> str:
    rows = [ANSI.sub("", r).rstrip() for r in text.splitlines()]
    rows = [r for r in rows if r.strip()]
    return "\n".join(rows[-lines:])


@dataclass
class Output:
    text: str
    status: str | None = None  # "working" | "idle" | "done" | None when the source cannot tell


def read_output(spec: dict, lines: int) -> Output:
    """spec = {"herdr": "<pane id>"} | {"tmux": "<target>"} | {"file": "<path>"} | {"command": [argv...]}"""
    if "herdr" in spec:
        text = run(["herdr", "pane", "read", spec["herdr"], "--source", "visible"])
        return Output(clean(text, lines), herdr_status(spec["herdr"]))
    if "tmux" in spec:
        return Output(clean(run(["tmux", "capture-pane", "-p", "-t", spec["tmux"], "-S", f"-{lines * 3}"]), lines))
    if "file" in spec:
        path = Path(spec["file"]).expanduser()
        if not path.exists():
            raise SourceError(f"no such file: {path}")
        with path.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 64_000))
            return Output(clean(fh.read().decode("utf-8", "replace"), lines))
    if "command" in spec:
        argv = spec["command"]
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise SourceError("command must be a list of strings (no shell)")
        return Output(clean(run(argv), lines))
    raise SourceError(f"unknown output source: {sorted(spec)}")


def herdr_status(pane: str) -> str | None:
    try:
        doc = json.loads(run(["herdr", "pane", "list"]))
    except (SourceError, ValueError):
        return None
    for p in doc.get("result", {}).get("panes", []):
        if p.get("pane_id") == pane:
            s = p.get("agent_status")
            return {"working": "working", "busy": "working", "done": "idle", "idle": "idle"}.get(s)
    return None


@dataclass
class GitInfo:
    branch: str
    head: str
    subject: str
    age: str
    dirty: int
    ahead: int | None


def git_info(repo: str, base: str | None) -> GitInfo:
    cwd = str(Path(repo).expanduser())
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd).strip()
    head, age, subject = run(["git", "log", "-1", "--format=%h%x00%cr%x00%s"], cwd).rstrip("\n").split("\0", 2)
    dirty = len([r for r in run(["git", "status", "--porcelain"], cwd).splitlines() if r.strip()])
    ahead = None
    if base:
        try:
            ahead = int(run(["git", "rev-list", "--count", f"{base}..HEAD"], cwd).strip())
        except (SourceError, ValueError):
            ahead = None
    return GitInfo(branch, head, subject, age, dirty, ahead)
