-- =====================================================================
-- geo_expert_requests.mode CHECK制約に新モード"draft_generation"を追加
--
-- IOS Native Report Draft（sql/2026-09-18_geo_ios_native_report_drafts.sqlの追補）で
-- 追加したExpert AIの新モード"draft_generation"（geo_expert_schema.MODES/
-- schemas/geo_expert_ai_request.schema.json）が、geo_expert_requests.modeの
-- DB側CHECK制約に含まれておらず、実際に呼び出すとINSERT自体が400で拒否される
-- （2026-09-18、実LLM呼び出しテストで発見）。既存7モードの制約はそのまま維持し、
-- draft_generationを追加するだけ（destructive文は含まない、既存行への影響なし）。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する。
-- =====================================================================

alter table public.geo_expert_requests
    drop constraint geo_expert_requests_mode_check;

alter table public.geo_expert_requests
    add constraint geo_expert_requests_mode_check
        check (mode in (
            'theme_design', 'research_design', 'case_analysis', 'outline_review',
            'draft_review', 'executive_brief', 'analyst_coaching', 'draft_generation'
        ));

comment on column public.geo_expert_requests.mode is
    '指示書14.2章の7モード＋IOS Native Report Draft用のdraft_generationモード（8種）'
    'のいずれか。';
