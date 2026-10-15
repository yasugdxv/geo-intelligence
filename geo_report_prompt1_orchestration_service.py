"""Report Studio「Design」領域: Prompt1 Application AI実行オーケストレーション（Phase D）。

`geo_report_prompt1_classification_service`（LLM分類）と`geo_report_current_pipeline_
persistence_service`（DB永続化パターン）を、`geo_report_executions`
（stage='prompt1'）＋`geo_report_execution_batches`によるChunk実行・チェックポイント・
リトライへ結びつける。

実データ規模（Analysis Cluster/Sentiment Clusterとも万単位）を素朴に1リクエストで
逐次処理しないよう、バッチ単位（既定20クラスタ/バッチ）で計画し、`run_next_pending_batch`
を繰り返し呼ぶ設計にする（本プロジェクトにバックグラウンドジョブ基盤が無いため、
既存`geo_report_studio_api.py`の「UI側から少数ずつ繰り返し呼ぶ」パターンを踏襲）。
失敗したバッチだけ`retry_batch`で再試行でき、Run全体をやり直す必要はない。

Idempotency: 分類結果保存はupsert（execution_id×cluster_idで既存行があれば更新、
無ければ挿入）。バッチをリトライしても重複行は増えない。
"""

from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import geo_llm_common
import geo_report_execution_canonical_service as canonical
import geo_report_prompt1_classification_service as classify
import geo_report_project_assets_service as assets
import geo_report_run_cancellation_service as cancellation
from geo_external_signal_service import _select_all_paginated

CHUNK_SIZE = 500

# IN filter一括取得時、id件数が多いとURL長がプロキシ/PostgREST側の上限を超えうるため、
# id listをこの件数ずつに分割してSELECTし、結果を結合する（2026-09-12、Targeted Stance
# 候補選定でSC全件規模のid listを扱うようになったため導入）。
_ID_FILTER_CHUNK_SIZE = 200


def _select_by_ids_chunked(client, table: str, base_params: dict, id_field: str, ids: list) -> list:
    if not ids:
        return []
    rows: list = []
    for chunk in _chunked(ids, _ID_FILTER_CHUNK_SIZE):
        rows.extend(_select_all_paginated(client, table, {
            **base_params, id_field: "in.(" + ",".join(chunk) + ")",
        }))
    return rows

# ---------------------------------------------------------------------------
# Batching最適化（2026-09-11）: 「1クラスタ=1回のLLM呼び出し」を廃止し、複数クラスタを
# 1回の呼び出しでまとめて分類する（ChatGPT一括処理相当の効率を狙う。詳細は
# kind-napping-hare.md「Phase G: Prompt1 Batching最適化」参照）。
# 全て設定値化（コード定数のハードコードにしない、環境変数で調整可能）。
# ---------------------------------------------------------------------------

# 1バッチに含める最大クラスタ数。ただしこの件数固定ではなく、Token-aware batching
# （後述_pack_token_aware_batches）で「件数上限 または 推定トークン数上限のどちらか
# 先に到達した時点でバッチを閉じる」方式にする（代表投稿の長さがクラスタごとに大きく
# 異なるため、件数だけでバッチサイズを決めると長い代表投稿を持つバッチだけ極端に
# トークン数が膨らみ、completion token上限に達しうるため）。
MAX_CLUSTERS_PER_BATCH = int(os.environ.get("GEO_REPORT_MAX_CLUSTERS_PER_BATCH", "20"))
DEFAULT_BATCH_SIZE = MAX_CLUSTERS_PER_BATCH  # 既存呼び出し元・テストとの後方互換名

# 1バッチの推定input token数上限（4文字≒1トークンの粗い見積り、代表投稿のtitle+
# text_for_reviewの合計文字数から算出）。この上限に達した時点でMAX_CLUSTERS_PER_BATCH
# 未満でもバッチを閉じる。
MAX_BATCH_INPUT_TOKENS = int(os.environ.get("GEO_REPORT_MAX_BATCH_INPUT_TOKENS", "12000"))
_CHARS_PER_TOKEN_ESTIMATE = 4

# 同時に処理するバッチ数（バッチ=1回のLLM呼び出しになったため、並列化はバッチ単位で行う。
# 以前の「バッチ内クラスタ単位の並列化」は廃止）。429はgeo_llm_common.call_llm_structured
# が指数バックオフで自動リトライするため、多少の超過は自動回復する。
# 2026-09-12実測（batch=20、実データ）: concurrency 3→20で持続throughputが0.85→3.7
# cluster/秒(4.3倍)、429発生も持続負荷で3/5/8/10/15/20では僅少〜ゼロ、30で急増(32/35)
# という明確な悪化点を確認した。P0としてまずconcurrencyのみを3→15へ変更し
# （batch sizeは20のまま据え置き、単一変数での効果測定のため）、Full Run実測後に
# 15→20への追加引き上げを検討する。
MAX_CONCURRENT_BATCHES = int(os.environ.get("GEO_REPORT_MAX_CONCURRENT_BATCHES", "15"))

# 1バッチ呼び出しでmissing/duplicate/unknown/invalidが発生した場合、その分だけ
# アイテム単位で追加リトライする最大ラウンド数（バッチ全体の再送はしない）。
ITEM_RETRY_ROUNDS = int(os.environ.get("GEO_REPORT_ITEM_RETRY_ROUNDS", "2"))


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---------------------------------------------------------------------------
# Execution / Batch ライフサイクル
# ---------------------------------------------------------------------------

def create_prompt1_execution(client, run_id: str, *, execution_method: str = "application_ai") -> str:
    """geo_report_executions行を作る（stage='prompt1'）。input_preprocess_execution_idは
    Runのcanonical_preprocess_execution_idから引く（Preprocessが完了済みであること）。"""
    run_rows = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run_id}"})
    if not run_rows:
        raise ValueError(f"run_id={run_id} が見つかりません。")
    run = run_rows[0]
    if not run.get("canonical_preprocess_execution_id"):
        raise ValueError(f"run_id={run_id} はPreprocessが未完了です（canonical_preprocess_execution_id無し）。")

    execution_id = _new_id()
    client.insert("geo_report_executions", [{
        "id": execution_id, "run_id": run_id, "stage": "prompt1", "execution_method": execution_method,
        "status": "running", "input_preprocess_execution_id": run["canonical_preprocess_execution_id"],
        "started_at": _now_iso(), "progress": {"batches_completed": 0, "batches_total": 0},
    }])
    return execution_id


def _update_execution_progress(client, execution_id: str) -> None:
    # 1 executionの全stage合計バッチ数は実データ規模（AC/SC合計37,182クラスタ相当）で
    # 1,000件のPostgREST既定行数上限を超えうるため、_select_all_paginatedで全件取得する
    # （2026-09-12、cluster fetchの同種バグ発覚を受けて確認・修正）。
    batches = _select_all_paginated(client, "geo_report_execution_batches", {
        "select": "status", "execution_id": f"eq.{execution_id}",
    })
    total = len(batches)
    completed = len([b for b in batches if b["status"] in ("completed", "skipped")])
    failed = len([b for b in batches if b["status"] == "failed"])
    client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
        "progress": {"batches_completed": completed, "batches_failed": failed, "batches_total": total},
    })


def complete_prompt1_execution_if_done(client, run_id: str, execution_id: str) -> bool:
    """全バッチがcompleted/skippedであればExecutionをcompletedにし、
    Runのcanonical_prompt1_execution_idを更新する。1件でもfailed/pending/runningが
    残っていれば何もしない（呼び出し元がリトライを促す）。戻り値: 完了させたかどうか。"""
    batches = _select_all_paginated(client, "geo_report_execution_batches", {
        "select": "status", "execution_id": f"eq.{execution_id}",
    })
    if not batches or any(b["status"] in ("pending", "running") for b in batches):
        return False
    if any(b["status"] == "failed" for b in batches):
        return False
    client.update("geo_report_executions", {"id": f"eq.{execution_id}"}, {
        "status": "completed", "completed_at": _now_iso(),
    })
    canonical.set_canonical_execution(client, run_id, "prompt1", execution_id)
    return True


def _estimate_representative_weight_by_cluster(client, run_id: str, table: str, id_column: str) -> dict:
    """id_column(analysis_cluster_id/sentiment_cluster_id)ごとに、代表投稿のtitle+
    text_for_reviewの合計文字数を粗いトークン数見積り(4文字≒1トークン)に変換した重みを
    返す。Token-aware batchingの重み付けに使う（代表投稿の長さがクラスタごとに大きく
    異なるため、件数だけでバッチサイズを決めない。実データ規模では代表投稿テーブルが
    万単位になるため_select_all_paginatedでページングして取得する）。"""
    rows = _select_all_paginated(client, table, {
        "select": f"{id_column},title,text_for_review", "run_id": f"eq.{run_id}",
    })
    chars_by_id: dict = {}
    for row in rows:
        cid = row.get(id_column)
        if cid is None:
            continue
        length = len(row.get("title") or "") + len(row.get("text_for_review") or "")
        chars_by_id[cid] = chars_by_id.get(cid, 0) + length
    return {cid: max(1, chars // _CHARS_PER_TOKEN_ESTIMATE) for cid, chars in chars_by_id.items()}


def _pack_token_aware_batches(cluster_ids: list, weight_by_id: dict, *,
                                max_items: int, max_tokens: int) -> list:
    """cluster_idsを「件数がmax_itemsに到達」または「推定input token数がmax_tokensに
    到達」のいずれか早い方でバッチを閉じながら詰めていく（Token-aware batching）。
    weight_by_idに無いid（代表投稿が無い等）は重み0として扱う。"""
    batches: list = []
    current: list = []
    current_tokens = 0
    for cid in cluster_ids:
        weight = weight_by_id.get(cid, 0)
        if current and (len(current) >= max_items or current_tokens + weight > max_tokens):
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(cid)
        current_tokens += weight
    if current:
        batches.append(current)
    return batches


def plan_batches(client, run_id: str, execution_id: str, stage: str, cluster_ids: list, *,
                  batch_size: int = DEFAULT_BATCH_SIZE, weight_by_id: dict = None,
                  max_tokens: int = MAX_BATCH_INPUT_TOKENS) -> list:
    """cluster_idsをToken-aware batching（batch_size件 or 推定トークン数上限のどちらか
    先に到達した時点でバッチを閉じる）で分割し、geo_report_execution_batches行を作る
    （status='pending'）。weight_by_idを渡さない場合は重み0扱いとなり、実質的に
    batch_size件ずつの固定分割になる（既存呼び出し元・テストとの後方互換）。
    戻り値: 作成したbatch行のリスト。"""
    batches = _pack_token_aware_batches(
        cluster_ids, weight_by_id or {}, max_items=batch_size, max_tokens=max_tokens)
    payload = [{
        "id": _new_id(), "run_id": run_id, "execution_id": execution_id, "stage": stage, "batch_no": i + 1,
        "item_ids": batch, "status": "pending",
    } for i, batch in enumerate(batches)]
    for chunk in _chunked(payload, CHUNK_SIZE):
        client.insert("geo_report_execution_batches", chunk)
    _update_execution_progress(client, execution_id)
    return payload


def plan_analysis_cluster_batches(client, run_id: str, execution_id: str, *,
                                    batch_size: int = DEFAULT_BATCH_SIZE) -> list:
    # PostgRESTのdb-max-rows既定値(1000件)により、明示ページングなしのselect()は
    # 大規模Runで黙って先頭1000件に切り詰められる（2026-09-12実データで発覚、
    # AC 1,477件中1,000件・SC 1,773件中1,000件しか計画されていなかった）。
    # _select_all_paginatedで全件取得する。
    rows = _select_all_paginated(client, "geo_report_analysis_clusters",
                                   {"select": "analysis_cluster_id", "run_id": f"eq.{run_id}"})
    cluster_ids = [r["analysis_cluster_id"] for r in rows]
    weight_by_id = _estimate_representative_weight_by_cluster(
        client, run_id, "geo_report_analysis_cluster_representatives", "analysis_cluster_id")
    return plan_batches(client, run_id, execution_id, "prompt1_analysis_cluster", cluster_ids,
                          batch_size=batch_size, weight_by_id=weight_by_id)


def plan_sentiment_cluster_batches(client, run_id: str, execution_id: str, *,
                                     batch_size: int = DEFAULT_BATCH_SIZE) -> list:
    rows = _select_all_paginated(client, "geo_report_sentiment_clusters",
                                   {"select": "sentiment_cluster_id", "run_id": f"eq.{run_id}"})
    cluster_ids = [r["sentiment_cluster_id"] for r in rows]
    weight_by_id = _estimate_representative_weight_by_cluster(
        client, run_id, "geo_report_sentiment_cluster_representatives", "sentiment_cluster_id")
    return plan_batches(client, run_id, execution_id, "prompt1_sentiment_cluster", cluster_ids,
                          batch_size=batch_size, weight_by_id=weight_by_id)


def _load_required_context_by_narrative(client, run_id: str) -> dict:
    """Narrative Masterのrequired_context列（候補者名・alias等、Prompt1 §4.5.2の
    target候補源1）を narrative_id（sub_narrative_id優先）ごとの用語リストへ変換する。"""
    run_rows = client.select("geo_report_collection_runs", {
        "select": "narrative_master_asset_id", "run_id": f"eq.{run_id}"})
    asset_id = run_rows[0].get("narrative_master_asset_id") if run_rows else None
    if not asset_id:
        return {}
    asset = assets.get_asset_by_id(client, asset_id)
    if not asset:
        return {}
    rows = (asset.get("content") or {}).get("rows") or []
    result: dict = {}
    for r in rows:
        nid = r.get("sub_narrative_id") or r.get("narrative_id")
        required_context = (r.get("required_context") or "").strip()
        if nid and required_context:
            result[nid] = [t.strip() for t in required_context.split(";") if t.strip()]
    return result


def _has_explicit_target(representatives: list, terms: list) -> bool:
    """Prompt1 §4.5.2: 「単なる固有名詞出現だけではtargeted stanceを付けない」——
    Narrative Masterのrequired_contextにあらかじめ定義された候補者名/aliasが、
    代表投稿のmatched_terms（Shellの高Recall抽出結果）または本文に実際に現れる場合のみ
    「明示targetあり」と機械的に判定する（本文の自由なentity推定は行わない）。"""
    if not terms:
        return False
    terms_lower = [t.lower() for t in terms]
    for rep in representatives:
        matched = (rep.get("matched_terms") or "").lower()
        text = (rep.get("text_for_review") or "").lower()
        if any(t in matched or t in text for t in terms_lower):
            return True
    return False


def plan_targeted_stance_batches(client, run_id: str, execution_id: str, *,
                                   batch_size: int = DEFAULT_BATCH_SIZE) -> list:
    """Precision優先（2026-09-12改訂、Prompt1 §4.5.8のCandidate Favorability算定条件と
    整合させる）。全Public Voice候補ではなく、同一executionのSC final classification
    （§4 Sentiment Cluster Classification）で
      - theme_relevance = relevant
      - source_class = Public Voice
      - apply_to_cluster = true
    と判定され、かつNarrative Masterのrequired_context用語が代表投稿から検出できる
    （§4.5.2の明示target候補）クラスタのみを対象にする。SC stageが完了済みである
    ことが前提（呼び出し順序: AC→SC完了→本関数→TS）。"""
    classifications = _select_all_paginated(client, "geo_report_sentiment_cluster_classifications", {
        "select": "sentiment_cluster_id,theme_relevance,source_class,apply_to_cluster",
        "execution_id": f"eq.{execution_id}",
    })
    final_public_voice_ids = [
        c["sentiment_cluster_id"] for c in classifications
        if c.get("theme_relevance") == "relevant" and c.get("source_class") == "Public Voice"
        and c.get("apply_to_cluster") is True
    ]
    if not final_public_voice_ids:
        return plan_batches(client, run_id, execution_id, "prompt1_targeted_stance", [], batch_size=batch_size)

    fact_rows = _select_by_ids_chunked(
        client, "geo_report_sentiment_clusters",
        {"select": "sentiment_cluster_id,narrative_id", "run_id": f"eq.{run_id}"},
        "sentiment_cluster_id", final_public_voice_ids)
    narrative_by_cluster = {r["sentiment_cluster_id"]: r.get("narrative_id") for r in fact_rows}
    required_context_by_narrative = _load_required_context_by_narrative(client, run_id)

    reps_rows = _select_by_ids_chunked(
        client, "geo_report_sentiment_cluster_representatives",
        {"select": "sentiment_cluster_id,matched_terms,text_for_review", "run_id": f"eq.{run_id}"},
        "sentiment_cluster_id", final_public_voice_ids)
    reps_by_cluster: dict = {}
    for r in reps_rows:
        reps_by_cluster.setdefault(r["sentiment_cluster_id"], []).append(r)

    cluster_ids = [
        cid for cid in final_public_voice_ids
        if _has_explicit_target(reps_by_cluster.get(cid, []),
                                  required_context_by_narrative.get(narrative_by_cluster.get(cid)))
    ]
    weight_by_id = _estimate_representative_weight_by_cluster(
        client, run_id, "geo_report_sentiment_cluster_representatives", "sentiment_cluster_id")
    return plan_batches(client, run_id, execution_id, "prompt1_targeted_stance", cluster_ids,
                          batch_size=batch_size, weight_by_id=weight_by_id)


# ---------------------------------------------------------------------------
# DBの行 → classify_* が期待するdict形状への変換
# ---------------------------------------------------------------------------

def _analysis_cluster_to_row(db_row: dict) -> dict:
    return {**db_row, "cluster_id": db_row["analysis_cluster_id"]}


def _sentiment_cluster_to_row(db_row: dict) -> dict:
    return {**db_row, "cluster_id": db_row["sentiment_cluster_id"]}


def _load_analysis_cluster_and_representatives(client, run_id: str, cluster_id: str) -> tuple:
    cluster_rows = client.select("geo_report_analysis_clusters", {
        "select": "*", "run_id": f"eq.{run_id}", "analysis_cluster_id": f"eq.{cluster_id}",
    })
    reps = client.select("geo_report_analysis_cluster_representatives", {
        "select": "*", "run_id": f"eq.{run_id}", "analysis_cluster_id": f"eq.{cluster_id}",
        "order": "representative_no.asc",
    })
    return (_analysis_cluster_to_row(cluster_rows[0]) if cluster_rows else None), reps


def _load_sentiment_cluster_and_representatives(client, run_id: str, cluster_id: str) -> tuple:
    cluster_rows = client.select("geo_report_sentiment_clusters", {
        "select": "*", "run_id": f"eq.{run_id}", "sentiment_cluster_id": f"eq.{cluster_id}",
    })
    reps = client.select("geo_report_sentiment_cluster_representatives", {
        "select": "*", "run_id": f"eq.{run_id}", "sentiment_cluster_id": f"eq.{cluster_id}",
        "order": "representative_no.asc",
    })
    return (_sentiment_cluster_to_row(cluster_rows[0]) if cluster_rows else None), reps


def _load_analysis_clusters_and_representatives_batch(client, run_id: str, cluster_ids: list) -> dict:
    """複数cluster_idをまとめてIN取得する（2026-09-12性能改善）。従来は
    _classify_batch_with_retryがcluster毎に2 SELECTをforループしており、
    batch_size=20なら1ラウンドあたり40回のPostgREST往復が発生していた。
    IN filterでcluster本体・代表投稿とも2回のSELECTにまとめる。
    戻り値: {cluster_id: (cluster_row, representatives)}（見つからないIDはキー自体が無い）。"""
    if not cluster_ids:
        return {}
    ids_filter = "in.(" + ",".join(cluster_ids) + ")"
    cluster_rows = client.select("geo_report_analysis_clusters", {
        "select": "*", "run_id": f"eq.{run_id}", "analysis_cluster_id": ids_filter,
    })
    reps_rows = client.select("geo_report_analysis_cluster_representatives", {
        "select": "*", "run_id": f"eq.{run_id}", "analysis_cluster_id": ids_filter,
        "order": "representative_no.asc",
    })
    reps_by_cluster: dict = {}
    for r in reps_rows:
        reps_by_cluster.setdefault(r["analysis_cluster_id"], []).append(r)
    return {
        row["analysis_cluster_id"]: (_analysis_cluster_to_row(row), reps_by_cluster.get(row["analysis_cluster_id"], []))
        for row in cluster_rows
    }


def _load_sentiment_clusters_and_representatives_batch(client, run_id: str, cluster_ids: list) -> dict:
    """_load_analysis_clusters_and_representatives_batchのSentiment Cluster版
    （Targeted Stanceも同じSentiment Clusterの器を使うため共用する）。"""
    if not cluster_ids:
        return {}
    ids_filter = "in.(" + ",".join(cluster_ids) + ")"
    cluster_rows = client.select("geo_report_sentiment_clusters", {
        "select": "*", "run_id": f"eq.{run_id}", "sentiment_cluster_id": ids_filter,
    })
    reps_rows = client.select("geo_report_sentiment_cluster_representatives", {
        "select": "*", "run_id": f"eq.{run_id}", "sentiment_cluster_id": ids_filter,
        "order": "representative_no.asc",
    })
    reps_by_cluster: dict = {}
    for r in reps_rows:
        reps_by_cluster.setdefault(r["sentiment_cluster_id"], []).append(r)
    return {
        row["sentiment_cluster_id"]: (_sentiment_cluster_to_row(row), reps_by_cluster.get(row["sentiment_cluster_id"], []))
        for row in cluster_rows
    }


# ---------------------------------------------------------------------------
# 分類結果の永続化（Idempotent upsert）
# ---------------------------------------------------------------------------

def _upsert(client, table: str, match: dict, payload: dict) -> None:
    existing = client.select(table, {"select": "id", **{k: f"eq.{v}" for k, v in match.items()}})
    if existing:
        client.update(table, {"id": f"eq.{existing[0]['id']}"}, payload)
    else:
        client.insert(table, [{"id": _new_id(), **match, **payload}])


def save_analysis_cluster_classification(client, execution_id: str, run_id: str, cluster_id: str,
                                           data: dict) -> None:
    _upsert(client, "geo_report_analysis_cluster_classifications",
            {"execution_id": execution_id, "cluster_id": cluster_id},
            {"run_id": run_id, **data})


def save_sentiment_cluster_classification(client, execution_id: str, run_id: str, cluster_id: str,
                                            data: dict) -> None:
    payload = dict(data)
    payload["exception_pattern"] = classify.exception_pattern_to_text(payload.get("exception_pattern", []))
    _upsert(client, "geo_report_sentiment_cluster_classifications",
            {"execution_id": execution_id, "sentiment_cluster_id": cluster_id},
            {"run_id": run_id, **payload})


def save_targeted_stance(client, execution_id: str, run_id: str, cluster_id: str, targets: list) -> None:
    """このクラスタの既存Targeted Stance行（同一execution）を全削除してから再挿入する
    （target数が実行のたびに変わり得るため、単純なfield upsertでは対応できない。
    delete-then-insertはこのクラスタ・このexecutionへ限定される安全な範囲）。

    2026-09-12実機確認: LLMが同一cluster内で同じ(target_type, target_key相当)の
    targetを複数回返すことがあり、DBのunique(execution_id, sentiment_cluster_id,
    target_type, target_key)制約に違反して409 Conflictでinsert全体が失敗していた
    （しかもこの例外が _process_batch で捕捉されずPipeline全体をクラッシュさせていた
    ——別途_process_batch側も修正）。ここではDBのtarget_key生成列
    （coalesce(target_normalized_id, target_name)）と同じロジックで事前に重複排除する
    （後勝ちではなく先勝ち＝最初に出現したtargetを残す）。"""
    client.delete("geo_report_targeted_stance", {
        "execution_id": f"eq.{execution_id}", "sentiment_cluster_id": f"eq.{cluster_id}",
    })
    if not targets:
        return
    seen_keys = set()
    deduped = []
    for t in targets:
        target_key = t.get("target_normalized_id") or t.get("target_name")
        dedup_key = (t["target_type"], target_key)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        deduped.append(t)
    payload = [{
        "id": _new_id(), "execution_id": execution_id, "run_id": run_id, "sentiment_cluster_id": cluster_id,
        "signal_id": t.get("signal_id"), "target_type": t["target_type"], "target_name": t["target_name"],
        "target_normalized_id": t.get("target_normalized_id"), "stance_polarity": t["stance_polarity"],
        "stance_strength": t["stance_strength"], "stance_confidence": t["stance_confidence"],
        "stance_evidence": t["stance_evidence"], "stance_basis": t["stance_basis"],
        "stance_cluster_consistency": t["stance_cluster_consistency"],
        "apply_stance_to_cluster": t["apply_stance_to_cluster"],
        "stance_exception_pattern": t.get("stance_exception_pattern", ""), "stance_reason": t["stance_reason"],
    } for t in deduped]
    client.insert("geo_report_targeted_stance", payload)


# ---------------------------------------------------------------------------
# バッチ実行
# ---------------------------------------------------------------------------

def _mark_batch(client, batch_id: str, status: str, *, error_message: str = None,
                 model_used: str = None, escalated: bool = None) -> None:
    patch = {"status": status}
    if status == "running":
        patch["started_at"] = _now_iso()
    if status in ("completed", "failed"):
        patch["completed_at"] = _now_iso()
    if error_message is not None:
        patch["error_message"] = error_message
    if model_used is not None:
        patch["model_used"] = model_used
    if escalated is not None:
        patch["escalated"] = escalated
    client.update("geo_report_execution_batches", {"id": f"eq.{batch_id}"}, patch)


def _get_next_pending_batch(client, execution_id: str, stage: str) -> dict:
    rows = client.select("geo_report_execution_batches", {
        "select": "*", "execution_id": f"eq.{execution_id}", "stage": f"eq.{stage}",
        "status": "eq.pending", "order": "batch_no.asc", "limit": "1",
    })
    return rows[0] if rows else None


def run_next_pending_batch(client, azure_client, model: str, run_id: str, execution_id: str, stage: str) -> dict:
    """pendingバッチを1件処理する。バッチが無ければ{"processed": False}を返す
    （呼び出し元がこれをループ終了条件にする）。"""
    batch = _get_next_pending_batch(client, execution_id, stage)
    if not batch:
        return {"processed": False}
    return _process_batch(client, azure_client, model, run_id, execution_id, batch)


def retry_batch(client, azure_client, model: str, run_id: str, execution_id: str, batch_id: str) -> dict:
    """failedなバッチだけをpendingへ戻して再実行する（Run全体をやり直さない）。"""
    rows = client.select("geo_report_execution_batches", {"select": "*", "id": f"eq.{batch_id}"})
    if not rows:
        raise ValueError(f"batch_id={batch_id} が見つかりません。")
    batch = rows[0]
    client.update("geo_report_execution_batches", {"id": f"eq.{batch_id}"}, {
        "retry_count": batch.get("retry_count", 0) + 1,
    })
    return _process_batch(client, azure_client, model, run_id, execution_id, batch)


def _analysis_cluster_build_item(cluster_row: dict, representatives: list) -> dict:
    return {"cluster_row": cluster_row, "representatives": representatives}


def _sentiment_cluster_build_item(cluster_row: dict, representatives: list) -> dict:
    return {"cluster_row": cluster_row, "representatives": representatives}


def _save_analysis_cluster_result(client, execution_id, run_id, cluster_id, item_body):
    save_analysis_cluster_classification(client, execution_id, run_id, cluster_id, item_body)


def _save_sentiment_cluster_result(client, execution_id, run_id, cluster_id, item_body):
    save_sentiment_cluster_classification(client, execution_id, run_id, cluster_id, item_body)


def _save_targeted_stance_result(client, execution_id, run_id, cluster_id, item_body):
    save_targeted_stance(client, execution_id, run_id, cluster_id, item_body.get("targets", []))


def _content_filter_placeholder_analysis_cluster(reason: str) -> dict:
    """Azureのcontent_filterで拒否されたAnalysis Clusterへの、LLM判断ではないシステム
    除外プレースホルダー（2026-09-12）。Prompt1 Output契約の列は全て埋めるが、
    noise_reason/notes_for_prompt2で「LLM判断ではなくシステムにより除外」であることを
    明示する。"""
    return {
        "final_topic_label": "(Azure content filterにより判定不能)", "source_assessment": "Unknown",
        "tone_gradient": "N/A", "voice_direction": "N/A", "report_use_status": "Exclude",
        "continuity_relevance": "No continuity relevance", "matched_previous_topic_id": None,
        "change_signal_summary": "", "noise_flag": True,
        "noise_reason": f"Azure content filterにより本文取得不可（システム除外）: {reason}",
        "voice_summary": "", "notable_phrases": "", "representative_quality": "Low",
        "notes_for_prompt2": "LLM判断ではなくシステムにより除外（content_filter）。手動レビュー推奨。",
    }


def _content_filter_placeholder_sentiment_cluster(reason: str) -> dict:
    return {
        "theme_relevance": "irrelevant", "source_class": "Unknown", "reaction_type": "Unknown",
        "attitude": "unknown", "cluster_consistency": "Low", "apply_to_cluster": False,
        "exception_pattern": ["other"],
        "classification_reason": f"Azure content filterにより本文取得不可（システム除外）: {reason}",
    }


def _content_filter_placeholder_targeted_stance(_reason: str) -> dict:
    return {"targets": []}


_STAGE_BATCH_CONFIG = {
    "prompt1_analysis_cluster": {
        "batch_loader": _load_analysis_clusters_and_representatives_batch,
        # 関数名(文字列)で持ち、呼び出し直前にgetattrで解決する。モジュール属性への直接参照を
        # ここへ束縛すると、テストのmonkeypatch.setattr(classify, "...", stub)が効かなくなる
        # （importlib的な属性ルックアップではなく値コピーになってしまうため）。
        "batch_classify_name": "classify_analysis_clusters_batch",
        "item_schema": classify.ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA,
        "build_item": _analysis_cluster_build_item,
        "save": _save_analysis_cluster_result,
        "content_filter_placeholder": _content_filter_placeholder_analysis_cluster,
    },
    "prompt1_sentiment_cluster": {
        "batch_loader": _load_sentiment_clusters_and_representatives_batch,
        "batch_classify_name": "classify_sentiment_clusters_batch",
        "item_schema": classify.SENTIMENT_CLUSTER_CLASSIFICATION_SCHEMA,
        "build_item": _sentiment_cluster_build_item,
        "save": _save_sentiment_cluster_result,
        "content_filter_placeholder": _content_filter_placeholder_sentiment_cluster,
    },
    "prompt1_targeted_stance": {
        # Targeted StanceもSentiment Clusterの器（representatives）を使う（Prompt1仕様通り）。
        "batch_loader": _load_sentiment_clusters_and_representatives_batch,
        "batch_classify_name": "classify_targeted_stance_batch",
        "item_schema": classify.TARGETED_STANCE_SCHEMA,
        "build_item": _sentiment_cluster_build_item,
        "save": _save_targeted_stance_result,
        "content_filter_placeholder": _content_filter_placeholder_targeted_stance,
    },
}


def _log_llm_call(client, execution_id: str, stage: str, model: str, result: dict, item_count: int,
                   latency_ms: int) -> None:
    """1回のバッチLLM呼び出し(成功分のみ)をgeo_report_execution_logへ記録する
    （2026-09-11、実行効率の実測分析——AC/SC/Targeted Stance毎の呼び出し数・平均
    input/output tokens・平均latency・429リトライ回数を後から集計できるようにする）。"""
    usage = result.get("usage") or {}
    try:
        client.insert("geo_report_execution_log", [{
            "id": str(uuid.uuid4()), "execution_id": execution_id, "stage": stage, "model": model,
            "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
            "cached_tokens": usage.get("cached_tokens"),
            "item_count": item_count, "latency_ms": latency_ms,
            "rate_limit_retries": result.get("rate_limit_retries", 0),
        }])
    except Exception:
        pass  # ログ記録の失敗で本処理を止めない（分析用の補助情報のため）


def _is_content_filter_error(exc: Exception) -> bool:
    return "content_filter" in str(exc).lower()


def _bisect_content_filtered(client, azure_client, model: str, cluster_ids: list, loaded: dict,
                               batch_classify, item_schema, build_item) -> tuple:
    """content_filter（Azureの責任あるAI ポリシーによる400拒否）でバッチ全体が失敗した
    場合の隔離処理（2026-09-12実機確認: 性的/暴力的表現を含む代表投稿1件のせいで
    バッチ全体が"何度リトライしても同じ理由で必ず失敗する"ことを実データで確認した。
    通常のitem-level retryは「呼び出しは成功したが一部の結果が欠落/不正」という
    ケース向けであり、「呼び出し自体が決定的に拒否される」ケースには効かないため
    別処理が必要）。バッチを再帰的に二分し、1件まで絞り込めたら原因クラスタとして
    除外する。戻り値: (valid: dict, excluded: dict[cluster_id, reason], retry_needed: list)。"""
    if len(cluster_ids) == 1:
        cid = cluster_ids[0]
        try:
            batch_classify(azure_client, model, [build_item(*loaded[cid])])
            return {}, {}, [cid]  # 単独では再現しなかった(一過性)→通常retryへ差し戻す
        except Exception as e:
            if _is_content_filter_error(e):
                return {}, {cid: str(e)[:300]}, []
            return {}, {}, [cid]

    mid = len(cluster_ids) // 2
    valid: dict = {}
    excluded: dict = {}
    retry_needed: list = []
    for half in (cluster_ids[:mid], cluster_ids[mid:]):
        entries = [build_item(*loaded[cid]) for cid in half]
        try:
            result = batch_classify(azure_client, model, entries)
            raw_results = result["data"].get("results", [])
            reconciled = geo_llm_common.reconcile_batch_llm_results(half, raw_results, item_schema)
            valid.update(reconciled["valid"])
            retry_needed.extend(reconciled["missing"] + reconciled["invalid"])
        except Exception as e:
            if _is_content_filter_error(e):
                sub_valid, sub_excluded, sub_retry = _bisect_content_filtered(
                    client, azure_client, model, half, loaded, batch_classify, item_schema, build_item)
                valid.update(sub_valid)
                excluded.update(sub_excluded)
                retry_needed.extend(sub_retry)
            else:
                retry_needed.extend(half)  # content_filter以外はまとめて通常retryへ差し戻す
    return valid, excluded, retry_needed


def _classify_batch_with_retry(client, azure_client, model: str, run_id: str, cluster_ids: list, *,
                                 batch_loader, batch_classify, item_schema, build_item,
                                 content_filter_placeholder=None,
                                 execution_id: str = None, stage: str = None,
                                 max_extra_rounds: int = ITEM_RETRY_ROUNDS) -> tuple:
    """cluster_ids全件を1回のLLM呼び出しでまとめて分類する。missing/duplicate/unknown/
    invalidが発生した場合、その分だけ（バッチ全体ではなく該当クラスタだけ）最大
    max_extra_rounds回まで追加でLLM呼び出しを行う（呼び出し自体が例外を投げた場合も
    同じラウンドとして扱い、対象クラスタを変えずに次ラウンドへ進む）。
    戻り値: (valid: dict[cluster_id, item_body], unresolved: list[cluster_id])。
    クラスタ本体がDBに見つからないID（データ不整合）はLLMを呼ばずunresolvedへ直行する。

    2026-09-12性能改善: クラスタ本体・代表投稿は最初に1回だけbatch_loaderでIN取得する
    （従来はラウンドごと・クラスタごとにSELECTしていたため、batch_size=20なら
    1ラウンドあたり40回のPostgREST往復が発生していた）。

    2026-09-12堅牢性改善: content_filterで呼び出し自体が拒否された場合は二分探索で
    原因クラスタを特定・除外し、残りは分類を続行する（content_filter_placeholderが
    指定されていれば、除外したクラスタにも「システムにより除外」であることを明示する
    プレースホルダー分類を割り当てる。指定が無ければ従来通りunresolvedのまま返す）。"""
    loaded = batch_loader(client, run_id, cluster_ids)
    unresolved_missing_upfront = [cid for cid in cluster_ids if cid not in loaded]
    remaining = [cid for cid in cluster_ids if cid in loaded]
    valid: dict = {}

    for _round in range(max_extra_rounds + 1):
        if not remaining:
            break
        entries = [build_item(*loaded[cid]) for cid in remaining]
        call_started = time.monotonic()
        try:
            result = batch_classify(azure_client, model, entries)
            raw_results = result["data"].get("results", [])
        except Exception as e:
            if _is_content_filter_error(e) and len(remaining) > 1:
                bvalid, bexcluded, bretry = _bisect_content_filtered(
                    client, azure_client, model, remaining, loaded, batch_classify, item_schema, build_item)
                valid.update(bvalid)
                if content_filter_placeholder is not None:
                    for cid, reason in bexcluded.items():
                        valid[cid] = content_filter_placeholder(reason)
                else:
                    # placeholderが無い場合は除外クラスタを消さずunresolvedへ残す
                    # （valid/unresolvedのどちらにも入らず消えてしまうのを防ぐ）。
                    bretry = bretry + list(bexcluded.keys())
                remaining = bretry
                continue
            continue  # このラウンドは呼び出し自体が失敗。remainingを変えず次ラウンドへ。
        if execution_id and stage:
            _log_llm_call(client, execution_id, stage, model, result, len(entries),
                          int((time.monotonic() - call_started) * 1000))
        reconciled = geo_llm_common.reconcile_batch_llm_results(remaining, raw_results, item_schema)
        valid.update(reconciled["valid"])
        remaining = list(dict.fromkeys(reconciled["missing"] + reconciled["invalid"]))

    unresolved = remaining + unresolved_missing_upfront
    return valid, unresolved


def _process_batch(client, azure_client, model: str, run_id: str, execution_id: str, batch: dict) -> dict:
    if cancellation.is_cancel_requested(client, run_id):
        # まだ'pending'のまま何もせず返す——キャンセル解除後に再実行すればそのまま
        # 処理できる（ユーザーによる中断、2026-09-11）。
        return {"processed": True, "batch_id": batch["id"], "status": "cancelled"}

    stage = batch["stage"]
    config = _STAGE_BATCH_CONFIG.get(stage)
    if config is None:
        _mark_batch(client, batch["id"], "failed", error_message=f"未対応のstageです: {stage}")
        _update_execution_progress(client, execution_id)
        return {"processed": True, "batch_id": batch["id"], "status": "failed", "error": f"未対応のstageです: {stage}"}

    try:
        return _process_batch_body(client, azure_client, model, run_id, execution_id, batch, config)
    except Exception as e:
        # 2026-09-12堅牢性改善（実機確認）: DB更新・ネットワーク瞬断等、_process_batch
        # 内のどこで例外が起きても、ThreadPoolExecutor経由でPipeline全体（Prompt1〜
        # Prompt2の統合実行、数時間規模）をクラッシュさせないための最終防波堤。
        # 個別の失敗要因（保存時の一意制約違反等）は可能な限りここへ来る前に
        # 捕捉する（_process_batch_body内）が、ここは「その他すべて」の保険。
        message = f"バッチ処理中に想定外のエラー: {e}"
        try:
            _mark_batch(client, batch["id"], "failed", error_message=message[:2000])
            _update_execution_progress(client, execution_id)
        except Exception:
            pass  # 失敗記録自体が失敗しても(同じ障害等)、ここで諦めて呼び出し元は守る
        return {"processed": True, "batch_id": batch["id"], "status": "failed", "error": message}


def _process_batch_body(client, azure_client, model: str, run_id: str, execution_id: str, batch: dict,
                          config: dict) -> dict:
    stage = batch["stage"]
    _mark_batch(client, batch["id"], "running", model_used=model)
    item_ids = batch["item_ids"]
    batch_classify = getattr(classify, config["batch_classify_name"])
    valid, unresolved = _classify_batch_with_retry(
        client, azure_client, model, run_id, item_ids,
        batch_loader=config["batch_loader"], batch_classify=batch_classify,
        item_schema=config["item_schema"], build_item=config["build_item"],
        content_filter_placeholder=config["content_filter_placeholder"],
        execution_id=execution_id, stage=stage)

    # 保存自体の失敗（DB一意制約違反・一時的なネットワーク障害等）で例外を伝播させると
    # 呼び出し元(_process_batch)の最終防波堤で捕捉されバッチ全体がfailedになるが、
    # ここで先に1クラスタ単位に閉じ込めることで、同じバッチ内の他クラスタの保存は
    # 継続できる（該当クラスタは次回retry_batchで再試行できる）。
    save_failed: list = []
    for cluster_id, item_body in valid.items():
        try:
            config["save"](client, execution_id, run_id, cluster_id, item_body)
        except Exception as e:
            save_failed.append(f"{cluster_id}: {e}")
    if save_failed:
        for msg in save_failed[:5]:
            unresolved.append(msg.split(":", 1)[0])

    if unresolved:
        message = f"{len(unresolved)}/{len(item_ids)}件が未解決（missing/invalid）: " + ", ".join(unresolved[:10])
        if save_failed:
            message += " | 保存失敗: " + "; ".join(save_failed[:5])
        if len(unresolved) > 10:
            message += f" (他{len(unresolved) - 10}件を省略)"
        _mark_batch(client, batch["id"], "failed", error_message=message)
        _update_execution_progress(client, execution_id)
        return {"processed": True, "batch_id": batch["id"], "status": "failed", "error": message}

    _mark_batch(client, batch["id"], "completed")
    _update_execution_progress(client, execution_id)
    return {"processed": True, "batch_id": batch["id"], "status": "completed"}


def _list_pending_batches(client, execution_id: str, stage: str) -> list:
    return _select_all_paginated(client, "geo_report_execution_batches", {
        "select": "*", "execution_id": f"eq.{execution_id}", "stage": f"eq.{stage}",
        "status": "eq.pending", "order": "batch_no.asc",
    })


def run_all_pending_batches(client, azure_client, model: str, run_id: str, execution_id: str, stage: str, *,
                              max_batches: int = None) -> list:
    """pending中のバッチを（バッチ=1回のLLM呼び出しになったため）最大MAX_CONCURRENT_
    BATCHES件まで同時実行する。1件でもfailedになった場合も他のバッチの処理は止めない
    （失敗したバッチは後でretry_batchで個別に再試行できる）。"""
    pending = _list_pending_batches(client, execution_id, stage)
    if max_batches is not None:
        pending = pending[:max_batches]
    if not pending:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_BATCHES, len(pending)) or 1) as pool:
        futures = [pool.submit(_process_batch, client, azure_client, model, run_id, execution_id, batch)
                   for batch in pending]
        for future in as_completed(futures):
            results.append(future.result())
    return results
