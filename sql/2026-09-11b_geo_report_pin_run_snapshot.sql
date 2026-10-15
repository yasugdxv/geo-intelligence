-- =====================================================================
-- geo_report_pin_run_snapshot（Run作成時のDesign/Narrative/Template/Prompt/
-- Config/Continuity/Processing Version固定を1トランザクションで行うAtomic RPC）
-- 作成日: 2026-09-11
-- 根拠: C:\Users\269811\.claude\plans\kind-napping-hare.md
--   （「Run作成時の完全固定」要件。Phase B、create_run契約確定分）
--
-- 設計方針:
--   * geo_report_collection_runs行自体は本関数の呼び出し前に作成済みであること
--     （前処理=CSV取り込みは既存のpersist_collection_run/run_preprocessが担い、
--     本関数はその後段の「固定」ステップのみを担当する）。
--   * 固定対象は全てこの1関数のトランザクション内で書き込む:
--     geo_report_collection_runsの列更新＋geo_report_run_config行の作成＋
--     geo_report_run_artifacts（prompt_snapshot x2）の作成。
--     途中で例外が起きればPostgresの暗黙トランザクション境界で自動ロールバックされ、
--     Runの列は更新されずSnapshot行も1件も作られない（部分状態が残らない）。
--   * previous_continuity_artifact_id/baseline_prompt2_execution_idは呼び出し側
--     （Python）が解決してから渡す（baseline_run_idからの解決ロジックは
--     Phase Eの実行系譜が整うまでPython側に置く方が変更に強い）。
-- =====================================================================

create or replace function public.geo_report_pin_run_snapshot(
    p_run_id uuid,
    p_project_id uuid,
    p_design_version_id uuid,
    p_prompt1_contract_version text,
    p_prompt1_instruction_text text,
    p_prompt1_instruction_fingerprint text,
    p_prompt2_contract_version text,
    p_prompt2_instruction_text text,
    p_prompt2_instruction_fingerprint text,
    p_report_config_json jsonb,
    p_primary_graph_signals text[],
    p_common_primary_graph_start date,
    p_preprocess_contract_version text,
    p_aggregation_contract_version text,
    p_renderer_version text,
    p_application_build_revision text,
    p_baseline_prompt2_execution_id uuid,
    p_previous_continuity_artifact_id uuid
) returns public.geo_report_collection_runs
language plpgsql
as $$
declare
    v_design_version public.geo_report_design_versions;
    v_result          public.geo_report_collection_runs;
begin
    select * into v_design_version from public.geo_report_design_versions
        where id = p_design_version_id and project_id = p_project_id;
    if v_design_version.id is null then
        raise exception 'design_version_id % がproject_id % のDesign Versionと一致しません', p_design_version_id, p_project_id;
    end if;

    update public.geo_report_collection_runs set
        design_version_id = p_design_version_id,
        narrative_master_asset_id = v_design_version.narrative_master_asset_id,
        report_template_asset_id = v_design_version.report_template_asset_id,
        prompt1_contract_version = p_prompt1_contract_version,
        prompt1_instruction_fingerprint = p_prompt1_instruction_fingerprint,
        prompt2_contract_version = p_prompt2_contract_version,
        prompt2_instruction_fingerprint = p_prompt2_instruction_fingerprint,
        preprocess_contract_version = p_preprocess_contract_version,
        aggregation_contract_version = p_aggregation_contract_version,
        renderer_version = p_renderer_version,
        application_build_revision = p_application_build_revision,
        baseline_prompt2_execution_id = p_baseline_prompt2_execution_id,
        previous_continuity_artifact_id = p_previous_continuity_artifact_id
    where run_id = p_run_id and project_id = p_project_id
    returning * into v_result;

    if v_result.run_id is null then
        raise exception 'run_id % がproject_id % のRunと一致しません', p_run_id, p_project_id;
    end if;

    insert into public.geo_report_run_config (run_id, config_json, primary_graph_signals, common_primary_graph_start)
        values (p_run_id, p_report_config_json, p_primary_graph_signals, p_common_primary_graph_start)
        on conflict (run_id) do update set
            config_json = excluded.config_json,
            primary_graph_signals = excluded.primary_graph_signals,
            common_primary_graph_start = excluded.common_primary_graph_start;

    insert into public.geo_report_run_artifacts
        (id, run_id, execution_id, execution_method, artifact_type, file_name, content_text, sha256, size_bytes, media_type)
        values
        (gen_random_uuid(), p_run_id, null, 'system_snapshot', 'prompt_snapshot', 'prompt1_instruction.md',
         p_prompt1_instruction_text, p_prompt1_instruction_fingerprint, length(p_prompt1_instruction_text), 'text/markdown'),
        (gen_random_uuid(), p_run_id, null, 'system_snapshot', 'prompt_snapshot', 'prompt2_instruction.md',
         p_prompt2_instruction_text, p_prompt2_instruction_fingerprint, length(p_prompt2_instruction_text), 'text/markdown');

    return v_result;
end;
$$;

comment on function public.geo_report_pin_run_snapshot is
    'Run作成後段の固定ステップ専用。呼び出し前にgeo_report_collection_runs行が存在すること'
    '（project_idと一致するrun_id）。design_version_idが実在しCurrent Projectのものであること'
    'を検証してからDesign/Narrative/Template/Prompt/Config/Processing Version/Continuityを'
    '1トランザクションで固定する。例外時は自動ロールバックされ、Run列・Config行・Prompt'
    'Snapshot行のいずれも部分的に残らない';
