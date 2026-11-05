# 地政学ナレッジ＆インテリジェンスDB — Source Registry（Phase 1、Phase 2Aで更新）

対象: `geo_source_registry`（既存テーブルをALTERで拡張。詳細は`docs/geopolitical-data-model.md`4章）

## 1. Phase 1の方針

**Phase 1では自動収集Connectorを実装しなかった。** Source Registryへの登録は手動（画面またはAPI経由）で行い、実際のクロール・API呼び出しはPhase 2で対応する方針とした。Phase 1の目的は「どの情報源を、どんな条件で、誰が、いつ登録したか」を一元管理し、後続のConnector実装とKnowledge Candidate生成時の出典管理の土台を作ることに限定した。

**Phase 2Aでは World Bank / OFAC の2ソースについて自動Connectorを実装した**（5章参照）。取得履歴・成否は`geo_source_registry`の`last_ingestion_run_id`/`last_successful_ingestion_at`/`last_failed_ingestion_at`/`failure_count`（Phase 2Aで追加）で追跡する。詳細: `docs/geopolitical-connector-guide.md`、`docs/geopolitical-ingestion-operations.md`、`docs/geopolitical-freshness-policy.md`。

## 2. Trust Tier定義

| Tier | 定義 | 例 |
|---|---|---|
| A1 | 法律、条約、政府決定、選挙管理機関等の一次情報 | 官報、選挙管理機関公式結果、条約原文 |
| A2 | 政府・国際機関の公式統計 | World Bank, IMF, WTO, 各国統計局 |
| B | 権威ある研究機関・学術データ | SIPRI, V-Dem, UCDP |
| C | 契約済み専門家分析・有料情報 | JCIF, 契約ニュース |
| D | 報道・OSINT・SNS | 一般報道、SNS |
| E | 内部分析・AI仮説 | 専門家AI生成のCandidate |

trust_tierが高い＝常に正しい、ではない。`perspective`（発信主体の立場）・`recency`（新しさ）・`directness`（一次/二次）・`corroboration`（他ソースとの裏付け）を別途`geo_claim_evidence`/`geo_claims`側で管理する。

## 3. 利用条件チェック項目（`geo_source_registry`の対応列）

登録時に以下を必ず確認・記録する。未確認の項目は`automation_allowed`/`content_storage_allowed`/`embedding_allowed`等を`false`のまま残し、Connector実装（Phase 2）を`disabled`にする。

- automation_allowed（自動取得可否）
- content_storage_allowed（原文保存可否）
- embedding_allowed（Embedding生成可否）
- internal_redistribution_allowed（社内共有範囲）
- external_redistribution_allowed（外部への引用・再配布可否）
- license_notes / terms_url
- robots_checked_at

JCIF、Babel Street、契約ニュース、海運・商品データ等、契約条件が未確認のソースは、これらの確認が取れるまで`automation_allowed=false`のまま維持する。

### 3.1 reference_availability（Phase 2B-2で追加、External Reference Modelの第4のゲート）

`automation_allowed`/`connector_enabled`/`credential_configured`（Connectorの自動取得可否）とは独立に、`reference_availability`（`available`/`restricted`、既定`restricted`）は「このSource由来のClaimを、専門家AIがHuman Reviewなしで参照してよいか」を管理する。Claim個別の正誤判定ではなく、Sourceの信頼性・利用条件についての運用判断であり、Steward/管理者がSource単位で1回設定する（`geo_connector_base.set_reference_availability`）。詳細: `docs/geopolitical-external-reference-model.md`。

設定を変更すると、既存Claimのうち`retrieval_status`が`available`/`restricted`のものだけが一括再同期される（`deprecated`/`archived`は個別の除外理由があるため復活させない）。

## 4. 初期登録候補一覧（Phase 1で手動登録可能にする。自動取得はしない）

### 4.1 コア基礎情報
| ID | 情報源 | URL | trust_tier | Phase 1の扱い |
|---|---|---|---|---|
| jcif | Japan Center for International Finance | https://www.jcif.or.jp/index.html | C | 手動登録のみ、契約確認後にPhase2でconnector検討 |
| world_bank_wdi | World Bank Data / WDI | https://data.worldbank.org/ | A2 | 手動登録（Phase2でconnector実装予定） |
| world_bank_api | World Bank Indicators API | https://api.worldbank.org/v2/ | A2 | 手動登録（無認証、Phase2優先実装候補） |
| imf_weo | IMF World Economic Outlook | https://data.imf.org/en/datasets/IMF.RES:WEO | A2 | 手動登録 |
| imf_api | IMF Data API | https://data.imf.org/en/Resource-Pages/IMF-API | A2 | 手動登録（認証要、Phase2でcredential-required connector） |
| wto_data | WTO Data Portal | https://data.wto.org/ | A2 | 手動登録 |
| wto_api | WTO API Developer Portal | https://apiportal.wto.org/ | A2 | 手動登録（API key要） |
| mofa_japan | 日本国外務省 国・地域 | https://www.mofa.go.jp/mofaj/area/index.html | A1 | 手動登録 |

### 4.2 各国一次情報（国ごとに個別登録）
政府公式サイト・大統領府/首相府・外務省・国防省・財務省・中央銀行・統計局・選挙管理機関・議会・官報・政党公式サイト・裁判所・規制当局・国防白書・外交白書。国ごとに`geo_source_registry`へ個別行として登録する運用とし、Phase 1では台湾の既存6ソース（`geo_official_crawler.SOURCES`）に加え新規登録なし。

### 4.3 政治・議会・政党・選挙
| ID | 情報源 | URL | trust_tier |
|---|---|---|---|
| ipu_parline | IPU Parline | https://data.ipu.org/ | A2 |
| international_idea | International IDEA Data & Tools | https://www.idea.int/data-tools | B |
| idea_electoral_system | Electoral System Design Database | https://www.idea.int/data-tools/data/electoral-system-design-database | B |
| idea_turnout | Voter Turnout Database | https://www.idea.int/data-tools/data/voter-turnout-database | B |
| manifesto_project | Manifesto Project | https://manifesto-project.wzb.eu/ | B |
| v_dem | V-Dem | https://v-dem.net/data/ | B |
| v_party | V-Party | https://v-dem.net/data/ | B |

### 4.4 貿易・供給網・金融
| ID | 情報源 | URL | trust_tier |
|---|---|---|---|
| un_comtrade | UN Comtrade | https://comtradeplus.un.org/ | A2 |
| oecd_tiva | OECD Trade in Value Added | https://www.oecd.org/en/tiva.html | A2 |
| bis_data | BIS Data Portal | https://data.bis.org/ | A2 |
| national_central_banks | 各国中央銀行 | 国別設定 | A2 |
| national_statistics | 各国統計局 | 国別設定 | A2 |

### 4.5 軍事・安全保障・紛争
| ID | 情報源 | URL | trust_tier |
|---|---|---|---|
| sipri | SIPRI Databases | https://www.sipri.org/databases | B |
| sipri_milex | SIPRI Military Expenditure | https://www.sipri.org/databases/milex | B |
| sipri_arms | SIPRI Arms Transfers | https://www.sipri.org/databases/armstransfers | B |
| ucdp | UCDP Dataset Download Center | https://ucdp.uu.se/downloads/ | B |
| nato | NATO Official | https://www.nato.int/ | A1 |
| un_security_council | UN Security Council | https://main.un.org/securitycouncil/ | A1 |

### 4.6 エネルギー・資源・チョークポイント
| ID | 情報源 | URL | trust_tier |
|---|---|---|---|
| eia_international | U.S. EIA International | https://www.eia.gov/international/ | A2 |
| eia_chokepoints | World Oil Transit Chokepoints | https://www.eia.gov/international/analysis/special-topics/World_Oil_Transit_Chokepoints | A2 |
| usgs_minerals | USGS National Minerals Information Center | https://www.usgs.gov/centers/national-minerals-information-center | A2 |
| national_energy_sources | 各国エネルギー省・資源省 | 国別設定 | A1 |

### 4.7 制裁・国際法・制度
| ID | 情報源 | URL | trust_tier |
|---|---|---|---|
| un_sanctions | UN Security Council Consolidated List | https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list | A1 |
| ofac | OFAC Sanctions List Service | https://ofac.treasury.gov/sanctions-list-service | A1 |
| eu_sanctions | European Commission Sanctions | https://finance.ec.europa.eu/eu-and-world/sanctions-restrictive-measures_en | A1 |
| eu_sanctions_tracker | EU Sanctions Tracker | https://data.europa.eu/apps/eusanctionstracker/ | A2 |
| unclos | UNCLOS Part III | https://www.un.org/depts/los/convention_agreements/texts/unclos/part3.htm | A1 |
| un_treaties | UN Treaty Collection | https://treaties.un.org/ | A1 |

### 4.8 社会・価値観・世論
| ID | 情報源 | URL | trust_tier |
|---|---|---|---|
| world_values_survey | World Values Survey | https://www.worldvaluessurvey.org/ | B |
| official_pollsters | 各国の公的・信頼できる世論調査 | 国別設定 | B |
| census | 各国国勢調査 | 国別設定 | A2 |

### 4.9 契約・内部情報（Phase 1では登録のみ、automation_allowed=false固定）
| ID | 情報源 | trust_tier |
|---|---|---|
| babelstreet | Babel Street | C |
| licensed_media | 契約ニュース・データベース | C |
| maritime_data | AIS・船舶・港湾データ | C |
| commodity_data | 原油・ガス・穀物・金属等 | C |
| internal_expert_material | 専門家インタビュー・過去レポート | C |

## 5. Connectorの実装状況（Phase 2A・Phase 2Bで更新）

### 5.1 実装済み（自動取得）

**無認証（Phase 2A）**
- **World Bank Indicators Connector**（`geo_connector_world_bank.py`） — `trust_tier=A2`、`access_method=public_api`。詳細: `docs/geopolitical-connector-guide.md`2.1
- **OFAC Sanctions List Connector**（公開CSV、`geo_connector_ofac.py`） — `trust_tier=A1`、`access_method=public_csv`。SDN/ALT/ADD（住所）CSVに対応（2026-08-19追加）。国籍・身分証明書番号は単純CSVでは提供されず`sdn_advanced.xml`のみに含まれるため別タスクとして持ち越し（`citizen.csv`/`id.csv`というファイルは実在しない）。詳細: `docs/geopolitical-connector-guide.md`2.2

**無認証（Phase 2B）**
- **IMF DataMapper API Connector**（`geo_connector_imf.py`） — `trust_tier=A2`、`access_method=public_api`。認証要否を実アクセスで検証済み。詳細: `docs/geopolitical-connector-guide.md`2.3

**Credential-required（Phase 2B、`connector_credentials`にAPIキー未設定の間は`credential_configured=false`により無効）**
- **UN Comtrade API Connector**（`geo_connector_un_comtrade.py`） — `access_method=public_api_credential_required`。詳細: `docs/geopolitical-connector-guide.md`2.4
- **WTO Timeseries API Connector**（`geo_connector_wto.py`） — 詳細: `docs/geopolitical-connector-guide.md`2.5
- **Manifesto Project Database Connector**（`geo_connector_manifesto.py`、Main Datasetのみ） — `trust_tier=B`。詳細: `docs/geopolitical-connector-guide.md`2.6

Generic CSV/Excel/JSON Connector（Phase 1で実装済み・手動import）は継続利用。Source Registryの3ゲート分離（`automation_allowed`/`connector_enabled`/`credential_configured`）は`docs/geopolitical-connector-guide.md`1.1章参照。

### 5.2 Phase 2B次ラウンド以降に持ち越すConnector

Document Upload Connector（既存拡張）、Generic Authorized HTTP Connector（allowlist方式）、Babel Street（商用）、OFACの国籍・身分証明書番号取り込み（`sdn_advanced.xml`）、Manifesto ProjectのCorpus全文取り込み・chunking。

## 6. Phase 2B-2で追加

External Reference Model（Source Reference Policy、Claim個別のRetrieval Governance）、Internal Expert Learning Layer（内部専門家レビュー用の固定Source、`trust_tier=E`）を追加。詳細: `docs/geopolitical-external-reference-model.md`、`docs/geopolitical-internal-expert-learning.md`。
