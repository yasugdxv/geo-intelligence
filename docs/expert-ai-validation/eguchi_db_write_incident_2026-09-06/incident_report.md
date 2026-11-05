# DB Write Incident — 2026-09-06

## 概要

Eguchi Expert AI Final v1.0 Tier1のOFF/ON比較検証中、`geo_expert_engine.run_expert_request_staged`
のdocstring「DBへの保存は行わない（比較実験用）」を信用し、DB非破壊だと誤って報告していた。
実際にはこの関数は常に以下へ書き込んでいた:

- `geo_expert_requests` / `geo_source_packs` / `geo_expert_responses` / `geo_quality_evaluations` へのINSERT
- `geo_cases.final_output` のUPDATE（`case_id`指定時）
- `geo_retrieval_runs` / `geo_retrieval_results` へのINSERT、`geo_knowledge_items.usage_count`/`last_used_at`のUPDATE（`_record_case_retrieval_trace`経由、`case_id`指定時）
- `geo_audit_logs` へのINSERT（`geo_audit.log_audit`経由）

この結果、以下2ケースの`geo_cases.final_output`が実際に上書きされた:

- `M9 Pilot: Zelenskyy Oval Office Clash`（case_id: `4d6e7894-cd39-48d2-8f9e-1efbcf74b294`）
- `Expert AI Benchmark: Trump-Xi US-China Semiconductor & AI Controls`（case_id: `61447d94-e5cc-48f9-9352-31df72af9628`）

## 対応状況

### 1. 現状保全（完了）
上書き後の値（ON実行結果）を退避済み:
- `m9_geo_cases_row_before_restore_2026-09-06T05-04-42.json`
- `case1_geo_cases_row_current_overwritten_2026-09-06T05-28-52.json`

### 2. M9 Pilot 復元（完了、ユーザー承認済み）
実行前バックアップ（`m9_geo_cases_row_original_backup_pre_run.json`、`updated_at`が2026-08-31
であることを確認済み、実行より前の完全なスナップショット）から`final_output`のみを復元した。
`updated_at`は復元操作の実施時刻（2026-09-06T05:37:23）のまま——過去時刻への偽装はしていない。
実行ログ: `m9_restore_log.json`（restored_at, exact_match_confirmed=true）。

### 3. Case1 Trump-Xi 復元候補の特定（提案、未実施——ユーザー承認待ち）

`geo_retrieval_runs`テーブルは`case_id`を直接保持しており、この関数が`geo_cases.final_output`を
更新するのと同じ実行の中で書き込まれるため、この関数がこのcase_idに対して実行された正確な
履歴として使える。

Case1（`case_id=61447d94...`）の`geo_retrieval_runs`を`completed_at`昇順で確認したところ、
本日のOFF/ON実行（`22cf405d...`=05:25:36、`de2967e4...`=05:28:06）の直前は
**`expert_request_id=b3b390ad-62be-48cb-8cfc-c5eea873436d`（`completed_at`=2026-09-03T05:50:21）**
であり、この間に他のこのcase_idに対する実行は存在しない（`geo_retrieval_runs`全18件を確認、
時系列に抜けなし）。

このrequest_idに対応する`geo_expert_responses`は1件のみ:
`response_id=712a3c40-d111-485d-b194-d346947b4d66`、`created_at=2026-09-03T05:50:21`、
`model=Global-AzureOpenAI-DEV-gpt-5.1`、`prompt_version=geo-expert-v1.2-staged-phaseA`。
内容は`key_actor_profiles=[Donald Trump, Xi Jinping, Bureau of Industry and Security (BIS)]`、
`one_line_assessment`内に`as_of: 2026-09-03`と明記されており、Case1（米中半導体・AI規制）の
分析として整合している。

**注意**: `docs/expert-ai-validation/original_after_v2/raw/original_after_v2_case1_trump_xi_result.json`
にもCase1の過去出力があるが、これは`request_id=98199ea7...`（2026-09-01T08:10:55）で、
上記の直前実行(`b3b390ad...`, 09-03)より前の別のRunであり、上書き前の値としては**不採用**
（docsの既存ファイルを機械的に使うと誤った版を復元するリスクがあったため、DB内の
`geo_retrieval_runs`による正確な時系列照合を優先した）。

**復元前の最終機械的確認（ユーザー指示）**: 「retrieval_run→expert_request_id→
expert_response_id→response payload→geo_cases.final_output」という書込み経路が実コード上
成立していることを、**まだ改変していないCase2 Putin(`d9252cae-...`)で検証**した——
Case2の最新`geo_retrieval_runs`エントリに対応する`geo_expert_responses.response_json`は、
現在の`geo_cases.final_output`と**完全一致（EXACT MATCH）**することを実データで確認済み。
これによりこの書込み経路の妥当性が机上の推論ではなく実データで裏付けられた。

**復元実施済み（2026-09-06T05:51:02、ユーザー承認済み）**: `response_id=712a3c40...`の
`response_json`を`geo_cases.final_output`へ書き戻した。復元後の`final_output`が
`response_json`と完全一致することを確認済み。`updated_at`は復元操作の実施時刻のまま
（過去時刻への偽装はしていない）。詳細ログ: `case1_restore_log.json`
（recovered_from/provenance_verification/exact_match_confirmed等）。

### 4. Evaluation Dry-run実装（完了）

`geo_expert_engine.run_expert_request_staged`へ`persist: bool = True`を追加。
`persist=False`時は以下を一切行わない（Retrieval/selectは通常通り実行、Case Context/
Actor Intelligence/Foundation Pack等の入力は本番同等のまま）:

- `geo_expert_requests`/`geo_source_packs`/`geo_expert_responses`/`geo_quality_evaluations`へのINSERT
- `geo_cases.final_output`のUPDATE
- `_record_case_retrieval_trace`（`geo_retrieval_runs`/`geo_retrieval_results`のINSERT、
  `geo_knowledge_items.usage_count`/`last_used_at`のUPDATE）
- `geo_audit_logs`へのINSERT
- `persistent_preference_candidate`の生成（呼び出し自体をスキップ）

戻り値の形（`request`/`source_pack`/`response`/`quality_evaluation`等のキー構成）は
`persist=True`と同一に保つため、DB採番の代わりにメモリ上でUUIDを発行する。

docstringも実態に合わせて修正し、この経緯（2026-09-06に誤りが判明したこと）を明記した。

### 5. オフライン回帰テスト（完了）

- `tests/test_geo_expert_staged_execution.py`に新規テスト2件追加:
  - `test_persist_false_writes_zero_rows_to_all_tables`: persist=False時、
    geo_expert_requests/geo_source_packs/geo_expert_responses/geo_quality_evaluations/
    geo_audit_logs/geo_retrieval_runs/geo_retrieval_results/geo_casesの全てで
    書き込み行数=0であることを決定的に確認。
  - `test_persist_false_does_not_update_existing_case_final_output`: 既存の
    `geo_cases.final_output`が変更されないことを確認。
- 既存621件+新規2件、**リポジトリ全体623件全てpass**。

## 今後の運用ルール（今回の教訓）

- 評価・比較実験目的で`run_expert_request_staged`を呼ぶ場合は、**必ず`persist=False`を
  明示指定する**。`case_id`は外さない（Actor/Relationship/Foundation Pack/Case Contextの
  取得に必要なため、既存互換の入力を維持する）。
- 結果の保存は呼び出し元（評価Harness）が`docs/expert-ai-validation/...`へJSONとして
  保存する。
- docstringと実装が食い違っていないか、"DBに書かない"のような重要な安全性の記述は、
  実際にコードを読んで検証してから信用すること（今回はこれを怠ったことが根本原因）。
