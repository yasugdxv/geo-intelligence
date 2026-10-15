"""地政学インテリジェンス基盤: 地政学専門家AIのKnowledge読み込み。

knowledge/geo_expert/ 配下のMarkdownファイルをそのままKnowledgeとして読み込む。
既存の sustainability_expert_common.load_expert_base() と同じ発想（ファイル群を
バージョン管理しつつ読み込む）だが、地政学ドメイン用に完全に独立した実装とする
（既存ファイルはimportも改変もしない）。

Prompt/Knowledge/Model/Source Packをすべて記録し再現可能にする、という指示書の
絶対原則10のため、versionは全Knowledgeファイル内容のハッシュとする
（ファイル内容が変わればversionも変わる）。
"""

import hashlib
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge" / "geo_expert"

# 01のSystem Policyのみ本プロジェクトで新規作成、03〜10は専門家GPT資産
# （2026年7月31日Session1＋2026年8月25日Session2の専門家インタビューを基にした
# v1.2思考パターン集、Key Stakeholder-first分析順序を追加）を本アプリ向けに
# 統合・調整（01/04のWorld Model前提を書き換え）した上で使用する
KNOWLEDGE_FILES = [
    "01_SYSTEM_POLICY.md",
    "03_KNOWLEDGE_THINKING_OS.md",
    "04_KNOWLEDGE_STAKEHOLDER_FORCE_MODEL.md",
    "05_KNOWLEDGE_HYPOTHESIS_EVIDENCE_PROTOCOL.md",
    "06_KNOWLEDGE_REVIEW_OS.md",
    "07_KNOWLEDGE_OUTPUT_TEMPLATES.md",
    "08_KNOWLEDGE_CASE_LIBRARY.md",
    "09_KNOWLEDGE_BUSINESS_IMPACT_MODEL.md",
    "10_KNOWLEDGE_ANALYST_DEVELOPMENT.md",
]

_cache = None


def load_geo_expert_knowledge() -> dict:
    """{"files": {filename: content}, "version": str} を返す（プロセス内キャッシュ）。
    versionは全ファイル内容のsha256先頭12桁"""
    global _cache
    if _cache is not None:
        return _cache
    files = {}
    for name in KNOWLEDGE_FILES:
        path = KNOWLEDGE_DIR / name
        files[name] = path.read_text(encoding="utf-8") if path.exists() else ""
    combined = "".join(files.values())
    version = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:12]
    _cache = {"files": files, "version": version}
    return _cache


def build_knowledge_block(include_files: list | None = None) -> str:
    """システムプロンプトへ埋め込むためのKnowledge本文（ファイル名見出し付き結合）。

    include_files: 含めるファイル名のリスト（未指定時は全ファイル、既存呼び出し元
    （geo_expert_engine.run_expert_request、一括方式）と完全に後方互換）。段階実行方式
    （run_expert_request_staged）では、Stage/モジュールごとに実際に必要なファイルだけを
    渡すことでKnowledge投入量を削減する（geo_expert_engine.KNOWLEDGE_FILES_FOR_STAGE参照）。"""
    knowledge = load_geo_expert_knowledge()
    parts = []
    for name, content in knowledge["files"].items():
        if include_files is not None and name not in include_files:
            continue
        if content:
            parts.append(f"### {name}\n{content}")
    return "\n\n".join(parts)
