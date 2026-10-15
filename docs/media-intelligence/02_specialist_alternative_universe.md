# Media Intelligence Build — M2 Specialist / Alternative Source Universe（Lens別）

依頼書Section4の12 Lensごとに候補を整理する。**Lens 1/2/6/7/11の多くは既に
`docs/geopolitical-source-registry.md`（`geo_source_registry`テーブル、Phase1で登録
済みまたは登録候補）に存在するため、重複登録せず既存Source IDを引用する。**
新規に発見が必要なのはLens 3/5/8/9/10と、Lens4のうち現地語ソース（M1で一部カバー済み）。

## Lens 1 — Official / Primary（既存基盤を最大限再利用）
NATO公式(nato.int) / UN Security Council / 各国大統領府・外務省・国防省公式サイト
（`docs/geopolitical-source-registry.md`4.2「各国一次情報」に登録候補あり、加えて
Actor Source Registry構築時に確認済みのelysee.fr/diplomatie.gouv.fr/
bundesregierung.de/bundeskanzler.de/kantei.go.jp/whitehouse.gov等を再利用）。
**新規収集不要、既存Actor Source Registry・既存geo_source_registryのIDを参照する。**

## Lens 2 — Establishment Strategic Analysis
CSIS / RAND / IISS / Chatham House / Carnegie Endowment / Brookings / RUSI —
米欧の政策コミュニティ本流分析。`geo_source_registry`未登録のため新規候補として
Media Source Registryへ暫定登録（`source_class=specialist`）。

## Lens 3 — Regional / Local Expert（新規発見が必要）
地域大学・現地シンクタンク。例:
```
Israel: INSS(Institute for National Security Studies, Tel Aviv University)
Iran: 直接アクセス可能な独立系現地シンクタンクは限定的（国外のIran専門機関で代替、
      例: Atlantic Council's IranSource）
Russia: Carnegie Russia Eurasia Center（後継組織、旧Carnegie Moscow Center）
China/Taiwan: Taiwan's Institute for National Defense and Security Research(INDSR)
Korea: Sejong Institute, Asan Institute for Policy Studies(韓国)
Gulf: Gulf Research Center
```
現地語オリジナル発信が乏しい地域（特にIran/北朝鮮）は`lens_gap`として明示する
（依頼書Section16）。

## Lens 4 — Local-language Information
M1のRegion別Media Universe自体が現地語ソースの大半を占める（Xinhua/人民日報＝中国語
主体、TASS/RIA＝ロシア語主体、IRNA/Tasnim/Fars＝ペルシャ語主体等）。英語版と国内向け
原文の差分をSignalとして扱うため、`languages[]`フィールドに原文言語を必ず記録する
（M4 Registryで対応）。

## Lens 5 — OSINT / Satellite / Geospatial（新規）
```
Planet Labs(衛星画像、商用API・報道引用で間接利用)
Maxar Technologies(衛星画像、同上)
Bellingcat(OSINT検証報道)
ACLED(Armed Conflict Location & Event Data Project、紛争イベントデータ)
MarineTraffic / Lloyd's List Intelligence(AIS船舶追跡)
```

## Lens 6 — Shipping / Trade / Commodity / Insurance（既存基盤を再利用＋補完）
UN Comtrade / OECD TiVa / BIS Data（`geo_source_registry`既存登録）+
MarineTraffic(AIS、上記Lens5と重複登録、`lens=["osint","shipping"]`の複数タグ)。
`docs/geopolitical-source-registry.md`4.9「maritime_data」は`automation_allowed=false`
のまま契約未確認のプレースホルダとして既存。

## Lens 7 — Sanctions / Illicit Finance / Corporate Network（既存基盤を再利用）
OFAC(`geo_connector_ofac.py`で自動Connector実装済み) / EU Sanctions / UN Security
Council Consolidated List（すべて`geo_source_registry`既存登録）。企業登記・実質的
支配者情報（OpenCorporates等）は新規候補。

## Lens 8 — Conflict / Humanitarian / Civil Society（新規）
```
UCDP(既存geo_source_registry登録済み、Uppsala Conflict Data Program)
ACLED(Lens5と重複)
UNHCR Data Portal(難民・避難民統計)
OCHA(人道データ)
```

## Lens 9 — Opposition / Diaspora / Exile（M1と重複多数）
Meduza / Novaya Gazeta Europe(Russia) / Iran International(Iran) は既にM1へ登録済み。
追加候補: Belarusian opposition media(Nexta等、参考程度)。
**用途はHypothesis Generation/Early Signal/Contradiction Detectionに限定し、
単独ソースでのFact化はしない**（依頼書Section9のdo_not_use_for原則をそのまま適用）。

## Lens 10 — Religious / Ethnic / Tribal / Non-state（新規、カバレッジ限定的）
体系的な情報源の確保が困難な領域。今回のPilot Caseで実際に必要になった場合のみ
個別調査する方針とし、**現時点ではUniverseへの事前登録を見送り、`coverage_gap`として
明示する**（依頼書Section16 lens_gap原則、存在しないSourceを無理に埋めない）。

## Lens 11 — Academic / Historical Area Studies（既存基盤を一部再利用）
V-Dem / World Values Survey（既存`geo_source_registry`登録済み）+
新規: 主要Area Studies機関のワーキングペーパー（個別Case時にWebSearchで発見する運用とし、
恒常的なUniverse登録はしない——学術論文は媒体というよりCase固有のEvidence源のため）。

## Lens 12 — Counterparty / Third-country
Source TypeではなくRetrieval Ruleとして持つ（依頼書Section4末尾の指示通り）。
Media Source Registry上のSourceそのものではなく、Case分析時に「主要当事国以外の視点」
（例: Iran案件でOman/UAE/Turkey側の報道・公式見解）を明示的に検索する運用ルールとして
`docs/media-intelligence/`側にRetrieval Guidelineとして記録する（M8/M9で反映）。

## M2まとめ（新規登録候補数）
Lens2: 6 / Lens3: 6 / Lens5: 5 / Lens6: +1(MarineTraffic、Lens5と重複) /
Lens7: +1(OpenCorporates) / Lens8: +2(UNHCR/OCHA、UCDP・ACLEDはLens5/8重複) /
Lens9: +1(Nexta参考) — 実質新規は約20件、Lens1/6/7/11は既存`geo_source_registry`
の再利用でカバー。
