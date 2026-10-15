"""GitHub に送る「クリーンなスナップショット」を、作業フォルダを変えずに作る（1フォルダ運用）。

    python deploy/geo_azure/publish_clean_snapshot.py -m "コミットメッセージ"
        → スナップショットのコミットをローカルに作る（GitHubには送らない。内容の確認用）
    python deploy/geo_azure/publish_clean_snapshot.py -m "..." --push https://github.com/ORG/REPO.git --branch BRANCH
        → 作ったコミットを、GitHubの指定ブランチへ送る（送る前に、必ず内容を確認すること）

仕組み（Gitの内部機能を使う）:
    ・送る対象は export_clean_repo.py の一覧（コード・設定・画面のソース・SQL・テスト・デプロイ設定）だけ。
      uploads/、docs/ の巨大zip、ログ、評価結果、config.json などは含まれない。
    ・作業ツリー・インデックス・現在のブランチ・未コミットの変更には一切触れない
      （一時的なインデックスでツリーを組み立てる）。
    ・履歴は、このスナップショット同士の連なり（ローカルの参照 refs/geo-deploy/main に記録）。
      現状の履歴（uploads等を含む過去のコミット）は GitHub に出ない。
    ・前回のスナップショットから削除したファイルは、今回のツリーから自動的に消える。
    ・秘密情報らしきファイル・文字列、50MB超のファイルが見つかれば、作成せずに止まる。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_clean_repo as ex  # noqa: E402

DEPLOY_REF = "refs/geo-deploy/main"


def _git(repo: Path, *args: str, env: dict | None = None, input: bytes | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=repo, env=env, input=input, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} に失敗: {r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return r.stdout.decode("utf-8", "replace").strip()


def _ref(repo: Path, name: str) -> str | None:
    r = subprocess.run(["git", "rev-parse", "--verify", "-q", name], cwd=repo, capture_output=True)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def create_snapshot_commit(repo: Path, rels: list[Path], message: str, ref: str = DEPLOY_REF, dry_run: bool = False) -> dict:
    """rels（repoからの相対パス）だけを含むツリーを作り、前回のスナップショットを親にしてコミットする。
    作業ツリー・本物のインデックス・HEAD・ブランチは変更しない。戻り値: {commit, tree, parent, changed, summary}。
    dry_run=True のときは、ツリーの内容と前回との差だけを計算し、コミットも参照も作らない（commit は None）。"""
    rels = sorted({Path(r).as_posix() for r in rels})
    blobs = _git(repo, "hash-object", "-w", "--stdin-paths", input=("\n".join(rels) + "\n").encode("utf-8")).split("\n")
    if len(blobs) != len(rels):
        raise RuntimeError("ファイルの登録数が一致しません")
    info = "".join(f"100644 blob {sha}\t{path}\n" for sha, path in zip(blobs, rels)).encode("utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "snapshot-index")}      # 一時的なインデックス（本物は使わない）
        _git(repo, "update-index", "--add", "--index-info", env=env, input=info)
        tree = _git(repo, "write-tree", env=env)
    parent = _ref(repo, ref)
    summary = {"added": 0, "modified": 0, "deleted": 0}
    if parent:
        if _git(repo, "rev-parse", f"{parent}^{{tree}}") == tree:
            return {"commit": parent, "tree": tree, "parent": None, "changed": False, "summary": summary, "files": len(rels)}
        for line in _git(repo, "diff-tree", "-r", "--name-status", parent, tree).splitlines():
            summary[{"A": "added", "M": "modified", "D": "deleted"}.get(line[:1], "modified")] += 1
    if dry_run:
        return {"commit": None, "tree": tree, "parent": parent, "changed": True, "summary": summary, "files": len(rels)}
    args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else [])
    commit = _git(repo, *args)
    _git(repo, "update-ref", ref, commit)
    return {"commit": commit, "tree": tree, "parent": parent, "changed": True, "summary": summary, "files": len(rels)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-m", "--message", default="", help="コミットメッセージ（日本語で書く）。--dry-run のときは不要")
    ap.add_argument("--dry-run", action="store_true", help="コミットを作らず、対象・検査結果・前回との差だけを表示する")
    ap.add_argument("--push", metavar="URL", help="作ったコミットを送る先のリポジトリURL（省略するとローカルに作るだけ）")
    ap.add_argument("--branch", help="--push のとき、送り先のブランチ名")
    args = ap.parse_args()
    if args.push and not args.branch:
        sys.exit("--push には --branch（送り先のブランチ名）も指定してください。")
    files = ex.collect()
    problems = ex.scan_files(ex.ROOT, files)
    if problems:
        print("【検査で問題が見つかりました。スナップショットは作りません】")
        for p in problems:
            print(" -", p)
        sys.exit(1)
    if not args.dry_run and not args.message.strip():
        sys.exit("-m（コミットメッセージ）を指定してください。")
    result = create_snapshot_commit(ex.ROOT, files, args.message, dry_run=args.dry_run)
    size = sum((ex.ROOT / f).stat().st_size for f in files)
    print(f"対象: {result['files']}ファイル / {size / 1024 / 1024:.1f}MB（検査OK: 秘密情報・50MB超のファイルなし）")
    if args.dry_run:
        s = result["summary"]
        print(f"[dry-run] コミットは作っていません。ツリー: {result['tree'][:10]}" + (
            f" / 前回との差: 追加{s['added']} / 変更{s['modified']} / 削除{s['deleted']}" if result["parent"] else "（初回）"))
        return
    if not result["changed"]:
        print("前回のスナップショットから変更はありません。新しいコミットは作りませんでした。")
        return
    s = result["summary"]
    print(f"スナップショット: {result['commit'][:10]}  前回との差: 追加{s['added']} / 変更{s['modified']} / 削除{s['deleted']}"
          if result["parent"] else f"スナップショット（初回）: {result['commit'][:10]}")
    if args.push:
        out = subprocess.run(["git", "push", args.push, f"{result['commit']}:refs/heads/{args.branch}"], cwd=ex.ROOT)
        sys.exit(out.returncode)
    print("GitHubへは送っていません。内容を確認したら、--push URL --branch 名前 を付けて、もう一度実行してください。")


if __name__ == "__main__":
    main()
