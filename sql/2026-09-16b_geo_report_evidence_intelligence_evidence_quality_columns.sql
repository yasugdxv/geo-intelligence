-- Report Studio「Design」領域: geo_report_analysis_cluster_classificationsへ
-- evidence_intelligence_v1のEvidence品質フィールド（evidence_directness）を追加する。
--
-- 背景: Phase H（evidence_intelligence_v1用Prompt2新規実装）のレビューで、
-- H1/H2/H3のHypothesis評価で使うと定義した項目（source_authority/directness等）が
-- classified_clusters.csvの列に存在しない状態で評価基準として要求する矛盾が
-- 見つかった。source_authorityは既存列だが、directness（一次発言か伝聞かの区別）は
-- 新規に追加する。
--
-- evidence_independenceは追加しない（レビューで、Cluster単位で独立評価する
-- Prompt1には「他Clusterと独立した情報源か」の判定が構造的に不可能と指摘され、
-- Prompt2側（複数Cluster横断のCross-Evidence Quality判定）へ責務を移した）。
--
-- targeted_stance_status列の実障害（2026-09-16、CHECK制約が原因でChatGPT Import
-- 全体が失敗した）から学んだ通り、この列にもCHECK制約は付けない
-- （厳しすぎるCHECKで想定外の値がImport全体を失敗させることを避ける）。

alter table public.geo_report_analysis_cluster_classifications
    add column if not exists evidence_directness text;

comment on column public.geo_report_analysis_cluster_classifications.evidence_directness is
    'evidence_intelligence_v1: 代表投稿がActor本人の一次発言(Direct)か、伝聞・引用・要約経由(Indirect)かの評価。Unknownも許容。単一Cluster内で判定可能な項目に限定（他Clusterとの独立性はPrompt2側の役割）。';
