# -*- coding: utf-8 -*-
"""Foundation 段階3: 過去の公的資料の取り込みクエリ（領域別）。

各クエリは「どの公的資料を探すか」だけを書く（事実・結論は書かない）。scripts/foundation_history_ingest.py が
Perplexity（出典つきWeb検索、公的ドメインに限定）で実在するURLを見つけ、本文を取得し、ゲートを通ったものだけ
geo_documents へ登録する。公開日は検索結果のメタデータ、無ければページのメタ情報から取り、信用できない日付は空のまま。

(domain_key, query)  ※ domain_key は結果の集計用。クエリは日本語/英語どちらでもよい。
"""

# 公的ドメインのみを検索対象にする（Perplexityのsearch_domain_filterは最大20）
PUBLIC_DOMAINS = [
    "mofa.go.jp", "mod.go.jp", "kantei.go.jp", "meti.go.jp", "cas.go.jp", "kokkai.ndl.go.jp",
    "state.gov", "whitehouse.gov", "defense.gov", "congress.gov", "treasury.gov", "bis.gov",
    "nato.int", "consilium.europa.eu", "ec.europa.eu", "un.org", "iaea.org", "imf.org", "worldbank.org",
]
PUBLIC_DOMAINS_B = [
    "fmprc.gov.cn", "english.www.gov.cn", "mod.gov.cn", "mnd.gov.tw", "mofa.gov.tw", "president.gov.tw",
    "mfa.gov.il", "gov.il", "mid.ru", "kremlin.ru", "president.gov.ua", "mfa.gov.ua", "gov.uk", "bundesregierung.de",
    "elysee.fr", "international.gc.ca", "pm.gc.ca", "mfa.gov.tr", "mofa.go.kr",
]

QUERIES = [
    # --- 日本・日中・日米・東アジア ---
    ("japan_east_asia", "防衛白書 中国の軍事動向 令和7年版"),
    ("japan_east_asia", "外交青書 2025 日中関係"),
    ("japan_east_asia", "日米首脳共同声明 2025 台湾海峡 尖閣"),
    ("japan_east_asia", "日米安全保障協議委員会 2+2 共同発表 2025"),
    ("japan_east_asia", "日中首脳会談 2025 総理 習近平 戦略的互恵関係"),
    ("japan_east_asia", "日米韓 外相会合 共同声明 2025 経済的威圧"),
    ("japan_east_asia", "通商白書 2025 経済安全保障 重要鉱物 中国の輸出管理"),
    ("japan_east_asia", "国家安全保障戦略 防衛力整備計画 改定 2025"),
    ("china_policy", "China Ministry of Foreign Affairs spokesperson press conference Japan 2025"),
    ("china_policy", "China white paper national security 2025 State Council"),
    ("china_policy", "China export control rare earth announcement Ministry of Commerce 2025"),
    ("china_policy", "Taiwan Affairs Office press conference 2025 Taiwan Strait"),
    ("china_policy", "Taiwan Ministry of National Defense report PLA activity 2025"),
    ("korean_peninsula", "North Korea nuclear policy statement KCNA Kim Jong Un 2025 Russia treaty"),
    ("korean_peninsula", "South Korea US joint statement 2025 alliance summit"),
    # --- 米国（対中・技術・同盟） ---
    ("us_policy", "National Security Strategy United States 2025"),
    ("us_policy", "National Defense Strategy 2025 China Indo-Pacific"),
    ("us_policy", "Bureau of Industry and Security export controls advanced computing semiconductors rule 2025"),
    ("us_policy", "USTR Section 301 China investigation 2025 tariffs"),
    ("us_policy", "US China trade agreement joint statement 2025 Geneva tariffs rare earths"),
    ("us_policy", "Treasury OFAC sanctions Iran oil 2025 designation"),
    ("us_policy", "Congress hearing China select committee 2025 chips export controls"),
    # --- 中東（イラン・イスラエル・湾岸） ---
    ("middle_east", "IAEA Board of Governors report Iran nuclear programme 2025"),
    ("middle_east", "IAEA Director General statement Iran enrichment 2025"),
    ("middle_east", "UN Security Council resolution Iran snapback 2025"),
    ("middle_east", "Strait of Hormuz maritime security advisory 2025"),
    ("middle_east", "Israel Prime Minister statement Iran strikes 2025 ceasefire"),
    ("middle_east", "Saudi Arabia United States defense agreement civil nuclear 2025 joint statement"),
    ("middle_east", "UN Security Council Gaza ceasefire resolution 2025"),
    ("middle_east", "Oman mediation Iran United States talks 2025 statement"),
    # --- ロシア・ウクライナ・欧州・NATO ---
    ("russia_europe", "NATO summit declaration 2025 defence spending 5%"),
    ("russia_europe", "NATO Secretary General press conference Ukraine 2025"),
    ("russia_europe", "European Council conclusions Ukraine 2025 sanctions package"),
    ("russia_europe", "Council of the EU sanctions Russia 2025 package adopted"),
    ("russia_europe", "Ukraine President address negotiations 2025 ceasefire proposal"),
    ("russia_europe", "Kremlin Putin statement Ukraine negotiations 2025"),
    ("russia_europe", "European Commission trade defence 2025 China electric vehicles"),
    ("russia_europe", "German government coalition agreement defence budget 2025"),
    # --- 中南米・カナダ・その他 ---
    ("americas_other", "Venezuela United States sanctions policy statement 2025 Treasury"),
    ("americas_other", "Canada China electric vehicle tariff policy 2025 Prime Minister"),
    ("americas_other", "Canada Alberta referendum independence 2025 federal government"),
    ("americas_other", "BRICS summit declaration 2025"),
    ("americas_other", "G7 leaders statement 2025 critical minerals"),
    ("americas_other", "G20 leaders declaration 2025"),
    ("americas_other", "India foreign ministry statement 2025 China border talks"),
    # --- 歴史的な基礎資料（枠組み・条約・年次文書） ---
    ("history_frameworks", "日中共同声明 1972 外務省 全文"),
    ("history_frameworks", "日中平和友好条約 1978 外務省 全文"),
    ("history_frameworks", "日米安全保障共同宣言 1996 外務省"),
    ("history_frameworks", "Taiwan Relations Act 1979 text"),
    ("history_frameworks", "Shanghai Communique 1972 text State Department"),
    ("history_frameworks", "Joint Comprehensive Plan of Action 2015 text IAEA"),
    ("history_frameworks", "NATO Strategic Concept 2022"),
    ("history_frameworks", "Budapest Memorandum 1994 text United Nations"),
    ("history_frameworks", "Abraham Accords 2020 text State Department"),
    ("history_frameworks", "Treaty of Mutual Cooperation and Security Japan United States 1960 text"),
]
