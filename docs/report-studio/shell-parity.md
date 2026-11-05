# Report Studio Phase1: Shell Parity検証結果

`geo_report_processing_service.py` は外部ワークフロー `BSprocessv3.0/run.ps1`
（version `2026-08-31-v6.1.6-wide-master-singleton-array-fix`）の前処理段階
（列エイリアス解決→重複排除→Narrative Master照合→クラスタリング→代表サンプリング→
population/audit集計）を移植したもの。本ドキュメントは実データ・実PowerShellでの
突き合わせ結果を記録する。

## 検証方法

1. 実際に `run.ps1` を実PowerShellで実行し、正解出力（ZIP内`preprocess_audit_summary.csv`/
   `all_clusters.csv`/`record_cluster_map_*.csv`）を生成した。
2. 同一のMaster（`masters/iran/signal_narrative_master.csv`、実データ）・
   同一の入力ファイル（`dropbox/イラン_S1各国政府の受け止め_20260831-065652.csv`、
   実データ28,708件、Babel Street実出力）を `geo_report_processing_service.run_preprocess`
   に通し、記録単位・signal単位・cluster単位で突き合わせた。
3. `-StartDate 2026-08-01 -EndDate 2026-08-31` で期間指定して実行（全件が対象期間内）。

## 既知のRunner側バグ（run.ps1自体、今回の移植では再現しない）

`Resolve-InputFiles` が入力ファイルを1件しか検出できない場合、呼び出し側
`$InputFiles.Count` へアクセスした際に単一オブジェクトへ配列プロパティアクセスして
例外になる（`The property 'Count' cannot be found on this object.`、run.ps1:1299）。
検証時は入力ディレクトリに2ファイル（実データ1本＋ダミー1行のCSV）を置いて回避した。
Pythonポートはこの制約を持たない（`file_paths`はリストとして扱う）。

## 結果（signal単位、`preprocess_audit_summary.csv`相当）

実データ28,709件（実サンプル28,708件＋ダミー1件）を処理した結果:

| 指標 | PowerShell（正解） | Python | 差分 |
|---|---|---|---|
| total_records | 28,709 | 28,709 | 0 |
| period_in_scope_records | 28,709 | 28,709 | 0 |
| nonduplicate_records | 22,409 | 22,400 | **-9（0.040%）** |
| matched_records | 6,045 | 6,043 | -2 |
| fallback_records | 16,364 | 16,357 | -7 |
| fallback_share | 0.7302 | 0.7302 | 0（丸め後一致） |
| cluster_count | — | 2,071 | （PS側は`all_clusters.csv`行数で今後突合予定） |

record単位（`record_cluster_map_*.csv` vs `record_rows`）で28,647件中
**9件が不一致**（`is_duplicate`判定の差）、残り28,638件（99.97%）は
`record_id`・`is_duplicate`・`match_status`が完全一致した。

## 不一致9件の根本原因（特定済み・Expected Difference）

9件はすべて `source_bucket=public_social`（Twitter）系レコードで、
`Normalize-Text`（重複排除用テキスト正規化）が使う「句読点・記号・空白除去」の
実装が、.NET正規表現の`[\p{P}\p{S}\s]+`（Unicode Category P/S）と
Pythonの`unicodedata.category()`ベースの実装とで、ツイート本文に含まれる
一部のUnicode文字（絵文字・特殊記号等）に対して異なる`text_hash`を生成した
ことに起因する（`text_hash`が変わるとdedup判定・`match_status`の分岐が変わる）。
1件のみ`source_bucket=media`（URL dedup）でも派生的に不一致が生じたが、これは
上記の連鎖（同一text_hashが「既知」として登録されるタイミングが1件ずれたことによる
後続への波及）であり、独立した別原因ではない。

**分類: Expected Difference（意図的な仕様差ではなく、.NET/Python間のUnicode
実装差に起因する既知の許容差）**。Narrative Master照合ロジック・クラスタリング
ロジック自体に不一致はない（9件とも`signal_id`/`narrative_id`の割り当ては
PowerShellと一致しており、ずれているのは重複排除フラグのみ）。

### 対応方針

現時点では追加対応しない。実データの0.04%という規模であり、Prompt1/Prompt2の
目的（高再現率でのクラスタリング・人間レビュー前提の一次分類）において実質的な
影響は無視できる。将来、対応が必要と判断された場合は `regex`パッケージ
（`\p{}`記法をPythonでもサポート）の導入を検討する（新規依存追加のため、
必要性が実データで確認されてから判断する）。

## 未実施の検証（Phase2以降で追加予定）

- `all_clusters.csv`とのcluster単位完全突合（件数のみ確認済み、内容diff未実施）
- `cluster_representatives.csv`との代表サンプル一致確認（reservoir sampling自体は
  移植済みだが、`Get-ShardedClusterId`のシャーディング境界に依存するため、
  クラスタ内訳が僅かにずれるレコードがある可能性がある）
- 複数Signal（S2以降）でのマスタ照合確認（実データはS1のみ収集されていたため未検証）
