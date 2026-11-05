# P2 Issue: Workflow Board — Evaluation / Test Session Segregation

## 問題

`GET /api/geo/ios/workflow-board`（`geo_ios_workflow_board_service.list_board_cases`）は
`geo_ios_analysis_session_service.list_sessions`が返す全Analysis Sessionをそのまま束ねる。
このため、Phase I-B0 Retrospective Proxy Evaluation（Hungary/BC Strategy/Iran・Hormuz/
US-China）で作成したAnalysis Session群が、実運用（Operational）案件と同じBoard上に
混在して表示される。

## 影響

- 現時点ではSecurity上の問題ではない（表示される情報の範囲はrole別Authorizationで
  正しく絞り込まれている——§1参照）。
- UX上の問題として、Approver/Knowledge Steward等が実運用案件を探す際に、退役済みの
  評価用Sessionが紛れ込む（実際の承認/公開対象ではないものが承認待ち列等に見える
  可能性がある）。

## 今回は対応しない理由

Phase I-B0シリーズは`5e703e1`/`931e5d7`でCOMPLETE/FROZENとして確定済みであり、今回の
Targeted Security Checkのスコープ（新規read-only APIの認可）にEvaluation Session自体の
表示ロジック変更は含まれない。title prefix等のheuristic filterをここで追加することは
しない（脆く、将来の命名規則変更で静かに壊れるため）。

## 将来の対応方針（記録のみ）

`geo_ios_analysis_sessions`に明示的なフィールド（例: `session_purpose` =
`operational` / `evaluation` / `test`）を追加し、Board側はデフォルトで
`session_purpose = operational`のみを表示する設計にする。Phase I-B0のような
Retrospective Evaluation実行時は、Session作成時点で明示的に`evaluation`を設定する。

| category | problem | impact | recommended_action | priority | status |
|---|---|---|---|---|---|
| Evaluation/Test Session Segregation | Workflow BoardがOperational SessionとPhase I-B0 Retrospective Evaluation Sessionを区別せず束ねる | 中（Security影響なし、UXのみ。運用案件探索時にEvaluation Sessionが紛れ込む） | `geo_ios_analysis_sessions`に`session_purpose`（operational/evaluation/test）フィールドを追加し、Board既定表示をoperationalのみに絞る | **P2** | 未対応（設計課題として記録のみ、heuristic filterは追加しない） |
