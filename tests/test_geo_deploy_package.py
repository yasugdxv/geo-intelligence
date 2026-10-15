"""デプロイ成果物・クリーン取り出しに、実行時に必要なディレクトリが入っているかのテスト。

過去に、専門家AIの知識ファイル（knowledge/geo_expert）が成果物に入っておらず、Azureで専門家AIが動かなくなる
取りこぼしがあった。コードが __file__ 基準で読むディレクトリが増えたときに、ここで気づけるようにする。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# コードが __file__ 基準で読むが、成果物に含めなくてよいもの（環境変数で保存先を変える／生成物／秘密情報／別管理）
NOT_PACKAGED = {"config.json", "uploads", "logs", "frontend", "eco-digest-spark"}


def _runtime_roots() -> set:
    names = set()
    for p in ROOT.glob("geo_*.py"):
        src = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r'(?:Path\(__file__\)(?:\.resolve\(\))?\.parent|BASE_DIR|_BASE)\s*/\s*"([^"/]+)"', src):
            names.add(m.group(1))
        for m in re.finditer(r'os\.path\.join\(os\.path\.dirname\(os\.path\.abspath\(__file__\)\),\s*"([^"]+)"', src):
            names.add(m.group(1))
    return {n for n in names if (ROOT / n).exists() and not n.endswith(".py")}


def test_package_and_export_include_every_runtime_directory():
    import sys
    sys.path.insert(0, str(ROOT / "deploy" / "geo_azure"))
    import build_package
    import export_clean_repo
    packaged = {d.split("/")[0] for d in build_package.INCLUDE_DIRS}
    exported = {d.split("/")[0] for d in export_clean_repo.INCLUDE_DIRS}
    missing_in_package = sorted(n for n in _runtime_roots() - NOT_PACKAGED if n not in packaged)
    assert missing_in_package == [], f"デプロイ成果物に含まれていない実行時ディレクトリ: {missing_in_package}"
    missing_in_export = sorted(n for n in _runtime_roots() - NOT_PACKAGED if n not in exported)
    assert missing_in_export == [], f"クリーン取り出しに含まれていない実行時ディレクトリ: {missing_in_export}"


def test_expert_ai_knowledge_files_are_packaged():
    import sys
    sys.path.insert(0, str(ROOT / "deploy" / "geo_azure"))
    import build_package
    assert "knowledge/geo_expert" in build_package.INCLUDE_DIRS
    assert (ROOT / "knowledge" / "geo_expert" / "01_SYSTEM_POLICY.md").is_file()
