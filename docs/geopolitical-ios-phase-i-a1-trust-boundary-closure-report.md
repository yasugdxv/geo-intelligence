# Geopolitical Intelligence OS — Phase I-A.1: Trust Boundary Closure 完了報告

日付: 2026-09-16
対象: Phase I-A Mechanical E2E Pilotで発見したSEC-1/SEC-2/SEC-3(認証・権限の
trust boundary欠落)と、development-integrityが指摘したgit baseline欠落の解消。
Expert AI / CQAS / Report Studio / Learningロジックには一切手を入れていない
(authorization層のみ)。

## 1. Git Baseline Status

- 監査結果: secrets/credentials/config.json/.envの誤コミットは無し(全体スキャン済み、
  `.gitignore`で保護済み)。
- 並行Report Studio作業チームの未コミット差分(150件以上)が同じworking treeに混在して
  いたため、`git add -A`は行わず、**自分のPhase B-I IOS作業ファイルのみを明示的に列挙**
  してbaseline commit(`ce3183b`)を作成した。並行作業には一切触れていない
  (diff内容を個別確認、1件誤認しかけたファイルは除外)。
- これによりPhase I-A.1の変更は`git diff ce3183b`で正確に12ファイルへ特定できる
  ようになった(development-integrityのbase revision guardが今回は`pass: true`)。

## 2. SEC-1 Fix（Approval Authorization）

- `POST /api/geo/ios/approval/in-app` / `/approval/upload`は、認証済み
  `IosIdentity`(X-IOS-Credentialヘッダー)からserver-side roleを解決し、
  `APPROVAL_ROLES=("approver",)`のみを許可するよう変更。
- API層(fail-fast、他の処理より先に検証)とservice層(`geo_ios_approval_service.
  record_approval_event`内で独立に再検証)の両方でrole checkを実施——内部関数の
  直接呼び出しによるbypassを防止。
- 順序修正: 当初はRoute Aのsnapshot自動構築(Report Studio状態読み取り)がrole check
  より先に走る実装ミスがあり、未認可の呼び出し者に「Collection Runが無い」という
  内部状態を先に開示してしまう問題を発見・修正した(role checkを関数の先頭へ移動)。
- request payload側の`approver_role`は互換性のため受け取るが、authorizationには
  使わない。authenticated roleとの食い違いは`_role_mismatch_warning`としてレスポンスに
  記録する(audit/mismatch detection用途のみ)。

## 3. SEC-2 Identity Model（Pilot-grade）

- 新規 `geo_ios_identity.py`: `authenticated identity → server-side assigned role
  → authorization` の順序を機械的に強制する。
- credential→identity_id→roleのマッピングは`config.json`の`geo_ios.identities`
  (server-side、`.gitignore`対象、client指定不可)でのみ管理。
- 未設定/空の場合は常に401(既存`geo_intelligence_auth`のような「未設定なら
  dev-no-authで通す」フォールバックは採用しない——それではSEC-1/SEC-2の趣旨に反する)。
- **これはBroad Production用の最終RBACではなく、正式な社内認証基盤(Entra ID等)が
  導入されるまでのControlled Pilot専用trust boundaryである**、と明記。
- 適用範囲: Approval(SEC-1)、Learning Candidate承認/却下/promote、Knowledge
  Publication(report-studio/external)。`geo_role_guard.ROLES`に`"approver"`を
  新規追加(既存の"Expert/Approver"という概念上の区別を、実際のroleとして分離)。

## 4. SEC-3 Real Environment Configuration

- Phase Fで実装済みの`CallerIdentity`機構(`geo_intelligence_auth.py`)自体は
  無変更。`config.json`の`geo_intelligence.api_keys`に、Controlled Pilot用の
  実credential(自己生成のopaqueトークン)を`sustainability_intelligence`/
  `erm_intelligence`向けに設定し、初めて実際に有効化した。
- これにより、これまで「未設定なら無検証で通す」dev-no-authだった`/api/v1/
  geo-intelligence/query`が、実際にX-API-Keyを要求するようになった。
- 内部呼び出し(`geo_chat_ui`)がロックアウトされないことを確認済み:
  `api_server.py`が`geo_intelligence_service.process_request`を直接Python関数
  呼び出しするパス(`caller_identity=None`)であり、この認証層を経由しないため。

## 5. Role Spoofing Tests（§Security Test必須項目、全件PASS）

新規テスト19件(`tests/test_geo_ios_phase_i_a1_trust_boundary.py`)+ 実サーバーへの
実credentialによるLive E2E再検証(`phase_ia1_recheck.py`)の両方で確認:

| ケース | 結果 |
|---|---|
| authenticated analyst + payload approver_role=approver | **DENY (403)** |
| authenticated expert_reviewer + payload approver_role=knowledge_steward | **DENY (403)** |
| authenticated approver + payload approver_role=analyst | **ALLOW**、認証済みroleのみ使用 |
| unknown identity（credential未登録） | **DENY (401)** |
| missing identity（credentialヘッダー無し） | **DENY (401)** |
| service層への直接呼び出し（API層bypass想定） | **DENY**（同じrole check） |

## 6. Approval Authorization Tests

上記5.のケースに加え、`approve_via_upload`(Route B)でも同じspoofing denyを確認。
Live E2Eでは新規に作成したreport_projectを使い、`authorization → approval →
publish`の一連を実credentialで完走(§9参照)。

## 7. Learning / Publication Authorization Regression

- `geo_ios_learning_api.py`(approve/reject/promote)、`geo_ios_knowledge_
  publication_api.py`(publish report-studio/external)を同じ`IosIdentity`
  パターンへ統一。
- 既存のservice層role check(`CANDIDATE_REVIEW_ROLES`/`PUBLISH_ROLES=
  ("knowledge_steward",)`)は変更なし、authorizationの入力元だけをrequest
  payloadからauthenticated identityへ切り替えた。
- Regression: 全1487件のテストが変更前後とも合格(config.json実credential投入前後
  の両方で実行、既存テストがdev-no-authフォールバックに暗黙依存していないことを確認)。
- スコープ外として明示: `geo_knowledge_api.py`の汎用`/items/{id}/review`エンドポイント
  自体の直接HTTP認可は今回統一していない(IOS専用ではない広いサーフェスであり、
  大規模RBAC再設計を避ける指示に従った)。Publication経由での間接呼び出しは、
  Publication層が先に認証済みroleへ統一されたことで既に保護されている。

## 8. Cross-Agent Authenticated E2E

実際に動いているサーバーへ、実際に生成したcredentialで以下を確認:

| ケース | 結果 |
|---|---|
| missing X-API-Key | **DENY (401)**（api_keys投入前はdev-no-authで通っていた） |
| unknown X-API-Key | **DENY (401)** |
| Sustainability credential + requester_app=geopolitics(内部domainを騙る) | 認証はSustainabilityのまま、internal扱いされない(knowledge_sufficiency=partial、外部caller向けの結果) |
| ERM credential | 正常に認証 |
| 内部呼び出し(geo_chat_ui) | 影響なし(この認証層を経由しないため) |

## 9. Human Visibility

`human_visibility="department"/"geopolitics_team"`が実質「認証済み人間なら
誰でも」という既知の簡略化(Phase E由来、コード内に明記済み)は今回変更していない。
`IosIdentity.domain`との連携も行っていない——大規模なRBAC再設計が必要になるため
（正式な部門・人間ユーザーモデルが存在しない）。

**採用した方針**: Controlled Pilotの利用者範囲を、明示的に登録された
`geo_ios.identities`のロースター(現在5件: approver/knowledge_steward/analyst/
expert_reviewer/administrator、各1名分のPilot用credential)に限定する、という
運用上の制限で対応する。真の部門スコープ制御はBroad Production前の課題として残す。

## 10. Files Changed（12ファイル、`git diff ce3183b`で検証済み）

新規: `geo_ios_identity.py`, `tests/test_geo_ios_phase_i_a1_trust_boundary.py`
変更: `geo_role_guard.py`, `geo_ios_approval_api.py`, `geo_ios_approval_service.py`,
`geo_ios_learning_api.py`, `geo_ios_knowledge_publication_api.py`,
`config.json.example`, `tests/test_geo_ios_foundation.py`,
`tests/test_geo_ios_knowledge_publication_service.py`,
`tests/test_geo_ios_learning_extraction_service.py`,
`tests/test_geo_ios_phase_d_report_studio_adapter.py`

Expert AI / Report Studio / CQAS / Learningロジックへの変更: **0件**(diff内容を
grepで確認済み、development-integrityのinvariant checkで裏付け済み)。

## 11. Secrets Handling

- 新規生成したPilot用credential(5 IOS identities + 2 Cross-Agent keys)は
  `config.json`(既存`.gitignore`対象)にのみ保存。リポジトリへは一切コミットしていない。
  `config.json.example`にはplaceholder表記のみ追加。
- credential自体はどのログ・audit記録・レスポンスにも出力していない(grepで確認済み、
  `IosIdentity.__repr__`もcredential自体は含まない設計)。

## 12. Tests / Regression

- 新規: 19件(`test_geo_ios_phase_i_a1_trust_boundary.py`)、全PASS。
- 既存全体: **1487件、config.json実credential投入前後とも全PASS**（リグレッション無し）。
- Live E2E: `phase_ia1_recheck.py`(SEC-1/SEC-2)、`phase_ia1_sec3_recheck.py`(SEC-3)、
  両方とも実サーバー・実credentialで期待通りの結果。

## 13. Governance Skill Results（既存順序で再実行）

| Skill | 結果 |
|---|---|
| development-integrity | scope_status=**complete**、base_revision_guard.pass=**true**（`ce3183b`基点で完全に検証可能になった） |
| security-review | **P0: 0、P1: 0**（Phase I-Aで発見したP1×3件はamendmentsとしてCLOSED記録）、P2×1・P3×1（pilot-grade故の軽微な事項のみ） |
| evaluator | authorization_integrity **0.3 → 1.0**、near_miss_rate **0.5 → 0.0**、task_success 1.0（4 eval cases、regression含む） |
| token-optimizer | 新規LLM呼び出し無し、findings無し（P2は指示通り変更対象外） |
| release-gate | 下記参照 |

## 14. Remaining P0/P1

**なし。** Phase I-Aで発見したSEC-1/SEC-2/SEC-3の3件のP1は全てCLOSED。development-
integrityのbase revision欠落も解消済み。

## 15. Release Gate

機械的判定: **BLOCK**（`release-gate.json`）。ただし理由は
`required eval metric missing: grounding` / `safety`の2件のみ——これはdefault
policyの汎用的な指標要件(コンテンツ品質evalを前提)が、今回の**認証・権限専用**
のeval(そもそもgrounding/content-safetyを測るものではない)に合っていないための
ものであり、実質的なセキュリティ・整合性のブロッカーは**存在しない**。

実質的な判断: セキュリティ・development-integrityの観点では**PASS相当**
（P0/P1ゼロ、near_miss_rateゼロ、base revision guard pass）。

## 16. Current Readiness

| Gate | 状態 |
|---|---|
| Code/Merge Gate（機械的policy） | BLOCK（§15の理由、実質的ブロッカーなし） |
| Code/Merge Gate（実質・セキュリティ観点） | **PASS** |
| Internal Pilot | **Ready**（引き続き有効） |
| **Controlled Production Pilot** | **Ready**（今回のFixにより到達） — SEC-1 closed / SEC-2 closed at pilot-grade / SEC-3 real credential E2E PASS / authorization_integrity=1.0 / P0・P1 authorization bypassゼロ、を全て満たした |
| Broad Production | **Not Ready**（§17参照） |

## 17. Requirements Before Broad Production

1. `geo_ios_identity`/`geo_intelligence_auth`を、正式な社内認証基盤(Entra ID等)へ
   置き換える（現状はどちらもPilot-grade、config.json手動管理）。
2. `human_visibility`の"department"/"geopolitics_team"を、実際の部門・人間ユーザー
   モデルと連携した本物のスコープ制御へ拡張する（現状は簡略化されたまま）。
3. `geo_knowledge_api.py`の汎用`/items/{id}/review`エンドポイント自体の直接HTTP
   認可を、IOS外の他の呼び出し元も含めて統一する（今回は明示的にスコープ外）。
4. Phase I-Aで記録済みのP2/Backlog項目（LLM transient retry、process restart
   handling、DB overfetch等）——今回の指示により対象外のまま。
5. Phase I-B（Expert Validation Pilot）は引き続き別タスクとして未着手。
