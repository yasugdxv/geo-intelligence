# Media Intelligence Build — M3 Source Accessibility Audit

5並列調査Agent（中東/ロシア・ウクライナ/韓国・中台シンクタンク/日本・Global Core残り/
専門家OSINT・シンクタンク）による実WebFetch検証結果と、前タスク（Actor Intelligence
Build）で既に検証済みの結果を統合する。URLの存在確認だけで「利用可能」にしない
（依頼書Section30）方針を徹底。

## 1. 失敗パターンの分類（重要、単純な"blocked"で一括りにしない）

今回の調査で、ブロックには複数の異なる性質があることが判明した:

| パターン | 意味 | 該当例 |
|---|---|---|
| 汎用WebFetchツール失敗("unable to fetch") | ツール側のbot検知の可能性が高い、実際は人間のブラウザからは無料アクセス可能なことが多い | Reuters/AP/BBC/NYT/WSJ/FT/Bloomberg/Politico Europe/Euractiv/独仏紙全般/The Economist/Politico本体/NHK/Yomiuri/Asahi/Mainichi/Sankei/Japan Forward/Kyodo/Yonhap/Chosun/JoongAng/Hankyoreh |
| 明確なHTTP 403 Forbidden | サーバー側の明示的アクセス拒否(WAF)。人間ブラウザでは無料な場合も多い | Al Arabiya, Suspilne, Asan Institute, RAND, ReliefWeb(OCHA), OpenCorporates |
| 503 Service Unavailable(複数パス・robots.txt含め一貫) | WAF/レート制限の疑いが強い | Ukrainska Pravda, Interfax-Ukraine |
| DNS解決失敗(ENOTFOUND)/接続拒否(ECONNREFUSED) | ネットワークレベルの遮断 | `.ru`ドメイン全般(TASS/RIA/Kommersant/RBC)、Tasnim, Fars, Gulf Research Center, Rodong Sinmun |
| TLS/証明書エラー | 証明書信頼チェーンの問題、内容の存否とは別 | Press TV(自己署名証明書), INSS Tel Aviv(handshake failure) |
| 実際のHTTPペイウォール応答(402等) | 本物のアクセス制御であり、ツール側の失敗ではない | Japan Times(402 Payment Required)、CNN(451 Unavailable For Legal Reasons、GDPR地域ブロックと推定) |
| JS-heavyでタイトルしか取れない | ブロックではなく単純HTML取得の技術的限界 | IISS, MarineTraffic |
| 直接到達可、代替ミラー経由 | 一次ソース自体は不可だが第三者アーカイブで代替可 | KCNA(kcna.kp不可) → kcnawatch.org(到達可、第三者運営) |

**したがって「crawlable=false」を一律「価値がない」とは扱わない。** 403/503/DNS拒否の
場合は「人間のブラウザ・別の取得経路（RSS/API/契約）では取得できる可能性がある」ことを
`robots_or_access_notes`に明記し、Media Profile構築時（Phase M5以降）にWebSearchで
の補完・将来的なRSS/API個別検証の余地を残す。

## 2. 地域別サマリー

### Middle East / Israel / Iran / Gulf
| Source | reachable | 失敗パターン | 備考 |
|---|---|---|---|
| Al Jazeera | ○ | — | |
| Al Arabiya | × | 403 | |
| The National | ○ | — | |
| Haaretz | ○ | — | 一部premium paywall |
| Ynet | ○ | — | |
| Times of Israel* | ○ | — | *前タスクで検証済み |
| Jerusalem Post* | ○ | — | *前タスクで検証済み |
| Asharq Al-Awsat* | ○ | — | *前タスクで検証済み |
| IRNA | × | 汎用失敗 | |
| Tasnim | × | DNS(ENOTFOUND) | |
| Fars | × | DNS/ECONNREFUSED | |
| Press TV | × | TLS証明書エラー | |
| Iran International | ○ | — | 国外反体制系 |
| INSS(Tel Aviv) | × | TLS handshake失敗 | |
| Atlantic Council IranSource | ○ | — | |
| Gulf Research Center | × | ECONNREFUSED | |

### Russia / Ukraine
| Source | reachable | 失敗パターン | 備考 |
|---|---|---|---|
| TASS/RIA/Kommersant/RBC | × | `.ru`ネットワーク遮断* | *前タスクで検証済み |
| Meduza/Novaya Gazeta Europe* | ○ | — | *前タスクで検証済み |
| Kyiv Independent* | ○ | — | *前タスクで検証済み |
| Ukrainska Pravda | × | 503(WAF疑い、robots.txtも503) | 2025年10月より一部有料化(裏取り済み) |
| Suspilne | × | 403(robots.txtも403) | |
| Interfax-Ukraine | × | 503(WAF疑い) | 契約制ワイヤーサービス |
| Carnegie Russia Eurasia Center | ○ | — | 旧Carnegie Moscow Center後継 |

### China / Taiwan / Hong Kong
| Source | reachable | 備考 |
|---|---|---|
| Xinhua/人民日報/Global Times/CGTN/Caixin/SCMP* | ○ | *前タスクで検証済み |
| CNA(Focus Taiwan)/Taipei Times* | ○ | *前タスクで検証済み |
| United Daily News/Liberty Times | 未検証 | Gate2以降で個別確認 |
| INDSR(台湾) | ○ | — |

### Korean Peninsula
| Source | reachable | 失敗パターン | 備考 |
|---|---|---|---|
| Yonhap/Chosun Ilbo/JoongAng Ilbo/Hankyoreh | × | 汎用失敗(bot検知疑い) | 4紙とも同一パターン |
| KCNA(kcna.kp) | × | 原因特定不能 | 同一IP帯のRodongはECONNREFUSED確認済み |
| kcnawatch.org(第三者ミラー) | ○ | — | 北朝鮮とは無関係の民間アーカイブ、代替アクセス経路として採用 |
| Rodong Sinmun | × | ECONNREFUSED(明確なネットワーク遮断) | |
| Sejong Institute | ○ | — | |
| Asan Institute | × | 403(明確なアクセス拒否) | |

### Europe / NATO
| Source | reachable | 備考 |
|---|---|---|
| Le Monde/Le Figaro/Der Spiegel/FAZ/Süddeutsche/Welt/Politico Europe/Euractiv* | × | *前タスクで検証済み、すべて汎用失敗 |

### Japan / Global Core残り
| Source | reachable | 失敗パターン | 備考 |
|---|---|---|---|
| NHK | × | 汎用失敗 | |
| Nikkei Asia | ○ | — | freemium |
| Yomiuri(Japan News) | × | 汎用失敗 | 裏取り: 英語版は無料 |
| Asahi(AJW) | × | 汎用失敗 | 裏取り: 英語版は無料、日本語本体は有料化 |
| Mainichi(English) | × | 汎用失敗 | 裏取り: 無料 |
| Sankei | × | 汎用失敗 | 英語版なし(日本語のみ)、Japan Forwardが英語版相当 |
| Japan Forward | × | 汎用失敗 | 裏取り: 基本無料、support会員は寄付制 |
| Kyodo News(英語) | × | 汎用失敗 | 裏取り: 通信社、無料の可能性高い |
| Jiji Press | ○ | — | |
| Japan Times | × | **402 Payment Required(実ペイウォール)** | 月20本まで無料、以降有料 |
| CNN | × | **451(法的/地域ブロックと推定)** | |
| The Economist | × | 汎用失敗 | 裏取り: 月5本無料の従量制 |
| Politico(本体) | × | 汎用失敗 | 裏取り: 登録制(10本まで) |
| Reuters/AP/AFP/Bloomberg/FT/WSJ/NYT/WaPo/BBC* | × | 汎用失敗* | *前タスクで検証済み |

### Specialist — Establishment Strategic Analysis
| Source | reachable | 備考 |
|---|---|---|
| CSIS | ○ | 無料、インタラクティブダッシュボードあり |
| RAND | × | 403(WAF疑い、裏取り: 実際は無料公開95%) |
| IISS | △ | JS-heavyでタイトルのみ取得(ブロックではない)、主要データは有料DB |
| Chatham House | ○ | 無料 |
| Carnegie Endowment | ○ | 無料 |
| Brookings | ○ | 完全無料 |
| RUSI | ○ | 大半無料、Journal等一部会員限定 |

### Specialist — OSINT / Physical / Humanitarian Data
| Source | reachable | 備考 |
|---|---|---|
| Bellingcat | ○ | 無料 |
| ACLED | ○ | api/dataset、高度機能は要登録 |
| MarineTraffic | △ | JS-heavyでタイトルのみ、無料版でも基本機能利用可(裏取り) |
| Planet Labs | ○ | 公開コンテンツは事例紹介のみ、衛星画像本体は有料契約必須 |
| UNHCR Data Portal | ○ | 無料・CC BY 4.0 |
| OCHA/ReliefWeb | × | 403(WAF疑い、裏取り: 本来無料・APIも無料公開) |
| OpenCorporates | × | 403(WAF疑い、裏取り: 基本検索は無料、バルク/APIは有料) |

## 3. Gap（依頼書Section16 lens_gap原則）

- 韓国4大紙・北朝鮮公式2サイトともに直接WebFetchが機能しない。Gate2以降でRSS/API個別
  検証、またはkcnawatch.org型の第三者ミラー活用を検討する。
- Religious/Ethnic/Tribal/Non-state(Lens10)は引き続き体系的ソースなし(M2参照)。
- United Daily News/Liberty Times(台湾)は未検証のまま残存。
