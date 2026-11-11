"""Offline check of local:<base>..<head> reviews and review comments.

Builds a throwaway git repo where one test method moves to a new class verbatim
and another changes, then checks the diff, the verdicts and the comment store,
in-process (no server, no network).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="rr_local_"))
os.environ["REFACTOR_REVIEW_CACHE"] = str(tmp / "cache")
os.environ["REFACTOR_REVIEW_COMMENTS"] = str(tmp / "comments")
os.environ["REFACTOR_REVIEW_LOCAL_CLONE"] = str(tmp / "repo")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import prdata  # noqa: E402
import server  # noqa: E402

BASE = '''class TestFoo:
    def test_a(self):
        x = 1
        assert x == 1

    def test_b(self):
        y = 2
        assert y == 2
'''
HEAD = '''class TestFoo:
    def test_b(self):
        y = 3
        assert y == 3


class TestFooAccelerator:
    def test_a(self):
        x = 1
        assert x == 1
'''


def git(*a):
    return subprocess.run(["git", *a], cwd=tmp / "repo", check=True, capture_output=True, text=True).stdout.strip()


(tmp / "repo").mkdir()
git("init", "-q", "-b", "main")
git("config", "user.email", "t@t")
git("config", "user.name", "t")
(tmp / "repo" / "test_foo.py").write_text(BASE)
git("add", ".")
git("commit", "-qm", "base")
base = git("rev-parse", "HEAD")
git("checkout", "-qb", "agent_x")
(tmp / "repo" / "test_foo.py").write_text(HEAD)
git("commit", "-qam", "move test_a")

fails = 0


def check(cond, msg):
    global fails
    print(("PASS  " if cond else "FAIL  ") + msg)
    fails += not cond


ref = prdata.local_ref(base[:10], "agent_x")
meta = server.api_pr({"ref": [ref]})
check(meta["reviewable"] == ["test_foo.py"], f"local ref lists the file: {meta['reviewable']}")
check(meta["number"] == 0 and meta["url"] == "", "local ref has no PR number / URL")
f = server.api_file({"ref": [ref], "path": ["test_foo.py"]})
v = {u["base"]["qualname"]: u["verdict"] for u in f["summary"]["units"]}
check(v.get("TestFoo.test_a") == "identical", f"verbatim move is identical: {v.get('TestFoo.test_a')}")
check(v.get("TestFoo.test_b") not in ("identical", None), f"edited method is a real change: {v.get('TestFoo.test_b')}")

r = server.post_comments({"key": "test_foo", "path": "test_foo.py", "side": "head", "line": 3,
                          "unit": "TestFoo.test_b", "text": "why 3?"})
cid = r["comments"][0]["id"]
check(len(server.api_comments({"key": ["test_foo"]})["comments"]) == 1, "comment saved and read back")
server.post_comments({"key": "test_foo", "id": cid, "status": "resolved"})
check(server.load_comments("test_foo")[0]["status"] == "resolved", "comment resolved")
server.post_comments({"key": "test_foo", "id": cid, "delete": True})
check(server.load_comments("test_foo") == [], "comment deleted")
try:
    server.post_comments({"key": "test_foo", "text": "  "})
    check(False, "empty comment rejected")
except prdata.PRDataError:
    check(True, "empty comment rejected")

sys.exit(1 if fails else 0)
