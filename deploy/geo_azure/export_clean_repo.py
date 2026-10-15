"""GitHub に上げる「クリーンなリポジトリ」の中身を、別フォルダへ取り出す（過去の履歴は引き継がない）。

目的: 過去に追跡された uploads/（アップロード資料）や docs/ の巨大zip、ログ、評価結果などを GitHub に出さず、
      アプリのコード・設定・画面のソース・SQL・テスト・デプロイ設定だけを上げる。

使い方:
    python deploy/geo_azure/export_clean_repo.py --dest C:/path/to/geo-intelligence
    （--dest は、まだ存在しないか、空のフォルダ。既存の内容は消さない）

取り出したあとに自動で検査する（1つでも見つかれば失敗扱い）:
    ・config.json / .env など秘密情報のファイル名
    ・秘密情報に見えるキー・トークン（JWT、sk-、Azureの接続文字列 等）
    ・50MB を超えるファイル（GitHub の上限は100MB）
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ルート直下で取り出すファイル（*.py は _ で始まるもの＝一時スクリプトを除く）
ROOT_FILES = ["requirements.txt", ".gitignore", "DEPLOY_GEO_AZURE.md", "config.json.example"]
# 取り出すフォルダ（再帰）。docs は実行時に使う media-intelligence だけ
INCLUDE_DIRS = ["config", "schemas", "resources", "sql", "tests", "eval_harness", "deploy/geo_azure", ".github",
                "docs/media-intelligence", "knowledge/geo_expert", "expert_review_cache", "eco-digest-spark"]
# どこにあっても除く
EXCLUDE_NAMES = {"__pycache__", ".pytest_cache", "node_modules", "dist", ".output", ".tanstack", ".wrangler", ".vinxi",
                 ".nitro", ".turbo", ".vite", "build", ".venv", ".git"}
EXCLUDE_GLOBS = ["*.pyc", "*.log", "*.png", "*.jpg", "*.zip", "*.csv", "_*.mjs", "_*.cjs", "_*.js", "_tmp*", "_verify*",
                 "_check*", "vite_*.log", "bun.lock", "bunfig.toml", "config.json", ".env", ".env.*", "*.pem", "*.key"]
KEEP_DESPITE_GLOB = {"config.json.example"}        # 例（値は空）は取り出す
MAX_FILE_BYTES = 50 * 1024 * 1024

SECRET_FILE_NAMES = {"config.json", ".env"}
SECRET_PATTERNS = [
    ("JWT/Supabaseキー", re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")),
    ("OpenAI系キー", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("Perplexityキー", re.compile(r"\bpplx-[A-Za-z0-9]{20,}")),
    ("Azure接続文字列", re.compile(r"(AccountKey|SharedAccessKey)=[A-Za-z0-9+/=]{20,}")),
    ("秘密鍵", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHubトークン", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("Azure OpenAIキー形式", re.compile(r"(api[_-]?key|apikey)[\"']?\s*[:=]\s*[\"'][A-Fa-f0-9]{32}[\"']", re.I)),
    # 半角の英数記号だけの値（日本語の説明文「ここに〜を入力」のような例示は、本物のパスワードではないので除く）
    ("パスワード代入", re.compile(r"(password|passwd)[\"']?\s*[:=]\s*[\"'][\x21-\x7e]{8,}[\"']", re.I)),
]
_TEXT_EXT = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".json", ".yaml", ".yml", ".md", ".sql", ".txt", ".toml", ".example",
             ".html", ".css", ".sh", ".cfg", ".ini"}


def _excluded(path: Path) -> bool:
    if any(part in EXCLUDE_NAMES for part in path.parts):
        return True
    name = path.name
    if name in KEEP_DESPITE_GLOB:
        return False
    return any(fnmatch.fnmatch(name, g) for g in EXCLUDE_GLOBS)


def collect() -> list[Path]:
    """取り出すファイルの一覧（ROOTからの相対パス）。"""
    files: list[Path] = []
    for p in sorted(ROOT.glob("*.py")):
        if not p.name.startswith("_"):
            files.append(p.relative_to(ROOT))
    for name in ROOT_FILES:
        if (ROOT / name).is_file():
            files.append(Path(name))
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(ROOT)
            if p.is_file() and not _excluded(rel):
                files.append(rel)
    return files


def scan(dest: Path) -> list[str]:
    files = [p.relative_to(dest) for p in sorted(dest.rglob("*"))
             if p.is_file() and ".git" not in p.relative_to(dest).parts]
    return scan_files(dest, files)


def scan_files(base: Path, rels: list[Path]) -> list[str]:
    """base 配下の rels（相対パス）を検査する。取り出し先のフォルダでも、元のプロジェクト（コピー前）でも使える。"""
    problems: list[str] = []
    for rel_path in sorted(rels):
        p = base / rel_path
        if not p.is_file():
            continue
        rel = Path(rel_path).as_posix()
        if p.name in SECRET_FILE_NAMES:
            problems.append(f"秘密情報のファイル名: {rel}")
        if p.stat().st_size > MAX_FILE_BYTES:
            problems.append(f"大きすぎるファイル({p.stat().st_size // 1024 // 1024}MB): {rel}")
        if p.suffix.lower() in _TEXT_EXT or p.name.endswith(".example"):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for label, pat in SECRET_PATTERNS:
                m = pat.search(text)
                if m:
                    problems.append(f"{label}らしき文字列: {rel}（先頭の{m.group(0)[:6]}…）")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True, help="取り出し先（存在しない、または空のフォルダ）")
    ap.add_argument("--list", action="store_true", help="取り出さず、対象の一覧だけ表示する")
    ap.add_argument("--sync", action="store_true", help="空でない取り出し先にも、対象ファイルを上書きコピーする（取り出し先のファイルは消さない）")
    args = ap.parse_args()
    files = collect()
    if args.list:
        for f in files:
            print(f.as_posix())
        print(f"{len(files)}ファイル")
        return
    dest = Path(args.dest).resolve()
    if dest == ROOT or ROOT in dest.parents:
        sys.exit("取り出し先は、このプロジェクトの外のフォルダにしてください。")
    if dest.exists() and any(dest.iterdir()) and not args.sync:
        sys.exit(f"取り出し先が空ではありません: {dest}（既存の内容は消しません。別のフォルダを指定するか、--sync を付けてください）")
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    total = sum((dest / f).stat().st_size for f in files)
    print(f"取り出し完了: {dest}  ({len(files)}ファイル / {total / 1024 / 1024:.1f}MB)")
    problems = scan(dest)
    if problems:
        print("\n【検査で問題が見つかりました】")
        for p in problems:
            print(" -", p)
        sys.exit(1)
    print("検査OK: 秘密情報らしきファイル・文字列、50MB超のファイルは見つかりませんでした。")


if __name__ == "__main__":
    main()
