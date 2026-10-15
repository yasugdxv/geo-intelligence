-- Report Studio v2 Phase 1: Report Edit Chat用artifact_type追加。
-- 詳細: geo_report_report_edit_service.py（新規）。
-- geo_report_executions.stageには既に'report_edit'が許可済み（2026-09-11時点のDDL、
-- 変更不要）。geo_report_run_artifacts.artifact_typeにはReport Edit指示文を保存する
-- 値が無いため、check制約を作り直して追加する（Postgresはcheck制約への値追加を
-- ALTERで直接行えないため、既存制約をDROPしてCREATEし直す。Additive only、
-- 既存データ・既存artifact_type値は変更しない）。

alter table public.geo_report_run_artifacts
    drop constraint if exists geo_report_run_artifacts_artifact_type_check;

alter table public.geo_report_run_artifacts
    add constraint geo_report_run_artifacts_artifact_type_check check (artifact_type in (
        'collection_input', 'preprocess_output', 'prompt_snapshot', 'prompt1_input', 'prompt1_output',
        'aggregation_output', 'prompt2_base_input', 'composition_result', 'final_report', 'prompt2_output',
        'report_template_snapshot', 'continuity_snapshot', 'report_config_snapshot', 'report_edit_instruction'));

comment on constraint geo_report_run_artifacts_artifact_type_check on public.geo_report_run_artifacts is
    '2026-09-13: report_edit_instruction追加（Report Edit Chatのユーザー指示文＋'
    'AIのedit_level/reason/warningをJSON保存、stage=report_editのexecution_idに紐づく）。';
