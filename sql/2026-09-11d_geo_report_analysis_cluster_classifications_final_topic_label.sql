-- Report Studio「Design」領域: geo_report_analysis_cluster_classificationsへ
-- final_topic_label列を追加（Phase D/実運用で発覚した欠落の修正）。
--
-- 背景: Prompt1 Analysis Cluster分類のLLM出力スキーマ（ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA、
-- geo_report_prompt1_classification_service.py）はfinal_topic_labelを必須フィールドとして
-- 返し、geo_report_prompt1_orchestration_service.save_analysis_cluster_classificationは
-- そのままこのテーブルへ書き込む。classified_clusters.csv（Canonical Prompt1 Output、
-- geo_report_prompt1_output_service.build_classified_clusters_rows）もこの列を必須で読む。
-- しかし2026-09-11_geo_report_studio_design_and_current_pipeline.sqlの当初DDLにはこの列が
-- 無く、実行時に「Could not find the 'final_topic_label' column」（PGRST204）で
-- Application AI Prompt1のAnalysis Cluster分類バッチが全件failedになっていた。

alter table public.geo_report_analysis_cluster_classifications
    add column if not exists final_topic_label text;

comment on column public.geo_report_analysis_cluster_classifications.final_topic_label is
    'Prompt1 AnalysisがCluster単位で確定した最終トピックラベル（report_topic_labelのAI最終判定版）。';
