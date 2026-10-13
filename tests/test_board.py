import json
import subprocess
from urllib.request import urlopen

import pytest

from agentboard import board, sources


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", "base")
    git(r, "checkout", "-q", "-b", "feat")
    (r / "a.txt").write_text("x")
    git(r, "add", "a.txt")
    git(r, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "add <a> & b")
    return r


def cfg(tmp_path, lanes):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"title": "T <x>", "lanes": lanes}))
    return board.load_config(p)


def test_file_lane_git_and_done(tmp_path, repo):
    log = tmp_path / "l.log"
    log.write_text("\x1b[31mred\x1b[0m\n\nline2\n")
    c = cfg(tmp_path, [{"id": "A", "output": {"file": str(log)}, "repo": str(repo), "base": "main",
                        "done_file": "DONE.md"}])
    lane = board.snapshot(c)["lanes"][0]
    assert lane["output"] == "red\nline2" and lane["state"] == "working"
    assert lane["git"]["branch"] == "feat" and lane["git"]["ahead"] == 1 and lane["git"]["dirty"] == 0
    (repo / "DONE.md").write_text("ok")
    assert board.snapshot(c)["lanes"][0]["state"] == "done"


def test_broken_source_is_blocked_not_fatal(tmp_path):
    c = cfg(tmp_path, [{"id": "A", "output": {"file": str(tmp_path / "missing")}},
                       {"id": "B", "output": {"command": ["false"]}},
                       {"id": "C", "after": "A"}])
    snap = board.snapshot(c)
    assert [lane["state"] for lane in snap["lanes"]] == ["blocked", "blocked", "waiting"]
    assert snap["counts"]["blocked"] == 2


def test_command_must_be_argv_list():
    with pytest.raises(sources.SourceError):
        sources.read_output({"command": "rm -rf /"}, 5)


def test_render_escapes_everything(tmp_path, repo):
    c = cfg(tmp_path, [{"id": "<i>", "title": "<script>", "repo": str(repo)}])
    page = board.render(board.snapshot(c), 10)
    assert "<script>" not in page and "&lt;script&gt;" in page and "add &lt;a&gt; &amp; b" in page


def test_bad_config(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"lanes": []}')
    with pytest.raises(ValueError):
        board.load_config(p)


def test_finished_lanes_hide_after_the_window(tmp_path, repo):
    (repo / "DONE.md").write_text("ok")
    c = cfg(tmp_path, [{"id": "A", "repo": str(repo), "done_file": "DONE.md"}, {"id": "B", "after": "A"}])
    snap = board.snapshot(c)
    done_at = snap["lanes"][0]["done_at"]
    assert [x["id"] for x in board.visible_lanes(snap, 600, False, done_at + 60)[0]] == ["A", "B"]
    kept, hidden = board.visible_lanes(snap, 600, False, done_at + 601)
    assert [x["id"] for x in kept] == ["B"] and hidden == 1
    assert len(board.visible_lanes(snap, 600, True, done_at + 601)[0]) == 2  # checkbox
    assert len(board.visible_lanes(snap, 0, False, done_at + 10**7)[0]) == 2  # 0 = never hide


def test_server_serves_page_and_api(tmp_path):
    c = cfg(tmp_path, [{"id": "A", "output": {"command": ["echo", "hello"]}}])
    srv = board.ThreadingHTTPServer(("127.0.0.1", 0), board.make_handler(board.Board(c)))
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert "hello" in urlopen(base + "/").read().decode()
        assert json.loads(urlopen(base + "/api/snapshot").read())["lanes"][0]["output"] == "hello"
    finally:
        srv.shutdown()
