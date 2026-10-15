"""Azure App Service（Python）へデプロイする成果物を組み立てる。

使い方（プロジェクトのルートで）:
    【1サーバー構成: 画面もAPIと一緒に配信】
      1. 画面を静的ビルド:  cd eco-digest-spark && GEO_STATIC_BUILD=1 npm run build
      2. python deploy/geo_azure/build_package.py            # → build/azure_package/ に出力
         python deploy/geo_azure/build_package.py --zip      # → build/azure_package.zip も作る
    【2サーバー構成: バック（API）だけ。画面は別のWeb App】
      python deploy/geo_azure/build_package.py --no-frontend

含めるもの（実行時に必要なもののみ）:
    ルート直下の *.py（api_server.py と geo_*.py 等）、requirements.txt、
    config/（設定・シード）、schemas/（JSON Schema）、resources/（Report Studioのプロンプト）、
    knowledge/geo_expert/（地政学専門家AIの知識ファイル）、expert_review_cache/（読み方UIの初期キャッシュ）、
    docs/media-intelligence/（メディアのドメイン対応表）、frontend/（画面の静的ファイル）

含めないもの（秘密情報・巨大・開発用）:
    config.json / .env（秘密情報。Azureでは「環境変数」で渡す）、tests/、uploads/、logs/、
    docs/ のその他（約1.3GB）、eval系、freeze/、sql/、eco-digest-spark/（ソースとnode_modules）、*.log、*.csv など
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build" / "azure_package"

# knowledge/geo_expert … 地政学専門家AIが実行時に読む知識ファイル（無いとExpert AIが動かない）
# expert_review_cache … 専門家レビュー（読み方UI）の生成済みキャッシュ（初期データ。無いと初回表示のたびにLLMで再生成する）
INCLUDE_DIRS = ["config", "schemas", "resources", "docs/media-intelligence", "knowledge/geo_expert", "expert_review_cache"]
INCLUDE_FILES = ["requirements.txt"]
FRONTEND_DIST = ROOT / "eco-digest-spark" / "dist" / "client"
FORBIDDEN_NAMES = {"config.json", ".env"}          # 成果物に入ってはいけない（秘密情報）
_SKIP_PARTS = {"__pycache__", ".pytest_cache"}


def _ignore(_dir, names):
    return [n for n in names if n in _SKIP_PARTS or n.endswith(".pyc") or n in FORBIDDEN_NAMES]


def build(out: Path = OUT, frontend_dist: Path = FRONTEND_DIST, with_frontend: bool = True) -> Path:
    if with_frontend and not (frontend_dist / "_shell.html").is_file():
        sys.exit(f"画面の静的ビルドが見つかりません: {frontend_dist}\n先に `cd eco-digest-spark && GEO_STATIC_BUILD=1 npm run build` を実行してください。")
    if out.exists():
        shutil.rmtree(out)           # 成果物用のディレクトリ（build/azure_package）だけを作り直す
    out.mkdir(parents=True)
    for p in sorted(ROOT.glob("*.py")):
        shutil.copy2(p, out / p.name)
    for name in INCLUDE_FILES:
        shutil.copy2(ROOT / name, out / name)
    for d in INCLUDE_DIRS:
        shutil.copytree(ROOT / d, out / d, ignore=_ignore)
    if with_frontend:
        shutil.copytree(frontend_dist, out / "frontend")
    leaked = [p for p in out.rglob("*") if p.name in FORBIDDEN_NAMES]
    if leaked:
        sys.exit(f"秘密情報になりうるファイルが成果物に入っています: {leaked}")
    return out


def make_zip(src: Path) -> Path:
    zpath = src.with_suffix(".zip")
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(src).as_posix())
    return zpath


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="store_true", help="zipも作る")
    ap.add_argument("--no-frontend", action="store_true", help="画面を含めない（2サーバー構成のバック用）")
    args = ap.parse_args()
    out = build(with_frontend=not args.no_frontend)
    files = [p for p in out.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    print(f"成果物: {out}  ({len(files)}ファイル / {size / 1024 / 1024:.1f}MB)")
    if args.zip:
        z = make_zip(out)
        print(f"zip: {z}  ({z.stat().st_size / 1024 / 1024:.1f}MB)")


if __name__ == "__main__":
    main()
