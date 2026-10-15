-- Report Studio「Design」領域: geo_report_analysis_cluster_classifications /
-- geo_report_sentiment_cluster_classificationsへ、evidence_intelligence_v1
-- （Prompt1 Actor/Position/Evidence分類）専用の列を追加する。
--
-- 背景: この2テーブルは元々Public Voice Prompt1 v4.3向けに設計されており
-- （2026-09-11_geo_report_studio_design_and_current_pipeline.sql）、
-- source_class/reaction_type/tone_gradient/voice_direction等の列しか持たない。
-- evidence_intelligence_v1のPrompt1 Output（classified_clusters.csv/
-- sentiment_cluster_classified.csv）は全く別の列構成
-- （actor_category/evidence_type/position_summary/hypothesis_relevance等）を
-- 使うため、ChatGPT ImportがPostgREST 400（PGRST204 "Could not find the
-- 'actor_category' column ..."）で失敗していた（2026-09-16、xius3プロジェクトの
-- 実データImportで発見）。
--
-- 値は実データ（xius3、Analysis Cluster 2,843件分）で確認済みだが、AI生成の
-- 自由記述に近い列（hypothesis_relevance='H1|H3'のような複合値等）が含まれるため、
-- 既存のsource_class/reaction_type列と同じ方針でCHECK制約は付けずtextのままにする
-- （厳しすぎるCHECKで想定外の値がImport全体を失敗させる事故を避ける。実際
-- targeted_stance_status列のCHECK制約がまさにこの理由で今回の障害の一因になった）。

alter table public.geo_report_analysis_cluster_classifications
    add column if not exists actor_category text,
    add column if not exists actor_name text,
    add column if not exists evidence_type text,
    add column if not exists source_authority text,
    add column if not exists position_summary text,
    add column if not exists objective text,
    add column if not exists incentive text,
    add column if not exists "constraint" text,
    add column if not exists bargaining_position text,
    add column if not exists red_line text,
    add column if not exists hypothesis_relevance text,
    add column if not exists evidence_relation text,
    add column if not exists evidence_strength text,
    add column if not exists materiality text,
    add column if not exists reaction text,
    add column if not exists attitude text;

comment on column public.geo_report_analysis_cluster_classifications.actor_category is
    'evidence_intelligence_v1: クラスタの主体カテゴリ（Official/Government, Major Media, Industry/Corporate等）。';
comment on column public.geo_report_analysis_cluster_classifications."constraint" is
    'evidence_intelligence_v1: 当該アクターの行動制約（予約語のためダブルクォート必須）。';
comment on column public.geo_report_analysis_cluster_classifications.hypothesis_relevance is
    'evidence_intelligence_v1: 関連する仮説ID。複数該当時は"H1|H3"のようにパイプ区切り。';

alter table public.geo_report_sentiment_cluster_classifications
    add column if not exists actor_category text,
    add column if not exists evidence_relation text;

comment on column public.geo_report_sentiment_cluster_classifications.actor_category is
    'evidence_intelligence_v1: Sentiment Clusterの主体カテゴリ（Analysis Cluster側と同義）。';
