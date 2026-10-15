-- =====================================================================
-- geo_handoff_create_session_if_absent（Phase 2G-5 fix: Theme Candidate →
-- Expert AI HandoffのConcurrent Idempotency補強）
-- 作成日: 2026-10-01
--
-- 問題: 既存のHandoff実装は「source_theme_candidate_idで既存Session検索→
-- 無ければcreate_session()」という逐次チェックのみで、本当に同時に2つの
-- Handoff request（Double-click等）が来た場合、両方とも「existingなし」と
-- 判定してしまいSessionが2件作られるraceが残っていた（ユーザー指摘、
-- 2026-10-01）。
--
-- 対応方針: 恒久的なunique制約にはしない——将来「同一Candidateから複数の
-- 相談を開始する」機能を設計したくなった場合の余地を残す（ユーザー判断）。
-- 代わりに、この関数のtransaction-scoped advisory lock（pg_advisory_xact_lock、
-- 関数が属するtransactionのcommit/rollback時に自動解放される）で、同一
-- candidate_idに対するcheck-and-createだけを直列化する。PostgRESTは1リクエスト
-- = 1トランザクションのため、関数呼び出し全体が単一トランザクション内で完結する。
--
-- 既存のgeo_ios_analysis_session_service.create_session()（他の呼び出し元、
-- 逐次呼び出し前提）は変更しない。この関数はTheme Candidate Handoff専用。
-- =====================================================================

create or replace function public.geo_handoff_create_session_if_absent(
    p_candidate_id uuid,
    p_title text,
    p_theme text,
    p_user_id text,
    p_mode text default 'create'
) returns jsonb
language plpgsql
as $$
declare
    existing_row public.geo_ios_analysis_sessions;
    created_row  public.geo_ios_analysis_sessions;
begin
    perform pg_advisory_xact_lock(hashtext(p_candidate_id::text));

    select * into existing_row from public.geo_ios_analysis_sessions
        where source_theme_candidate_id = p_candidate_id
        order by created_at asc
        limit 1;

    if found then
        return jsonb_build_object('session', to_jsonb(existing_row), 'created', false);
    end if;

    insert into public.geo_ios_analysis_sessions
        (title, theme, user_id, status, mode, source_theme_candidate_id)
        values (p_title, p_theme, p_user_id, 'active', p_mode, p_candidate_id)
        returning * into created_row;

    return jsonb_build_object('session', to_jsonb(created_row), 'created', true);
end;
$$;

comment on function public.geo_handoff_create_session_if_absent is
    'Theme Candidate → Expert AI Handoff専用。同一candidate_idへの同時Handoff requestでも'
    'Sessionが1件しか作られないことをtransaction-scoped advisory lockで保証する。'
    '恒久的なunique制約ではない（将来の複数相談開始の余地を残す）。';
