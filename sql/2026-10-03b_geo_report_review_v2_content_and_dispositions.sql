-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS): Report Review V2
-- Phase R1 (Content Review) + Phase R2 (Human Expert Review) 用の追加カラム。
--
-- 既存のgeo_report_review_v2_runs（Core Pipeline、Checkpoint固定済み）には
-- 一切触れず、新しい2カラムを追加するのみ。Expert AI Core・Reading Presentation v2・
-- Reading Review（report_reading/expert_reading/comparison_findings/
-- comparison_summary）のロジック・Schemaは無改修。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このリポジトリに
-- マイグレーションランナーは無い、既存運用どおり）。destructive文は含まない。
-- =====================================================================

alter table public.geo_report_review_v2_runs
    -- Phase R1: geo_ios_expert_ai_adapter.run_report_content_reviewのreview_findings。
    -- Reading Review（comparison_findings）とは別レイヤーとして保持する（ユーザー指示）。
    add column content_findings jsonb;

alter table public.geo_report_review_v2_runs
    -- Phase R2: Reading Review/Content Review各findingに対する人間専門家の判断。
    -- key形式: "<source>:<finding_id>"（source=reading|content）。
    -- value形式: {source, finding_id, disposition(agree|modify|reject), reason,
    --            modification, reviewed_by, reviewed_at}。
    -- AI Reviewの結果をそのまま正解扱いしない（ユーザー指示）——Agree以外も含め、
    -- 人間の最終判断をここに記録する。Learning Candidateへの接続は将来のPhaseに
    -- 先送りする（本カラムは判断の記録のみ、Learning Candidate生成ロジックは
    -- 本Migrationでは一切追加しない）。
    add column human_expert_dispositions jsonb not null default '{}'::jsonb;

comment on column public.geo_report_review_v2_runs.content_findings is
    'Phase R1 Content Review（レポート本文の品質レビュー）の指摘一覧。Reading Review'
    '(comparison_findings)とは別レイヤー。';

comment on column public.geo_report_review_v2_runs.human_expert_dispositions is
    'Phase R2 Human Expert Review。AIのfinding（Reading Review/Content Review）ごとの'
    '人間専門家のAgree/Modify/Reject判断と理由。AI Reviewの結果をそのまま正解扱いしない。';
