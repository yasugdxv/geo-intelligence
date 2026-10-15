# Media Intelligence Build — M5〜M8 完了報告(Stop Gate)

M9(Actor Intelligence統合)には進まず、ここで一度停止する。ユーザー指定の16項目を報告する。

## 1. Media Profile Schema

`geo_knowledge_item_schemas.py`の`MEDIA_PROFILE_SCHEMA`(knowledge_type="media_profile")。
必須: `source_id`/`source_name`/`confidence`。安定Intelligence本体
(institutional_perspective/typical_source_types/named_vs_anonymous_tendency/
historical_framing_characteristics/strategic_signaling_relevance/use_for/
do_not_use_for等)を持つ。詳細は[06_media_profile_and_episode_schema.md](06_media_profile_and_episode_schema.md)。

## 2. Media Episode Schema

`MEDIA_EPISODE_SCHEMA`(knowledge_type="media_episode")。依頼書Section12準拠。
`observable_framing`(Observed)と`hypothesized_information_role`(Hypothesis)を分離、
`corroboration_basis`/`corroboration_target`で「出来事の確認」と「意図の確認」を区別。
`MEDIA_PATTERN_SCHEMA`(knowledge_type="media_pattern")も同時実装、`pattern_role`
(default/exception)で矛盾保持を構造的に強制。

**DB Migration**: `geo_knowledge_items_knowledge_type_check`へ3値追加(ユーザー確認・
適用済み、テスト書き込みで動作確認済み)。列追加・データ移行なし。

## 3. Pilotで使用したSource一覧(15件、Episode 1件ずつ)

| Lens | Source |
|---|---|
| Ukraine domestic media | Kyiv Independent, Ukrainska Pravda |
| Ukraine official | Ukrainian Presidential Office(沈黙という観測) |
| Russia official | Kremlin公式スポークスパーソン束(Peskov/Medvedev/Zakharova) |
| Russia independent/exile | Meduza, Novaya Gazeta Europe |
| Global mainstream | Reuters等米国ワイヤー束(WebSearch経由), Euronews press-review |
| European/NATO official | 欧州首脳声明束(Macron/Starmer/von der Leyen/Rutte) |
| Military specialist | ISW |
| OSINT/Conflict data | ACLED |
| Global mainstream(overclaim例) | TIME Magazine |
| Third-country(China) | Global Times + 外交部 |
| Third-country(Turkey) | FDD Turkey分析 |
| Third-country(Saudi) | Saudi Press Agency |

## 4. Lens Coverage

ユーザー指定の10 Lens(Ukraine official/Russia official/Ukraine domestic/Russia
domestic-independent-exile/Global mainstream/European-NATO/Military specialist/
OSINT-physical/Conflict data/Third-country)を**全てカバー**した。唯一「Physical
evidence」固有(衛星画像等)は今回のEpisodeでは直接扱えず、Conflict Data(ACLED)と
Military Specialist(ISW)を代替として使用した(Gap、7章で後述)。

## 5. Zelenskyy Caseの主要Media Observations

- Ukraine大統領府は衝突への直接公式言及を確認できず(通常業務継続の演出)。融和的発言は
  X個人アカウント経由と報じられている。
- Kremlin公式は代理人(Peskov/Medvedev/Zakharova)を通じた強い調子の「ゼレンスキー正当性
  否定」発信に終始し、Putin本人の同時代的発言は確認されず(1年以上後の回顧発言のみ)。
- 欧州首脳(Macron/Starmer/von der Leyen)は即時・実名で連帯表明+具体的コミットメント
  (英国の凍結資産融資等)、NATO事務総長Rutteのみやや異なるトーン。
- 大陸欧州の報道機関は自国首脳の声明より強く感情的な「屈辱・破局」framingを使用。
- TIME誌の「情報共有停止が原因で数百人死亡」という主張は、同時期のISW/ACLEDが明示的に
  留保した因果関係を超える強い主張だった。
- 中国・トルコ・サウジは衝突の是非そのものではなく、自国の既存の戦略的立ち位置の補強に
  今回の局面を利用した。

## 6. Global / Ukraine / Russia / Specialist間のNarrative差

| 視点 | 支配的Framing |
|---|---|
| Global mainstream(米欧ワイヤー) | 「破局」「屈辱」スペクタクル、Trump/Vanceの言動への批判的注目 |
| Ukraine domestic(Kyiv Independent) | 兵士の声を通じた人間化・正当性回復(米保守ナラティブへの対抗) |
| Russia official | ゼレンスキー正当性否定の一貫したvindicationナラティブ |
| Russia独立/亡命系(2媒体) | Meduza=網羅的併載、Novaya Gazeta Europe=露公式反応を意図的か
  結果的か省略、という媒体間の分岐 |
| Military specialist(ISW/ACLED) | 一貫したhedged language、交絡要因の明示 |
| 第三国 | 是非への論評を避け自国ナラティブへ転換(中国=対米不信、トルコ=仲介者、
  サウジ=経済協力) |

## 7. Physical / OSINT Evidenceとの整合・不整合

**核心的発見**: TIME誌の「情報共有停止が原因で数百人のウクライナ兵が死亡」という
Media Narrativeは、同時期のISW(3/5, 3/9)・ACLED(3/1-3/7週次)という独立した2つの
OSINT/専門家ソースが明示的に「時期的相関はnoteworthyだが直接的因果はunclear」と
留保した範囲を超えていた。ISW・ACLEDは方法論的に独立でありながら同じhedged languageに
収斂しており、これは「専門家/OSINTコミュニティ全体の慎重姿勢」対「一般メディアの
より強い因果主張」という明確な不整合として記録した(media_pattern_media_overclaim_vs_specialist_hedge)。

## 8. Historical Media Episodes(15件)

`docs/media-intelligence/`のknowledge_item_id一覧はscratchpad
`media_episode_ids_pilot.json`に保持。主要ID: mep_kyiv_independent, mep_ukrainska_pravda,
mep_ukraine_presidency_silence, mep_kremlin_spokespeople, mep_meduza,
mep_novaya_gazeta_europe, mep_us_wire_mainstream, mep_european_press_bundle,
mep_european_leaders_official, mep_isw_kursk_causation, mep_acled_conflict_data,
mep_time_overclaim, mep_china_global_times_mfa, mep_turkey_mediator_positioning,
mep_saudi_jeddah_hosting。

## 9. 導出したMedia Pattern Candidates(5件)

1. `media_pattern_official_proxy_signaling_under_pressure`(default) — 評判上不利な
   局面での代理人発信/沈黙パターン
2. `media_pattern_media_overclaim_vs_specialist_hedge`(default) — メディアの過大な
   因果主張 vs 専門家/OSINTの留保
3. `media_pattern_press_more_emotive_than_official_statement`(default) — 報道機関 vs
   自国首脳声明のトーン差
4. `media_pattern_exile_media_kremlin_reaction_inclusion_diverges`(exception) —
   同一Lens内での編集判断の分岐
5. `media_pattern_third_country_self_interested_reframing`(default) — 第三国の
   自国ナラティブへの転換

## 10. Contradicting / Exception Episodes

- Pattern1の例外: 欧州首脳声明(`mep_european_leaders_official`)は代理人依存せず即時
  直接発言 — 連帯表明の局面では代理人パターンは生じない。
- Pattern2の例外: ISW/ACLED自身(`mep_isw_kursk_causation`/`mep_acled_conflict_data`)は
  過大主張パターンに該当しない。
- Pattern4はそれ自体が「同一Lens内でも均質でない」という反例保持Pattern
  (Meduza vs Novaya Gazeta Europe)。

## 11. Stable Media IntelligenceがCase Interpretationへ与えた実際の効果

**具体例で確認できた効果**: Global Times Episodeの解釈時、既存Actor Intelligence
Build(Xi Jinping profile)の`strategic_communication_pattern`に既に記録されていた
「Xinhua/人民日報とGlobal Timesを異なる強度のシグナリングチャネルとして使い分ける」
という知見と、今回新規に観測した「外交部は中立的トーン、Global Times記事は対米不信
メッセージ」という具体的挙動が、事前知識なしで解釈するより速く・確度高く整合づけ
られた。**ただしこれはActor Intelligence(既存)がMedia Episode解釈を助けた例であり、
Media Profile自体(今回新設、n=1)がMedia Episode解釈を助けた例ではない**——後者は
今回のPilot(1 Case)だけでは検証できず、複数Case後の再確認が必要。

ISW/ACLEDについては、今回作成した`media_profile`(hedged language傾向を明記)を
先に知っていれば、TIME記事の因果主張を「専門家コミュニティの慎重姿勢からの逸脱」
として即座にフラグ立てできたはずである、という反実仮想的な効果は確認できるが、
実際には今回はEpisode収集→Pattern導出という順序で行ったため、Profile先行による
解釈改善そのものはまだ実証されていない(暫定的な効果推定に留まる)。

## 12. Existing Session2 Media Reasoningとの接続状況

Schema設計レベルでは`observable_framing`/`hypothesized_information_role`/
`corroboration_basis`/`corroboration_target`を既存`media_editorial_context`と
完全に同じ語彙・区分で統一した。**ただし実際のExpert Engine実行(Stage1/Stage2)への
Prompt注入(`_format_media_intelligence_block`相当の関数)はまだ実装していない**
——これはM9の課題であり、意図的に今回のスコープ外とした。

## 13. Quality Guard結果

既存の`_media_observed_fields_free_of_inference_language`等のQuality Guard関数は
`response_json`(実際のExpert Engine応答)を対象に動作するものであり、今回作成した
`media_episode`/`media_pattern`/`media_profile`のknowledge_itemはこれらの自動チェック
対象には**まだ含まれていない**。執筆時に手動でObserved/Hypothesis分離規律・
Beneficiary Guard相当の判断(受益者=関与者と断定しない)を適用したが、自動検証は
未実装。M9統合時に、これらの新規knowledge_typeへも同種のGrounding Checkを拡張するかが
課題として残る(16章参照)。

## 14. Token / Azure / DB Call

**Azure OpenAI呼び出しはゼロ**(M5〜M8はすべてWebFetch/WebSearchによる実地調査＋
決定的Python処理によるDB書き込みのみで、Expert Engineは一度も起動していない)。
DB呼び出し: `create_knowledge_item` 25回(Episode15+Pattern5+Profile5)+検証用
select/delete数回。既存CallBudget(Stage1+Stage2+repair<=3)は今回の作業では適用対象外
(M9でExpert Engineに接続して初めて適用される)。

## 15. Media Intelligence Packの実サイズ

**未計測**。`_format_media_intelligence_block`相当のPrompt注入関数がまだ存在しない
ため、実際にExpert AIへ渡すサイズは測定できない(M9の課題)。参考値として、今回作成した
25件のstructured_content JSONは1件あたり平均約800〜1500文字程度。

## 16. M9 Actor Intelligence統合前に解決すべき課題

1. `_format_media_intelligence_block`の設計とToken予算(既存`ACTOR_PACK_CHAR_BUDGET`
   同様の上限)の決定。
2. 実Caseに対してどのMedia Profile/Episode/Patternを選択するかのRetrieval/Relevance
   ロジック(全25件を毎回注入しない)。
3. Quality Guardの`media_profile`/`media_episode`/`media_pattern`への拡張要否
   (現状はresponse_json専用)。
4. Actor `strategic_communication_pattern.information_channel_relationship_ids[]`
   (現状すべて空)へ、今回作成したsource_idを実際にどう接続するか。
5. **Pattern Candidateはまだ1 Case(Zelenskyy Pilot)のみの根拠であり、複数Caseでの
   再現確認が済んでいない**。少なくとも承認済みのPilot2(高市/台湾Case)を実行し、
   Cross-Case再現性を見てからHuman Reviewへ進めるべきか判断が必要。
6. 全25件は`candidate`のまま。Human Reviewの範囲・担当・時期の決定。
