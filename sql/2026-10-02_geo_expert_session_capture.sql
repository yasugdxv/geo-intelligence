-- Expert Session Capture V1（Phase S2-S3、2026-10-02）
--
-- Expert Sessionで、Gold Caseの評価・Calibration CaseのExpert-first入力/Lock/Reveal/
-- Gap Reviewを保存するための最小テーブル。Expert AI Core・CQAS・Frozen response（
-- geo_expert_responses）・既存Report Review（geo_ios_review_cycles）には一切触れない。
--
-- Source of Truth: docs/expert-session-case-registry.md（case_key → response_idの対応は
-- backend側ハードコード registryで持ち、このテーブル自体にはresponse_idを複製しない——
-- Frozen responseはgeo_expert_responsesのみがSource of Truthのまま）。
--
-- Lock/Reveal Gate: locked_at/revealed_atはnullable timestamptz。Server-side serviceが
-- 「既にlocked_atがある場合はexpert_first_inputの更新を拒否」「locked_atが無い場合は
-- revealを拒否」を強制する（カラム自体にはDB制約を持たせず、service層のロジックで保証する
-- ——既存geo_ios_review_cycles.statusと同じ「statusカラム+serviceでの遷移制御」方針を踏襲）。

create table public.geo_expert_session_case_runs (
    run_id              uuid primary key default gen_random_uuid(),
    case_key            text not null,
    case_type           text not null check (case_type in ('gold', 'calibration')),
    expert_identity_id  text not null,
    status              text not null default 'not_started'
                             check (status in ('not_started', 'in_progress', 'locked', 'revealed', 'completed')),
    expert_first_input  jsonb,
    locked_at           timestamptz,
    revealed_at         timestamptz,
    submitted_at        timestamptz,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    unique (case_key, expert_identity_id)
);
comment on table public.geo_expert_session_case_runs is
    'Expert Session Capture V1: 1 expert identity x 1 caseごとのrun（進行状態とCalibrationのblind input/lock/reveal timestampを保持）。';

create index geo_expert_session_case_runs_case_key_idx
    on public.geo_expert_session_case_runs(case_key);

alter table public.geo_expert_session_case_runs enable row level security;

create table public.geo_expert_session_evaluations (
    evaluation_id       uuid primary key default gen_random_uuid(),
    run_id              uuid not null references public.geo_expert_session_case_runs(run_id) on delete cascade,
    rubric_assessments  jsonb,
    feedback_tags       jsonb not null default '[]'::jsonb,
    expert_comment      text,
    gap_review          jsonb,
    submitted_at        timestamptz,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    unique (run_id)
);
comment on table public.geo_expert_session_evaluations is
    'Expert Session Capture V1: Gold評価（7項目rubric + comment + tag）またはCalibrationのGap Review（Reveal後）。run_id単位で1行。';

alter table public.geo_expert_session_evaluations enable row level security;
