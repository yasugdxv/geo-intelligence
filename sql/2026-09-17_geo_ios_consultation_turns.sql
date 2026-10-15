-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS): Consultation Turns
--
-- Expert AIへの相談を「切り口相談(framing)/調査方針(research_direction)/
-- プレレビュー(pre_review)」の3モードに分け、各モードごとに継続的な
-- チャット履歴を持たせるための永続化テーブル。既存Analysis Design
-- （geo_ios_analysis_designs、research_designモードのCQAS/Design専用）とは
-- 別の概念——本テーブルは「対話1ターン」を表すだけで、Analysis Designの
-- フィールドを更新しない（既存refine-design経路とは独立）。
--
-- 各ターンはExpert AI呼び出し完了まで数分かかる（フルパワーの標準実行を維持する
-- ユーザー判断のため、高速化のための簡略化はしない）。ページを離れても後から
-- 状態を確認できるよう、Expert AI呼び出し開始前にstatus='pending'で1行作成し、
-- 完了時にBackend側（BackgroundTasks、新規ジョブキュー基盤は追加しない）が
-- status='completed'/'failed'へ更新する——Frontendはこの行をポーリングするだけで、
-- ブラウザ側の一時状態（React Queryのmutation.isPending等）には依存しない。
--
-- 既存Expert AI（geo_expert_requests/geo_expert_responses、geo_expert_engine.py）・
-- Analysis Design（geo_ios_analysis_designs）のスキーマ・ロジックは一切変更しない。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは
-- 実行しない。geo_db_clientにマイグレーションランナーは存在せず、これが本リポジトリの
-- 既存運用）。destructive文（drop table/drop column）は含まない。
-- =====================================================================

create table public.geo_ios_consultation_turns (
    consultation_turn_id   uuid primary key default gen_random_uuid(),
    analysis_session_id    uuid not null
                                references public.geo_ios_analysis_sessions(analysis_session_id)
                                on delete cascade,
    -- framing=切り口相談(既存の局面を読む/自由に聞くと同じresearch_designモード)
    -- research_direction=調査方針(theme_designモード)
    -- pre_review=レポートのプレレビュー(draft_reviewモード、draft_text必須)
    consultation_mode      text not null
                                check (consultation_mode in ('framing', 'research_direction', 'pre_review')),
    user_message            text not null,
    draft_text              text,
    status                  text not null default 'pending'
                                check (status in ('pending', 'completed', 'failed')),
    -- 完了後のみ設定。既存geo_expert_requests/geo_expert_responsesへの参照のみで、
    -- 応答本体はコピーしない（Single Source of Truthを保つ）。
    expert_request_id       uuid,
    expert_response_id      uuid,
    error_message           text,
    created_by               text not null,
    created_at               timestamptz not null default now(),
    completed_at             timestamptz
);

create index geo_ios_consultation_turns_session_mode_idx
    on public.geo_ios_consultation_turns(analysis_session_id, consultation_mode, created_at);

create index geo_ios_consultation_turns_pending_idx
    on public.geo_ios_consultation_turns(status)
    where status = 'pending';

alter table public.geo_ios_consultation_turns enable row level security;

comment on table public.geo_ios_consultation_turns is
    'Expert AIへの相談1ターン（切り口相談/調査方針/プレレビュー）。DBに永続化した'
    'pending状態を持つことで、Frontend側の一時状態に依存せずページ離脱・再訪後も進捗を追跡できる。';
