# Design Note: Temporal Actor / Entity State Versioning(未実装、課題整理のみ)

作成日: 2026-09-16。Phase I-B0.1(As-Of Evaluation Harness)の監査で発見された、
本番システム側の構造的な制約を記録する。**今回は実装しない。**

## 背景

`eval_harness.AsOfEvaluationClient`はT0時点の世界知識を再現するプロキシだが、
以下のフィールドは**技術的に再現不能**であることが判明した(履歴バージョニングが
存在しないため):

| 項目 | 現状 | 問題 |
|---|---|---|
| current entity attributes(`geo_entities`の名称・種別等) | `active_from`/`active_to`(存在期間)のみ記録、属性自体は単一の「現在値」 | エンティティの名称・分類が過去に変わっていた場合、T0時点の値を再現できない(実害は比較的小さいと推定——国・組織の識別情報は安定的) |
| actor role / affiliation | `geo_entities`または関連テーブルに、時点ごとの所属・役職の履歴が無い | 「この人物がT0時点でどの役職にあったか」を機械的に再現できない |
| current posture(`geo_knowledge_items(actor_profile).structured_content.current_posture`) | `geo_knowledge_items`自体はバージョニングされる(`created_at`/`supersedes_id`)が、`current_posture`のようなサブフィールドが**行の中で直接上書き(in-place update)される設計になっていないか未検証** | もしin-place更新であれば、T0以前に作られた行でも中身が後日書き換わっている可能性があり、`created_at<=T0`というガードだけでは不十分になる |
| historical validity(妥当性期間) | `geo_knowledge_items`は`valid_from`/`valid_to`を持つが、`geo_relationships`以外の一部テーブル(`geo_entities`)には無い | テーブルによって「妥当性期間」概念の実装度合いがバラバラ |
| source provenance(その値が実際いつ確定したか) | `created_at`はDB投入時点、`as_of_date`は内容が指す時期——両者の関係が全テーブルで一貫していない | Evaluation Harnessが「システムがT0時点で持っていた情報」を再現する際、どちらを主軸にすべきかがテーブルごとに異なる判断を要求される(今回は`geo_knowledge_items`のみ`created_at`を主軸と決めたが、他のテーブルでは検証していない) |

## 将来必要になりうる履歴構造(整理のみ、設計未確定)

1. **Entity属性の完全バージョニング**: `geo_entities`に`geo_knowledge_items`と
   同様の`supersedes_id`/`valid_from`/`valid_to`パターンを導入するか、変更履歴を
   別テーブル(`geo_entity_attribute_history`のようなもの)に分離するか、の
   二択の検討が必要。
2. **`current_posture`等のサブフィールド専用バージョニング**: `geo_knowledge_items`
   の行全体を毎回複製するのは高コストなため、頻繁に更新されるサブフィールド
   (`current_posture`、メディア認識等)だけを別途、時系列で追記可能な構造に
   分離する設計もあり得る。
3. **Provenance統一ポリシー**: 「その情報がシステムに投入された時点」
   (`created_at`/`ingested_at`)と「その内容が指す時期」(`as_of_date`)を、
   全テーブルで一貫した2軸として明示する規約を作る。

## 今回の対応

- 実装は一切行っていない。
- `eval_harness.AsOfEvaluationClient`の`UNSUPPORTED_TEMPORAL_FIELDS`に、上記の
  未対応項目をコードコメントとして記録済み(Evaluation Harness側のlimitationsとして)。
- 本番スキーマ変更は別Issue化を推奨する(このDesign Note自体をそのIssueの出発点
  として使える)。
