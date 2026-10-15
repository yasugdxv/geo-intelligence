"""Report Studio → Intelligence Analysis Platform: Embedding Benchmark環境（Phase 2専用）。

詳細設計: docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md（Section G）。

設計原則（Plan文書の9項目をそのままコードへ反映）:
1. Production Pipelineからは一切import/呼び出しされない。
2. Production DBスキーマへembedding列を追加しない（`geo_report_embedding_cache`のみ）。
3. モデル名・versionを必ず記録する（`EMBEDDING_MODEL_NAME`/`EMBEDDING_MODEL_VERSION`）。
4. `embedding_vector`はBenchmark artifact/cacheであり、`input_text_hash`で照合し
   raw textから常に再生成可能にする。
5-6. 将来Azure Embeddingsを導入した場合は、必ず同じBenchmark形式で再実行し比較する
   （このモジュールをそのまま使わず、別のembedding_model名で並行運用すること）。
7. local embeddingで学習したclassifierは`embedding_model`/`embedding_version`を
   メタデータとして必ず埋め込む（利用側=`geo_report_semantic_census_service.py`の責務）。
8. `geo_report_reference_examples`は読み取り専用として扱う。
9. pip install/download中に明らかな長時間ハングが発生した場合は中断してユーザーへ報告する。

`sentence-transformers`/`torch`はBenchmark専用のoptional dependency
（requirements-benchmark.txt）。Production側のAPIサーバー起動時に誤ってこのモジュールが
importされても依存エラーにならないよう、実際のimportは関数内（`get_embedding_model`）
まで遅延させる。
"""

from __future__ import annotations

import hashlib
import uuid

import numpy as np

from geo_external_signal_service import _select_all_paginated

EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_MODEL_VERSION = "sentence-transformers-3.3.1"  # ライブラリversion(HF revision固定なし)

_MODEL_SINGLETON = {"model": None}

_CLUSTER_ID_COLUMN = {"analysis": "analysis_cluster_id", "sentiment": "sentiment_cluster_id"}
_REPRESENTATIVES_TABLE = {
    "analysis": "geo_report_analysis_cluster_representatives",
    "sentiment": "geo_report_sentiment_cluster_representatives",
}


def _new_id() -> str:
    return str(uuid.uuid4())


def _chunked(items: list, size: int = 500):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def get_embedding_model():
    """sentence-transformersモデルをプロセス内で1度だけロードしてキャッシュする。
    遅延import——Production側でこのモジュールが誤ってimportされても、この関数を
    呼ばない限り`sentence_transformers`/`torch`への依存エラーは発生しない。

    2026-09-13実機確認: モデル本体は既にローカルcache済みだが、`transformers`が
    PEFT adapter設定の有無を確認するためhuggingface.coへオンライン照会し、社内
    プロキシのTLS中間証明書（self-signed）で`SSLCertVerificationError`となり失敗する
    ことを確認した（証明書検証を無効化して回避すべき問題ではなく、そもそも既に
    ローカルにある固定versionモデルへネットワークアクセスする必要が無いケース）。
    `HF_HUB_OFFLINE`を強制することで、この不要なオンライン照会自体を止める。"""
    if _MODEL_SINGLETON["model"] is None:
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from sentence_transformers import SentenceTransformer
        _MODEL_SINGLETON["model"] = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _MODEL_SINGLETON["model"]


def normalize_text(title: str | None, body: str | None) -> str:
    return f"{(title or '').strip()}\n{(body or '').strip()}".strip()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _encode_texts(texts: list) -> list:
    model = get_embedding_model()
    vectors = model.encode(texts, show_progress_bar=False)
    return [np.asarray(v, dtype=np.float32) for v in vectors]


# IN filter一括取得時、id/hash件数が多いとURL長がプロキシ/PostgREST側の上限を超えうる
# ため（実データで実測確認済み）、この件数ずつに分割してSELECTし、結果を結合する。
_ID_FILTER_CHUNK_SIZE = 150


def _select_by_values_chunked(client, table: str, base_params: dict, field: str, values: list) -> list:
    if not values:
        return []
    rows: list = []
    for chunk in _chunked(list(values), _ID_FILTER_CHUNK_SIZE):
        rows.extend(_select_all_paginated(client, table, {**base_params, field: "in.(" + ",".join(chunk) + ")"}))
    return rows


def _compute_and_cache_embeddings(client, run_id: str, entity_type: str, items: list) -> int:
    """items: [{"entity_id":..., "cluster_type":..., "cluster_id":..., "text":...}, ...]。
    既にcacheされているhashはスキップし、未計算分だけencodeする。戻り値: 新規計算件数。"""
    if not items:
        return 0
    hashes = [_text_hash(it["text"]) for it in items]
    existing = _select_by_values_chunked(client, "geo_report_embedding_cache", {
        "select": "input_text_hash", "run_id": f"eq.{run_id}", "entity_type": f"eq.{entity_type}",
        "embedding_model": f"eq.{EMBEDDING_MODEL_NAME}", "embedding_version": f"eq.{EMBEDDING_MODEL_VERSION}",
    }, "input_text_hash", sorted(set(hashes)))
    existing_hashes = {r["input_text_hash"] for r in existing}

    to_compute = [(it, h) for it, h in zip(items, hashes) if h not in existing_hashes]
    if not to_compute:
        return 0
    vectors = _encode_texts([it["text"] for it, _ in to_compute])
    rows = [{
        "id": _new_id(), "run_id": run_id, "entity_type": entity_type, "entity_id": it["entity_id"],
        "embedding_model": EMBEDDING_MODEL_NAME, "embedding_version": EMBEDDING_MODEL_VERSION,
        "input_text_hash": h, "embedding_vector": vec.tolist(), "dimension": int(vec.shape[0]),
    } for (it, h), vec in zip(to_compute, vectors)]
    for chunk in _chunked(rows):
        client.insert("geo_report_embedding_cache", chunk, prefer="return=minimal")
    return len(rows)


def compute_embeddings_for_representatives(client, run_id: str, cluster_type: str, cluster_ids: list) -> int:
    """representative単位（クラスタ単位ではない）でtitle+text_for_reviewを正規化結合した
    テキストをエンコードし、`geo_report_embedding_cache`(entity_type='representative')へ
    保存する。戻り値: 新規計算件数。"""
    if not cluster_ids:
        return 0
    table = _REPRESENTATIVES_TABLE[cluster_type]
    cluster_col = _CLUSTER_ID_COLUMN[cluster_type]
    reps = _select_by_values_chunked(client, table, {
        "select": f"id,title,text_for_review,{cluster_col}", "run_id": f"eq.{run_id}",
    }, cluster_col, cluster_ids)
    items = [{
        "entity_id": r["id"], "cluster_type": cluster_type, "cluster_id": r[cluster_col],
        "text": normalize_text(r.get("title"), r.get("text_for_review")),
    } for r in reps]
    return _compute_and_cache_embeddings(client, run_id, "representative", items)


def compute_embeddings_for_records(client, run_id: str, cluster_type: str, cluster_ids: list, *,
                                      raw_csv_paths: list) -> int:
    """Section F Representative Selection Benchmark専用: Benchmark対象クラスタ
    （サンプルのみ、全Run全recordsは対象にしない）に属する全recordをrecord単位で
    embeddingする。`geo_report_records`自体には本文列が無いため、
    `geo_report_benchmark_raw_text_service`経由でRunの元Babel Street CSVから
    documentText/documentNameを読み込む（Benchmark専用のローカルファイル依存、
    Production Pipelineはこれに依存しない）。本文が見つからないrecordは
    embedding対象から除外する。戻り値: 新規計算件数。"""
    if not cluster_ids:
        return 0
    cluster_col = _CLUSTER_ID_COLUMN[cluster_type]
    records = _select_by_values_chunked(client, "geo_report_records", {
        "select": f"record_id,{cluster_col}", "run_id": f"eq.{run_id}",
    }, cluster_col, cluster_ids)

    from geo_report_benchmark_raw_text_service import load_record_texts
    texts_by_record_id = load_record_texts(raw_csv_paths, {r["record_id"] for r in records})

    items = []
    for r in records:
        text_info = texts_by_record_id.get(r["record_id"])
        if not text_info:
            continue
        text = normalize_text(text_info.get("title"), text_info.get("text"))
        if not text:
            continue
        items.append({
            "entity_id": r["record_id"], "cluster_type": cluster_type, "cluster_id": r[cluster_col],
            "text": text,
        })
    return _compute_and_cache_embeddings(client, run_id, "record", items)


def get_embeddings(client, run_id: str, entity_type: str, entity_ids: list) -> dict:
    """{entity_id: np.ndarray}。既にcache済みのembeddingを一括取得する。"""
    if not entity_ids:
        return {}
    rows = _select_by_values_chunked(client, "geo_report_embedding_cache", {
        "select": "entity_id,embedding_vector", "run_id": f"eq.{run_id}", "entity_type": f"eq.{entity_type}",
        "embedding_model": f"eq.{EMBEDDING_MODEL_NAME}", "embedding_version": f"eq.{EMBEDDING_MODEL_VERSION}",
    }, "entity_id", entity_ids)
    return {r["entity_id"]: np.asarray(r["embedding_vector"], dtype=np.float32) for r in rows}


def compute_cluster_centroid(client, run_id: str, cluster_type: str, cluster_id: str):
    """`geo_report_analysis_cluster_representatives`/`geo_report_sentiment_cluster_
    representatives`をcluster_idでJoinしてrepresentative行id一覧を求め、その
    embeddingの平均を都度計算して返す（DBには保存しない、常にrepresentative
    embeddingから再導出できることを優先する）。埋め込みが1件も無ければNoneを返す。"""
    table = _REPRESENTATIVES_TABLE[cluster_type]
    cluster_col = _CLUSTER_ID_COLUMN[cluster_type]
    reps = client.select(table, {"select": "id", "run_id": f"eq.{run_id}", cluster_col: f"eq.{cluster_id}"})
    rep_ids = [r["id"] for r in reps]
    embeddings = get_embeddings(client, run_id, "representative", rep_ids)
    if not embeddings:
        return None
    return np.mean(list(embeddings.values()), axis=0)
