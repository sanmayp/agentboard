"""agentboard: one live page for many coding agents working in parallel.

    python -m agentboard board.json            # http://127.0.0.1:8790/
    python -m agentboard board.json --once     # print the snapshot as JSON and exit

Read-only by design: no button on the page acts on an agent, a repo or a pane.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agentboard.sources import SourceError, git_info, read_output

STATES = ("blocked", "working", "idle", "done", "waiting")


def load_config(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text())
    if not isinstance(cfg.get("lanes"), list) or not cfg["lanes"]:
        raise ValueError("config needs a non-empty 'lanes' list")
    for i, lane in enumerate(cfg["lanes"]):
        if not lane.get("id"):
            raise ValueError(f"lane {i} has no id")
    cfg.setdefault("title", "agentboard")
    cfg.setdefault("refresh_s", 10)
    cfg.setdefault("lines", 14)
    cfg.setdefault("hide_done_after_s", 600)  # 0 = keep finished lanes forever
    return cfg


def lane_snapshot(lane: dict, lines: int) -> dict:
    snap = {k: lane.get(k) for k in ("id", "title", "agent", "after")}
    snap["errors"] = []
    status = None
    if lane.get("output"):
        try:
            out = read_output(lane["output"], lane.get("lines", lines))
            snap["output"], status = out.text, out.status
        except SourceError as exc:
            snap["output"] = ""
            snap["errors"].append(str(exc))
    if lane.get("repo"):
        try:
            g = git_info(lane["repo"], lane.get("base"))
            snap["git"] = g.__dict__
        except SourceError as exc:
            snap["errors"].append(str(exc))
    done_file = lane.get("done_file")
    done_path = Path(lane.get("repo", "."), done_file).expanduser() if done_file else None
    snap["done"] = bool(done_path and done_path.exists())
    snap["done_at"] = done_path.stat().st_mtime if snap["done"] else None
    snap["state"] = derive_state(snap, status)
    return snap


def derive_state(snap: dict, status: str | None) -> str:
    if snap["done"]:
        return "done"
    if snap.get("after") and not snap.get("output") and not snap.get("git"):
        return "waiting"
    if snap["errors"] and not snap.get("output"):
        return "blocked"
    return status or ("working" if snap.get("output") else "idle")


def snapshot(cfg: dict) -> dict:
    with ThreadPoolExecutor(max_workers=8) as pool:
        lanes = list(pool.map(lambda lane: lane_snapshot(lane, cfg["lines"]), cfg["lanes"]))
    counts = {s: sum(1 for lane in lanes if lane["state"] == s) for s in STATES}
    return {"title": cfg["title"], "taken_at": time.strftime("%Y-%m-%d %H:%M:%S"), "counts": counts, "lanes": lanes}


def visible_lanes(snap: dict, hide_after_s: int, show_done: bool, now: float | None = None) -> tuple[list, int]:
    """Drop lanes that finished more than hide_after_s ago (by the done file's mtime, so it survives restarts)."""
    if show_done or not hide_after_s:
        return snap["lanes"], 0
    now = time.time() if now is None else now
    keep = [lane for lane in snap["lanes"] if not (lane.get("done_at") and now - lane["done_at"] > hide_after_s)]
    return keep, len(snap["lanes"]) - len(keep)


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def render(snap: dict, refresh_s: int, hide_after_s: int = 0, show_done: bool = False) -> str:
    lanes, hidden = visible_lanes(snap, hide_after_s, show_done)
    cards = []
    for lane in lanes:
        g = lane.get("git") or {}
        git_line = ""
        if g:
            ahead = "" if g.get("ahead") is None else f" · {g['ahead']} ahead"
            dirty = f" · {g['dirty']} uncommitted" if g.get("dirty") else ""
            git_line = (f'<div class="git"><b>{esc(g["branch"])}</b> {esc(g["head"])} {esc(g["subject"])}'
                        f' <span class="m">({esc(g["age"])}{ahead}{dirty})</span></div>')
        errs = "".join(f'<div class="err">{esc(e)}</div>' for e in lane["errors"])
        after = f'<span class="m">after {esc(lane["after"])}</span>' if lane.get("after") else ""
        cards.append(f"""<section class="card s-{lane['state']}" id="{esc(lane['id'])}">
<header><span class="id">{esc(lane['id'])}</span><span class="t">{esc(lane.get('title'))}</span>
<span class="pill">{lane['state']}</span></header>
<div class="m">{esc(lane.get('agent'))} {after}</div>{git_line}{errs}
<pre>{esc(lane.get('output'))}</pre></section>""")
    toggle = ""
    if hide_after_s:
        note = f"{hidden} finished hidden" if hidden else f"finished lanes hide after {hide_after_s // 60} min"
        checked = " checked" if show_done else ""
        toggle = (f'<form method="get"><label class="m"><input type="checkbox" name="done" value="show"{checked} '
                  f'onchange="this.form.submit()"> show completed ({note})</label></form>')
    counts = " ".join(f'<span class="pill s-{k}">{k} {v}</span>' for k, v in snap["counts"].items() if v)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="{refresh_s}">
<title>{esc(snap['title'])}</title><style>{CSS}</style></head><body>
<h1>{esc(snap['title'])}</h1><div class="bar">{counts}<span class="m">as of {esc(snap['taken_at'])} · refresh {refresh_s}s · read-only</span>{toggle}</div>
<main>{''.join(cards)}</main></body></html>"""


CSS = """
:root{--bg:#f7f7f5;--fg:#1d1d1b;--m:#6b6b66;--card:#fff;--line:#e2e2dc;--work:#2563eb;--idle:#a16207;--done:#15803d;--block:#b91c1c;--wait:#6b7280}
@media (prefers-color-scheme:dark){:root{--bg:#121211;--fg:#eceae4;--m:#9a978f;--card:#1c1c1a;--line:#2e2d2a}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif}
h1{font-size:18px;margin:0 0 8px}.bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:12px}
main{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr))}
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--wait);border-radius:8px;padding:10px;min-width:0}
header{display:flex;gap:8px;align-items:baseline}.id{font:600 13px ui-monospace,monospace}.t{flex:1;font-weight:600}
.m{color:var(--m);font-size:12px}.git{font:12px ui-monospace,monospace;margin:4px 0;overflow-wrap:anywhere}
.err{color:var(--block);font-size:12px}pre{margin:6px 0 0;max-height:220px;overflow:auto;font:11px/1.35 ui-monospace,monospace;background:var(--bg);padding:6px;border-radius:4px;white-space:pre-wrap}
.pill{font-size:11px;padding:1px 7px;border-radius:9px;border:1px solid currentColor}
.s-working{border-left-color:var(--work)}.s-working .pill,.pill.s-working{color:var(--work)}
.s-idle{border-left-color:var(--idle)}.s-idle .pill,.pill.s-idle{color:var(--idle)}
.s-done{border-left-color:var(--done)}.s-done .pill,.pill.s-done{color:var(--done)}
.s-blocked{border-left-color:var(--block)}.s-blocked .pill,.pill.s-blocked{color:var(--block)}
.s-waiting .pill,.pill.s-waiting{color:var(--wait)}
"""


class Board:
    """Caches one snapshot per refresh period so many viewers cost one round of reads."""

    def __init__(self, cfg: dict):
        self.cfg, self._lock, self._snap, self._at = cfg, threading.Lock(), None, 0.0

    def get(self) -> dict:
        with self._lock:
            if self._snap is None or time.monotonic() - self._at >= self.cfg["refresh_s"]:
                self._snap, self._at = snapshot(self.cfg), time.monotonic()
            return self._snap


def make_handler(board: Board):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):  # noqa: N802
            path, _, query = self.path.partition("?")
            if path in ("/", "/index.html"):
                show = "done=show" in query.split("&")
                page = render(board.get(), board.cfg["refresh_s"], board.cfg["hide_done_after_s"], show)
                body, ctype = page.encode(), "text/html; charset=utf-8"
            elif path == "/api/snapshot":
                body, ctype = json.dumps(board.get()).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agentboard", description=__doc__.split("\n")[0])
    ap.add_argument("config")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default loopback; tunnel to view remotely)")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--once", action="store_true", help="print one JSON snapshot and exit")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    if a.once:
        json.dump(snapshot(cfg), sys.stdout, indent=2)
        print()
        return 0
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(Board(cfg)))
    print(f"agentboard on http://{a.host}:{a.port}/ ({len(cfg['lanes'])} lanes)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
