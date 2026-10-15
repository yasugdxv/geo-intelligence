# -*- coding: utf-8 -*-
"""Foundation 段階2「骨格」の種リスト（名前だけ。各項目はスクリプトがWikidataで裏付けを取ってから登録する）。

(canonical_name, wikidata_english_label, entity_type, 補足)
- wikidata_english_label: Wikidataでの英語ラベル（検索語。一致しない項目は登録しない）。
- 補足: 国際機関は {"domain": 関係ドメイン, "abbr": [略称]}、省庁・組織は {"parent": 親の国のcanonical_name, "abbr": [...]}。
人物は時点で変わるため含めない（既存のActor Profile側で扱う）。
"""

COUNTRIES = [
    "United States", "China", "Japan", "Russia", "Taiwan", "South Korea", "North Korea", "India", "Iran",
    "Israel", "Saudi Arabia", "United Arab Emirates", "Qatar", "Oman", "Turkey", "Egypt", "Iraq", "Syria",
    "Lebanon", "Yemen", "Jordan", "Kuwait", "Bahrain", "Pakistan", "Afghanistan", "Ukraine", "Belarus",
    "Poland", "Germany", "France", "United Kingdom", "Italy", "Spain", "Netherlands", "Hungary", "Greece",
    "Finland", "Sweden", "Norway", "Denmark", "Switzerland", "Belgium", "Canada", "Mexico", "Brazil",
    "Argentina", "Venezuela", "Cuba", "Colombia", "Australia", "New Zealand", "Philippines", "Vietnam",
    "Indonesia", "Thailand", "Malaysia", "Singapore", "Myanmar", "Kazakhstan", "Uzbekistan", "South Africa",
    "Nigeria", "Ethiopia", "Kenya", "Algeria", "Libya", "Sudan", "Mongolia", "Azerbaijan", "Armenia",
    "Georgia", "Serbia", "Bangladesh",
]
# 表示名が一般的な英語ラベルと違うもの
COUNTRY_CANONICAL = {"Turkey": "Türkiye"}

# 国際機関: canonical, Wikidata英語ラベル, domain(加盟関係のドメイン), 略称
ORGS = [
    ("North Atlantic Treaty Organization", "NATO", "security", ["NATO"]),
    ("European Union", "European Union", "diplomacy", ["EU"]),
    ("United Nations", "United Nations", "diplomacy", ["UN"]),
    ("Group of Seven", "G7", "diplomacy", ["G7"]),
    ("Group of Twenty", "G20", "diplomacy", ["G20"]),
    ("BRICS", "BRICS", "diplomacy", []),
    ("Shanghai Cooperation Organisation", "Shanghai Cooperation Organisation", "security", ["SCO"]),
    ("Association of Southeast Asian Nations", "ASEAN", "diplomacy", ["ASEAN"]),
    ("Organization of the Petroleum Exporting Countries", "Organization of the Petroleum Exporting Countries", "energy", ["OPEC"]),
    ("World Trade Organization", "World Trade Organization", "trade", ["WTO"]),
    ("International Atomic Energy Agency", "International Atomic Energy Agency", "legal", ["IAEA"]),
    ("International Monetary Fund", "International Monetary Fund", "finance", ["IMF"]),
    ("World Bank", "World Bank", "finance", []),
    ("Arab League", "Arab League", "diplomacy", []),
    ("Gulf Cooperation Council", "Cooperation Council for the Arab States of the Persian Gulf", "diplomacy", ["GCC"]),
    ("Collective Security Treaty Organization", "Collective Security Treaty Organization", "security", ["CSTO"]),
    ("Eurasian Economic Union", "Eurasian Economic Union", "trade", ["EAEU"]),
    ("African Union", "African Union", "diplomacy", ["AU"]),
    ("Mercosur", "Southern Cone Common Market", "trade", []),
    ("AUKUS", "AUKUS", "security", []),
    ("Quadrilateral Security Dialogue", "Quadrilateral Security Dialogue", "security", ["Quad"]),
    ("Five Eyes", "Five Eyes", "security", []),
    ("Comprehensive and Progressive Agreement for Trans-Pacific Partnership",
     "Comprehensive and Progressive Agreement for Trans-Pacific Partnership", "trade", ["CPTPP"]),
    ("Regional Comprehensive Economic Partnership", "Regional Comprehensive Economic Partnership", "trade", ["RCEP"]),
    ("United States–Mexico–Canada Agreement", "United States–Mexico–Canada Agreement", "trade", ["USMCA"]),
    ("Organisation for Economic Co-operation and Development", "Organisation for Economic Co-operation and Development", "diplomacy", ["OECD"]),
    ("Asia-Pacific Economic Cooperation", "Asia-Pacific Economic Cooperation", "trade", ["APEC"]),
    ("Organization for Security and Co-operation in Europe", "Organization for Security and Co-operation in Europe", "security", ["OSCE"]),
]

# 省庁・政府機関・軍: canonical, Wikidata英語ラベル, entity_type, 親の国, 略称
GOVERNMENT_BODIES = [
    ("Ministry of Foreign Affairs (Japan)", "Ministry of Foreign Affairs", "ministry", "Japan", ["MOFA"]),
    ("Ministry of Defense (Japan)", "Ministry of Defense", "ministry", "Japan", ["MOD"]),
    ("Ministry of Economy, Trade and Industry (Japan)", "Ministry of Economy, Trade and Industry", "ministry", "Japan", ["METI"]),
    ("Japan Self-Defense Forces", "Japan Self-Defense Forces", "military", "Japan", ["JSDF"]),
    ("Ministry of Foreign Affairs (China)", "Ministry of Foreign Affairs of the People's Republic of China", "ministry", "China", []),
    ("Ministry of National Defense (China)", "Ministry of National Defense of the People's Republic of China", "ministry", "China", []),
    ("Ministry of Commerce (China)", "Ministry of Commerce of the People's Republic of China", "ministry", "China", ["MOFCOM"]),
    ("People's Liberation Army", "People's Liberation Army", "military", "China", ["PLA"]),
    ("Chinese Communist Party", "Chinese Communist Party", "political_party", "China", ["CCP"]),
    ("Ministry of National Defense (Taiwan)", "Ministry of National Defense", "ministry", "Taiwan", []),
    ("Democratic Progressive Party", "Democratic Progressive Party", "political_party", "Taiwan", ["DPP"]),
    ("Kuomintang", "Kuomintang", "political_party", "Taiwan", ["KMT"]),
    ("United States Department of State", "United States Department of State", "ministry", "United States", []),
    ("United States Department of Defense", "United States Department of Defense", "ministry", "United States", ["DoD"]),
    ("United States Department of the Treasury", "United States Department of the Treasury", "ministry", "United States", []),
    ("United States Department of Commerce", "United States Department of Commerce", "ministry", "United States", []),
    ("Office of the United States Trade Representative", "Office of the United States Trade Representative", "ministry", "United States", ["USTR"]),
    ("National Security Council (United States)", "National Security Council", "government", "United States", ["NSC"]),
    ("United States Congress", "United States Congress", "government", "United States", []),
    ("United States Armed Forces", "United States Armed Forces", "military", "United States", []),
    ("Central Intelligence Agency", "Central Intelligence Agency", "security_service", "United States", ["CIA"]),
    ("Ministry of Foreign Affairs (Russia)", "Ministry of Foreign Affairs of the Russian Federation", "ministry", "Russia", []),
    ("Ministry of Defence (Russia)", "Ministry of Defence of the Russian Federation", "ministry", "Russia", []),
    ("Russian Armed Forces", "Russian Armed Forces", "military", "Russia", []),
    ("Ministry of Foreign Affairs (Iran)", "Ministry of Foreign Affairs of Iran", "ministry", "Iran", []),
    ("Islamic Revolutionary Guard Corps (IRGC)", "Islamic Revolutionary Guard Corps", "military", "Iran", ["IRGC"]),
    ("Israel Defense Forces", "Israel Defense Forces", "military", "Israel", ["IDF"]),
    ("Hamas", "Hamas", "armed_group", None, []),
    ("Hezbollah", "Hezbollah", "armed_group", "Lebanon", []),
    ("Houthis", "Houthis", "armed_group", "Yemen", []),
    ("European Commission", "European Commission", "government", None, []),
    ("European Parliament", "European Parliament", "government", None, []),
    ("Ministry of Defence (United Kingdom)", "Ministry of Defence", "ministry", "United Kingdom", []),
    ("Korean People's Army", "Korean People's Army", "military", "North Korea", []),
    ("Workers' Party of Korea", "Workers' Party of Korea", "political_party", "North Korea", []),
]

# 企業: canonical, Wikidata英語ラベル, entity_type, 親の国
COMPANIES = [
    ("Taiwan Semiconductor Manufacturing Company", "TSMC", "company", "Taiwan"),
    ("Nvidia", "Nvidia", "company", "United States"),
    ("Huawei", "Huawei", "company", "China"),
    ("Semiconductor Manufacturing International Corporation", "Semiconductor Manufacturing International Corporation", "company", "China"),
    ("ASML", "ASML Holding", "company", "Netherlands"),
    ("Samsung Electronics", "Samsung Electronics", "company", "South Korea"),
    ("Saudi Aramco", "Saudi Aramco", "state_owned_enterprise", "Saudi Arabia"),
    ("Gazprom", "Gazprom", "state_owned_enterprise", "Russia"),
    ("A.P. Moller–Maersk", "Maersk", "company", "Denmark"),
    ("Toyota", "Toyota", "company", "Japan"),
]

# 地理・戦略資産: canonical, Wikidata英語ラベル, entity_type
LOCATIONS = [
    ("Strait of Hormuz", "Strait of Hormuz", "strait"),
    ("Taiwan Strait", "Taiwan Strait", "strait"),
    ("Strait of Malacca", "Strait of Malacca", "strait"),
    ("Bab-el-Mandeb", "Bab-el-Mandeb", "strait"),
    ("Suez Canal", "Suez Canal", "canal"),
    ("Panama Canal", "Panama Canal", "canal"),
    ("South China Sea", "South China Sea", "geographic_location"),
    ("Senkaku Islands", "Senkaku Islands", "geographic_location"),
    ("Red Sea", "Red Sea", "geographic_location"),
    ("Black Sea", "Black Sea", "geographic_location"),
    ("Arctic", "Arctic", "geographic_location"),
]

# 二国間の条約・同盟・共同声明（締結日を持つ事実）。AIが出典付きで2回検証し、両方通ったものだけ登録する。
# (id, from, to, domain, relationship_type, formal_basis, valid_from(発効/締結日), 検証用の問い, mutual)
BILATERAL_FACTS = [
    ("us_japan_security", "United States", "Japan", "security", "mutual_defense_treaty",
     "Treaty of Mutual Cooperation and Security between the United States and Japan", "1960-06-23",
     "日米安全保障条約（新条約）はいつ署名され、いつ発効したか。", True),
    ("us_rok_mdt", "United States", "South Korea", "security", "mutual_defense_treaty",
     "Mutual Defense Treaty between the United States and the Republic of Korea", "1954-11-17",
     "米韓相互防衛条約はいつ署名され、いつ発効したか。", True),
    ("us_philippines_mdt", "United States", "Philippines", "security", "mutual_defense_treaty",
     "Mutual Defense Treaty between the United States and the Republic of the Philippines", "1952-08-27",
     "米比相互防衛条約はいつ署名され、いつ発効したか。", True),
    ("us_taiwan_tra", "United States", "Taiwan", "security", "us_domestic_law_commitment",
     "Taiwan Relations Act", "1979-04-10",
     "台湾関係法（Taiwan Relations Act）はいつ制定されたか。", False),
    ("china_russia_friendship", "China", "Russia", "diplomacy", "friendship_treaty",
     "Treaty of Good-Neighborliness and Friendly Cooperation", "2001-07-16",
     "中ロ善隣友好協力条約はいつ署名されたか。", True),
    ("china_dprk_treaty", "China", "North Korea", "security", "mutual_assistance_treaty",
     "Sino-North Korean Mutual Aid and Cooperation Friendship Treaty", "1961-07-11",
     "中朝友好協力相互援助条約はいつ署名されたか。", True),
    ("russia_dprk_treaty", "Russia", "North Korea", "security", "comprehensive_strategic_partnership_treaty",
     "Comprehensive Strategic Partnership Treaty", "2024-06-19",
     "ロシアと北朝鮮の包括的戦略パートナーシップ条約はいつ署名されたか。", True),
    ("russia_belarus_union", "Russia", "Belarus", "political_support", "union_state_treaty",
     "Treaty on the Creation of a Union State", "1999-12-08",
     "ロシア・ベラルーシ連合国家創設条約はいつ署名されたか。", True),
    ("russia_iran_partnership", "Russia", "Iran", "diplomacy", "comprehensive_strategic_partnership_treaty",
     "Comprehensive Strategic Partnership Treaty", "2025-01-17",
     "ロシアとイランの包括的戦略パートナーシップ条約はいつ署名されたか。", True),
    ("japan_china_peace", "Japan", "China", "diplomacy", "peace_and_friendship_treaty",
     "Treaty of Peace and Friendship between Japan and the People's Republic of China", "1978-08-12",
     "日中平和友好条約はいつ署名されたか。", True),
    ("japan_china_normalization", "Japan", "China", "diplomacy", "joint_communique",
     "Joint Communiqué of the Government of Japan and the Government of the People's Republic of China", "1972-09-29",
     "日中共同声明（国交正常化）はいつ発表されたか。", True),
    ("us_china_normalization", "United States", "China", "diplomacy", "diplomatic_relations_communique",
     "Joint Communiqué on the Establishment of Diplomatic Relations", "1979-01-01",
     "米中の外交関係樹立に関する共同コミュニケはいつ発表され、いつ国交が発効したか。", True),
    ("japan_australia_raa", "Japan", "Australia", "military", "reciprocal_access_agreement",
     "Japan–Australia Reciprocal Access Agreement", "2023-08-13",
     "日豪円滑化協定（Reciprocal Access Agreement）はいつ署名され、いつ発効したか。", True),
    ("japan_uk_raa", "Japan", "United Kingdom", "military", "reciprocal_access_agreement",
     "Japan–United Kingdom Reciprocal Access Agreement", "2023-10-15",
     "日英円滑化協定（Reciprocal Access Agreement）はいつ署名され、いつ発効したか。", True),
    ("japan_philippines_raa", "Japan", "Philippines", "military", "reciprocal_access_agreement",
     "Japan–Philippines Reciprocal Access Agreement", "2025-09-11",
     "日比円滑化協定（Reciprocal Access Agreement）はいつ署名され、いつ発効したか。", True),
]
