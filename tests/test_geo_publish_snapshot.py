"""クリーンなスナップショット（GitHubへ送る内容）を、作業フォルダを変えずに作る仕組みのテスト。"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "deploy" / "geo_azure"))
import publish_clean_snapshot as ps  # noqa: E402


def git(repo, *args):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.name", "tester")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "sql").mkdir()
    (tmp_path / "sql" / "a.sql").write_text("select 1;\n", encoding="utf-8")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "secret_doc.pdf").write_bytes(b"%PDF confidential")        # スナップショットに入れないもの
    git(tmp_path, "add", "app.py", "uploads/secret_doc.pdf")
    git(tmp_path, "commit", "-q", "-m", "dev history (uploads included)")
    (tmp_path / "app.py").write_text("print(2)\n", encoding="utf-8")                     # 未コミットの変更
    (tmp_path / "scratch.txt").write_text("untracked", encoding="utf-8")                 # 未追跡
    return tmp_path


def state(repo):
    return {"head": git(repo, "rev-parse", "HEAD"), "branch": git(repo, "branch", "--show-current"),
            "status": git(repo, "status", "--porcelain"), "index_tree": git(repo, "write-tree")}


def files_in(repo, commit):
    return set(git(repo, "ls-tree", "-r", "--name-only", commit).splitlines())


def test_snapshot_contains_only_selected_files_and_current_worktree_content(repo):
    before = state(repo)
    r = ps.create_snapshot_commit(repo, [Path("app.py"), Path("sql/a.sql")], "初回")
    assert r["changed"] is True and r["parent"] is None
    assert files_in(repo, r["commit"]) == {"app.py", "sql/a.sql"}                        # uploads・未追跡ファイルは入らない
    assert git(repo, "show", f"{r['commit']}:app.py") == "print(2)"                       # 未コミットの現在の内容がスナップショットされる
    assert "confidential" not in git(repo, "log", "-p", "--format=", r["commit"])        # 開発履歴（uploads）は、スナップショットの履歴に含まれない
    assert git(repo, "rev-list", "--count", r["commit"]) == "1"
    assert state(repo) == before                                                         # HEAD・ブランチ・状態・インデックスは変わらない


def test_second_snapshot_chains_to_previous_and_reflects_deletions(repo):
    first = ps.create_snapshot_commit(repo, [Path("app.py"), Path("sql/a.sql")], "初回")
    (repo / "sql" / "b.sql").write_text("select 2;\n", encoding="utf-8")
    second = ps.create_snapshot_commit(repo, [Path("app.py"), Path("sql/b.sql")], "2回目")   # a.sql を外し、b.sql を追加
    assert second["parent"] == first["commit"]
    assert second["summary"] == {"added": 1, "modified": 0, "deleted": 1}
    assert files_in(repo, second["commit"]) == {"app.py", "sql/b.sql"}                   # 削除が自動で反映される
    assert git(repo, "rev-parse", f"{second['commit']}^") == first["commit"]
    assert git(repo, "rev-parse", ps.DEPLOY_REF) == second["commit"]


def test_no_change_creates_no_new_commit(repo):
    a = ps.create_snapshot_commit(repo, [Path("app.py")], "初回")
    b = ps.create_snapshot_commit(repo, [Path("app.py")], "同じ内容")
    assert b["changed"] is False and b["commit"] == a["commit"]
    assert git(repo, "rev-list", "--count", ps.DEPLOY_REF) == "1"


def test_push_target_receives_only_snapshot_history(repo, tmp_path_factory):
    bare = tmp_path_factory.mktemp("remote")
    git(bare, "init", "-q", "--bare", "-b", "main")
    r = ps.create_snapshot_commit(repo, [Path("app.py"), Path("sql/a.sql")], "初回")
    git(repo, "push", str(bare), f"{r['commit']}:refs/heads/main")
    assert files_in(bare, "main") == {"app.py", "sql/a.sql"}
    assert git(bare, "rev-list", "--count", "main") == "1"                               # 開発履歴は送られていない
    assert "confidential" not in git(bare, "log", "-p", "--all", "--format=")


def test_real_file_list_passes_the_secret_scan():
    """実際に送る対象に、秘密情報・巨大ファイルが無いこと（送る前の検査と同じもの）。"""
    import export_clean_repo as ex
    files = ex.collect()
    assert not any(f.name in ex.SECRET_FILE_NAMES for f in files)
    assert ex.scan_files(ex.ROOT, files) == []
