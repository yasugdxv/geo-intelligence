"""Report Studio「Design」領域: Canonical Execution切り替え＋Stale伝播
（kind-napping-hare.md J.2、M#22/23/27、N#16/18/22参照）。

Application AI経路・ChatGPT Fallback経路のどちらの実行結果を「正式採用（Canonical）」と
するかを切り替える唯一の入り口。Prompt1側は`geo_report_prompt1_orchestration_service.
complete_prompt1_execution_if_done`が全バッチ完了時に自動でCanonicalを設定するが、
ChatGPT Import結果を採用する場合や、既にCanonicalな結果を後から差し替える場合はこの
モジュールの`set_canonical_execution`を明示的に呼ぶ。

切り替え後は必ず`propagate_staleness`を呼び、Canonicalが変わったことで前提が崩れた
下流Execution（Aggregation/Prompt2、およびそこから派生したReport Edit連鎖）を
`status='stale'`にする。
"""

from __future__ import annotations

_STAGE_TO_CANONICAL_COLUMNS = {
    "prompt1": ["canonical_prompt1_execution_id"],
    "prompt2": ["canonical_prompt2_execution_id", "canonical_report_execution_id"],
    # Report Edit（2026-09-13、Report Studio v2 Phase 1）: canonical_report_execution_id
    # のみを更新する（要件4/28: canonical_prompt2_execution_idとContinuityの起点は
    # Report Editで変更しない）。
    "report_edit": ["canonical_report_execution_id"],
}


def _get_run(client, run_id: str) -> dict:
    rows = client.select("geo_report_collection_runs", {"select": "*", "run_id": f"eq.{run_id}"})
    if not rows:
        raise ValueError(f"run_id={run_id} が見つかりません。")
    return rows[0]


def _get_execution(client, execution_id: str) -> dict:
    rows = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})
    if not rows:
        raise ValueError(f"execution_id={execution_id} が見つかりません。")
    return rows[0]


def set_canonical_execution(client, run_id: str, stage: str, execution_id: str) -> dict:
    """execution_idを`stage`のCanonicalとして採用する（Application AI/ChatGPT Import
    どちらの結果でも同じ入口）。同一run_id・指定stage・status='completed'であることを
    検証してから切り替え、下流Executionのstale伝播まで行う。"""
    if stage not in _STAGE_TO_CANONICAL_COLUMNS:
        raise ValueError(f"stageは{list(_STAGE_TO_CANONICAL_COLUMNS)}のいずれかである必要があります。")
    _get_run(client, run_id)
    execution = _get_execution(client, execution_id)
    if execution["run_id"] != run_id:
        raise ValueError(f"execution_id={execution_id}はrun_id={run_id}のExecutionではありません。")
    if execution["stage"] != stage:
        raise ValueError(f"execution_id={execution_id}のstageは'{execution['stage']}'であり、'{stage}'ではありません。")
    if execution["status"] != "completed":
        raise ValueError(
            f"execution_id={execution_id}はstatus='{execution['status']}'のため採用できません（completedのみ可）。")

    patch = {col: execution_id for col in _STAGE_TO_CANONICAL_COLUMNS[stage]}
    client.update("geo_report_collection_runs", {"run_id": f"eq.{run_id}"}, patch)
    staled_execution_ids = propagate_staleness(client, run_id, stage, execution_id)
    return {
        "run_id": run_id, "stage": stage, "canonical_execution_id": execution_id,
        "staled_execution_ids": staled_execution_ids,
    }


def _find_source_prompt2_execution_id(execution: dict, executions_by_id: dict) -> str | None:
    """report_edit executionのparent_execution_idを辿り、最初に見つかったprompt2
    executionのidを返す（Editが複数回連鎖していても正しく最上流へ到達する）。
    循環参照や到達不能な場合はNoneを返す。"""
    seen = set()
    current = execution
    while current is not None:
        if current["id"] in seen:
            return None  # 循環参照ガード(通常発生しない)
        seen.add(current["id"])
        if current["stage"] == "prompt2":
            return current["id"]
        parent_id = current.get("parent_execution_id")
        current = executions_by_id.get(parent_id) if parent_id else None
    return None


def propagate_staleness(client, run_id: str, changed_stage: str, new_canonical_execution_id: str) -> list:
    """`changed_stage`のCanonicalがnew_canonical_execution_idへ切り替わったことで、
    それ以外の入力から作られていた既存の完了済み下流Executionをstale化する。
    既にpending/running/failed/staleなExecutionは対象外（authoritativeでないものを
    重ねてstale化する意味が無いため、completedのみ扱う）。"""
    if changed_stage not in _STAGE_TO_CANONICAL_COLUMNS:
        raise ValueError(f"changed_stageは{list(_STAGE_TO_CANONICAL_COLUMNS)}のいずれかである必要があります。")

    all_executions = client.select("geo_report_executions", {"select": "*", "run_id": f"eq.{run_id}"})
    executions_by_id = {e["id"]: e for e in all_executions}
    staled_ids: list = []

    if changed_stage == "prompt1":
        stale_aggregations = [
            e for e in all_executions
            if e["stage"] == "aggregation" and e["status"] == "completed"
            and e.get("input_prompt1_execution_id") != new_canonical_execution_id
        ]
        stale_aggregation_ids = {e["id"] for e in stale_aggregations}
        for e in stale_aggregations:
            client.update("geo_report_executions", {"id": f"eq.{e['id']}"}, {"status": "stale"})
            staled_ids.append(e["id"])

        stale_prompt2 = [
            e for e in all_executions
            if e["stage"] == "prompt2" and e["status"] == "completed"
            and e.get("input_aggregation_execution_id") in stale_aggregation_ids
        ]
        stale_prompt2_ids = {e["id"] for e in stale_prompt2}
        for e in stale_prompt2:
            client.update("geo_report_executions", {"id": f"eq.{e['id']}"}, {"status": "stale"})
            staled_ids.append(e["id"])

        # Prompt1 Canonicalの切り替えでPrompt2がstale化した場合、そこからさらに
        # 派生したReport Edit Executionの連鎖まで辿ってstale化する（Phase 5のE2E
        # 検証で発覚したMAJOR: ChatGPT Import結果をPrompt1 Canonicalに採用した際、
        # 既存のReport Edit連鎖がcompletedのまま残り、canonical_report_execution_idが
        # 古いPrompt1結果に基づく編集内容を指し続けてしまっていた）。
        stale_report_edits_from_prompt1 = [
            e for e in all_executions
            if e["stage"] == "report_edit" and e["status"] == "completed"
            and _find_source_prompt2_execution_id(e, executions_by_id) in stale_prompt2_ids
        ]
        for e in stale_report_edits_from_prompt1:
            client.update("geo_report_executions", {"id": f"eq.{e['id']}"}, {"status": "stale"})
            staled_ids.append(e["id"])

    # changed_stage == "prompt2": Report EditはcanonicalなPrompt2結果ではなく
    # parent_execution_id経由の任意のReport Edit Executionから派生しうるため、
    # 「このRunの完了済みReport Edit Executionのうち、現在の(新しい)Prompt2
    # Canonicalへ辿り着かないもの」をstale化する（chainを遡ってprompt2 executionへ
    # 到達するかで判定、複数回Editが連鎖していても正しく判定できる）。
    # changed_stage == "report_edit"の場合は、Report Edit自体が末端工程のため
    # 現状stale化する下流Executionが無い。
    if changed_stage == "prompt2":
        stale_report_edits = [
            e for e in all_executions
            if e["stage"] == "report_edit" and e["status"] == "completed"
            and _find_source_prompt2_execution_id(e, executions_by_id) != new_canonical_execution_id
        ]
        for e in stale_report_edits:
            client.update("geo_report_executions", {"id": f"eq.{e['id']}"}, {"status": "stale"})
            staled_ids.append(e["id"])

    return staled_ids
