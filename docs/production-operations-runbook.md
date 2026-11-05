# DEPRECATED / superseded — 本書は参照用です

**正本は [production-scheduling-runbook.md](production-scheduling-runbook.md) です。**

2026-10-06（Phase D.1）、並行セッションで別々に作られた次の2つのRunbookを、実装と照合して1本に統合しました。

- `production-operations-runbook.md`（本ファイルの旧版）
- `production-scheduling-runbook.md`（正本として統合・更新）

旧版の内容のうち、実装と一致する部分（systemd/cronの配備定義、手動tickの`--no-heartbeat`、retry/stale回復の仕様、
日次運用、IMF/World Bankの`params`設定）は正本へ取り込みました。旧版にあった次の記述は**採用していません**:

- 旧`production-scheduling-runbook.md`の「IMF/World Bank設定は `target_entity_id`/`target_country_code` を追記」
  → 実装は `params: {theme_id, entity_id, country_code}`（正本に反映）
- 旧版の「GDELT catch-upは7日の暗黙打ち切りをScheduler側のギャップ計算で回避」
  → 根本原因は「取得に失敗した窓でcheckpointが進むこと」だった（D.1で修正。正本§6・§7）

関連する正本: Design/Change Log `PHASE_D_PRODUCTION_SCHEDULING_2026-10-06.md`／
Completion Report `phase-d-production-scheduling-completion-report-2026-10-06.md`／
Freeze `freeze/PRODUCTION_SCHEDULING_V1/`。
