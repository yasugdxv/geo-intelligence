# -*- coding: utf-8 -*-
"""Phase 3-0 Pilot: イラン/ホルムズ海峡Theaterへの最小限データ投入。

承認済みPlan: C:\\Users\\269811\\.claude\\plans\\pure-drifting-cosmos.md

設計方針（Plan 5章、ユーザーフィードバック反映）:
- ActorのObjectives/Interests/Constraints/Positioningは、外部機関・当事者の発言・分析
  として geo_claims（information_class=actor_claim/external_assessment/observed_fact）で
  保持する。承認ワークフローを経て「確定した基礎知識」として扱う actor_profile 等には
  しない。原文（取得できた範囲）は geo_documents に保存し、Claudeによる要約・特徴づけは
  derived contentとして geo_claims に登録、Evidenceで原文chunkへ接続する
- country_foundation/precedent/leverage_card は status=candidate まで作成する。
  「実際の人間レビュー」は本スクリプトでは行わない（Runbook別ステップでユーザー/別専門家が
  Candidate Review Queueから review_knowledge_item を実行する）
- Case-specific Issueを3つ独立して作る（1つの広いIssueへ人為的に一本化しない）
- Source登録・reference_availability設定はSteward操作（内容の正誤判定ではなくSource単位の
  運用判断）としてこのスクリプトが行ってよい（Phase2B-2の設計通り）
- 全データの出典は実際にWeb検索で確認した内容のみ（2026-08-20時点、docs/geopolitical-phase3
  -pilot-runbook.md参照）。原文全文を取得できなかった記事は、要約と出典URL・公開日のみを
  保持し、全文取得できたかのように装わない

使い方: python geo_hormuz_pilot_seed.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from geo_db_client import SupabaseClient  # noqa: E402
from config_utils import load_config  # noqa: E402

import geo_claim_service  # noqa: E402
import geo_connector_base as base  # noqa: E402
import geo_entity_service  # noqa: E402
import geo_knowledge_service  # noqa: E402
import geo_relationship_service  # noqa: E402
from geo_theme_seed_hormuz import seed_hormuz_theme  # noqa: E402

# Source Reference PolicyのSteward操作、およびEntity/Relationship/Asset/Dependency/Issueの
# 監査ログ上の識別子。Knowledge ItemのActive化レビュー（人間のみが行う）には一切使わない。
SEED_ACTOR_ID = "phase3_pilot_seed_agent"


def _get_or_create_entity(client, *, entity_type, canonical_name, name_en=None, country_code=None):
    existing = client.select("geo_entities", {"select": "*", "canonical_name": f"eq.{canonical_name}"})
    if existing:
        return existing[0]
    return geo_entity_service.create_entity(
        client, entity_type=entity_type, canonical_name=canonical_name, name_en=name_en,
        country_code=country_code, actor_id=SEED_ACTOR_ID)


def _get_or_create_source(client, *, theme_id, name, url, source_type, trust_tier, source_owner=None):
    existing = client.select("geo_source_registry", {"select": "*", "name": f"eq.{name}"})
    source = existing[0] if existing else client.insert("geo_source_registry", [{
        "theme_id": theme_id, "name": name, "url": url, "source_type": source_type,
        "trust_tier": trust_tier, "source_owner": source_owner, "access_method": "manual",
        "automation_allowed": False,
    }])[0]
    if source.get("reference_availability") != "available":
        base.set_reference_availability(client, source_id=source["source_id"],
                                         reference_availability="available", actor_id=SEED_ACTOR_ID)
        source = client.select("geo_source_registry", {
            "select": "*", "source_id": f"eq.{source['source_id']}", "limit": "1"})[0]
    return source


def _get_or_create_issue(client, *, theme_id, code, name_ja):
    existing = client.select("geo_issues", {"select": "*", "theme_id": f"eq.{theme_id}", "code": f"eq.{code}"})
    if existing:
        return existing[0]
    return client.insert("geo_issues", [{"theme_id": theme_id, "code": code, "name_ja": name_ja}])[0]


def _get_or_create_knowledge_item(client, *, knowledge_type, title, **kwargs):
    """既存のgeo_candidate_extraction_service._find_duplicate_candidatesと同じ、
    タイトル完全一致による重複検知パターン（本スクリプトの再実行での二重作成を防ぐ）。"""
    existing = client.select("geo_knowledge_items", {
        "select": "*", "knowledge_type": f"eq.{knowledge_type}", "title": f"eq.{title}"})
    if existing:
        return existing[0]
    return geo_knowledge_service.create_knowledge_item(
        client, knowledge_type=knowledge_type, title=title, **kwargs)


def _document_and_claim(client, *, theme_id, source, title, url, published_at, extracted_text,
                         claim_text, information_class, subject_entity_id=None, issue_ids=None):
    """原文（取得できた範囲）を geo_documents へ保存・chunk化し、Claudeによる要約は
    derived content として geo_claims に登録、Evidenceで原文chunkへ接続する。"""
    existing_docs = client.select("geo_documents", {"select": "*", "url": f"eq.{url}"})
    document = existing_docs[0] if existing_docs else base.save_as_document(
        client, theme_id=theme_id, source_id=source["source_id"], title=title,
        extracted_text=extracted_text, mime_type="text/plain", url=url, published_at=published_at)[0]

    chunks = geo_claim_service.chunk_document(client, document["document_id"])
    chunk = chunks[0] if chunks else None

    existing_claims = client.select("geo_claims", {
        "select": "*", "origin_type": "eq.document", "origin_id": f"eq.{document['document_id']}",
        "claim_text": f"eq.{claim_text}",
    })
    if existing_claims:
        claim = existing_claims[0]
    else:
        claim = geo_claim_service.create_claim(
            client, claim_text=claim_text, information_class=information_class,
            origin_type="document", origin_id=document["document_id"], actor_id=SEED_ACTOR_ID,
            subject_entity_id=subject_entity_id)
        if chunk:
            geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                             actor_id=SEED_ACTOR_ID, document_chunk_id=chunk["chunk_id"])
    for issue_id in (issue_ids or []):
        existing_link = client.select("geo_claim_issues", {
            "select": "*", "claim_id": f"eq.{claim['claim_id']}", "issue_id": f"eq.{issue_id}"})
        if not existing_link:
            geo_claim_service.link_claim_to_issue(client, claim_id=claim["claim_id"], issue_id=issue_id)
    return document, claim


def main():
    config = load_config()
    client = SupabaseClient(config)

    theme = seed_hormuz_theme(client)["theme"]
    theme_id = theme["theme_id"]

    # ── 1. Core Actor Entity ────────────────────────────────────────
    iran = _get_or_create_entity(client, entity_type="country", canonical_name="Iran", country_code="IR")
    us = _get_or_create_entity(client, entity_type="country", canonical_name="United States", country_code="US")
    israel = _get_or_create_entity(client, entity_type="country", canonical_name="Israel", country_code="IL")
    saudi = _get_or_create_entity(client, entity_type="country", canonical_name="Saudi Arabia", country_code="SA")
    uae = _get_or_create_entity(client, entity_type="country", canonical_name="United Arab Emirates", country_code="AE")
    oman = _get_or_create_entity(client, entity_type="country", canonical_name="Oman", country_code="OM")
    qatar = _get_or_create_entity(client, entity_type="country", canonical_name="Qatar", country_code="QA")
    china = _get_or_create_entity(client, entity_type="country", canonical_name="China", country_code="CN")
    irgc = _get_or_create_entity(client, entity_type="security_service",
                                  canonical_name="Islamic Revolutionary Guard Corps (IRGC)")

    # ── 2. Source Registry（Steward操作でreference_availability=available） ──
    npr = _get_or_create_source(client, theme_id=theme_id, name="NPR", url="https://www.npr.org",
                                 source_type="news", trust_tier="D")
    axios = _get_or_create_source(client, theme_id=theme_id, name="Axios", url="https://www.axios.com",
                                   source_type="news", trust_tier="D")
    bloomberg = _get_or_create_source(client, theme_id=theme_id, name="Bloomberg", url="https://www.bloomberg.com",
                                       source_type="news", trust_tier="D")
    cnbc = _get_or_create_source(client, theme_id=theme_id, name="CNBC", url="https://www.cnbc.com",
                                  source_type="news", trust_tier="D")
    aljazeera = _get_or_create_source(client, theme_id=theme_id, name="Al Jazeera", url="https://www.aljazeera.com",
                                       source_type="news", trust_tier="D")
    scmp = _get_or_create_source(client, theme_id=theme_id, name="South China Morning Post",
                                  url="https://www.scmp.com", source_type="news", trust_tier="D")
    commons = _get_or_create_source(client, theme_id=theme_id, name="UK House of Commons Library",
                                     url="https://commonslibrary.parliament.uk", source_type="official_site",
                                     trust_tier="B", source_owner="UK Parliament")

    # ── 3. Case-specific Issue（3件独立、人為的な一本化はしない） ──────
    issue_case1 = _get_or_create_issue(client, theme_id=theme_id, code="hormuz-reopening",
                                        name_ja="ホルムズ海峡封鎖リスク・再開交渉")
    issue_case2 = _get_or_create_issue(client, theme_id=theme_id, code="us-gulf-security-cost",
                                        name_ja="米国・湾岸諸国間の安全保障コスト負担")
    issue_case3 = _get_or_create_issue(client, theme_id=theme_id, code="china-iran-influence",
                                        name_ja="中国の対イラン影響力")

    for e in (iran, us, saudi, uae, oman, qatar, irgc):
        for issue_id in {issue_case1["issue_id"]}:
            existing = client.select("geo_entity_issues", {
                "select": "*", "entity_id": f"eq.{e['entity_id']}", "issue_id": f"eq.{issue_id}"})
            if not existing:
                client.insert("geo_entity_issues", [{"entity_id": e["entity_id"], "issue_id": issue_id}])
    for e in (us, saudi, uae, qatar):
        existing = client.select("geo_entity_issues", {
            "select": "*", "entity_id": f"eq.{e['entity_id']}", "issue_id": f"eq.{issue_case2['issue_id']}"})
        if not existing:
            client.insert("geo_entity_issues", [{"entity_id": e["entity_id"], "issue_id": issue_case2["issue_id"]}])
    for e in (china, iran):
        existing = client.select("geo_entity_issues", {
            "select": "*", "entity_id": f"eq.{e['entity_id']}", "issue_id": f"eq.{issue_case3['issue_id']}"})
        if not existing:
            client.insert("geo_entity_issues", [{"entity_id": e["entity_id"], "issue_id": issue_case3["issue_id"]}])

    # ── 4. Claims（External Assessment/Actor Claim/Observed Fact、derived content＋Evidence） ──
    _document_and_claim(
        client, theme_id=theme_id, source=commons,
        title="The Strait of Hormuz: Islamabad MoU and its terms",
        url="https://commonslibrary.parliament.uk/research-briefings/cbp-10636/",
        published_at="2026-07-01",
        extracted_text=(
            "UK House of Commons Library briefing summary (retrieved via web search, "
            "full text not fetchable — access restricted): 2026年6月17日、トランプ米大統領と"
            "ペゼシュキアン・イラン大統領が「イスラマバード覚書（MoU）」に署名。米側は海軍による"
            "イラン港湾封鎖を解除し、イラン側は60日間の無料での安全航行を約束した。海峡の将来的な"
            "管理体制は、イランがオマーン等湾岸諸国と協議して決定する、と規定された。"
        ),
        claim_text="2026年6月17日のイスラマバードMoUは、米が海軍封鎖を解除しイランが60日間の無料"
                   "安全航行を約束する一方、海峡の長期管理体制はイラン・オマーン間協議に委ねる"
                   "暫定合意だった（長期的争点は先送りされた）。",
        information_class="observed_fact", subject_entity_id=iran["entity_id"],
        issue_ids=[issue_case1["issue_id"]],
    )
    _document_and_claim(
        client, theme_id=theme_id, source=aljazeera,
        title="Strait of Hormuz: what has happened since the US-Iran MoU",
        url="https://www.aljazeera.com/news/2026/7/9/strait-of-hormuz-what-has-happened-since-the-us-iran-mou-on-june-17",
        published_at="2026-07-09",
        extracted_text=(
            "Al Jazeera報道（要約、Web検索で確認）: イスラマバードMoUの60日間安全航行期限が近づく中、"
            "イランは事前承認なく通航した商船への攻撃を再開し、カタールのLNG船Al Rekayyatを含む"
            "9隻以上が被害を受けた。"
        ),
        claim_text="イランはMoUの60日安全航行期限の前後から、事前承認なく通航した商船への攻撃を再開"
                   "した（カタールのLNG船Al Rekayyat含む9隻以上）。",
        information_class="observed_fact", subject_entity_id=iran["entity_id"],
        issue_ids=[issue_case1["issue_id"]],
    )
    _document_and_claim(
        client, theme_id=theme_id, source=npr,
        title="Iran says agreement with Oman for Strait of Hormuz prohibits U.S. and Israeli vessels",
        url="https://www.npr.org/2026/08/07/nx-s1-5923962/iran-says-agreement-with-oman-for-strait-of-hormuz-prohibits-u-s-and-israeli-vessels",
        published_at="2026-08-07",
        extracted_text=(
            "NPR報道（要約、Web検索で確認）: イランはオマーンとの間で、ホルムズ海峡において米・"
            "イスラエル船籍を禁止し、違反船には貨物価値の最大20%の罰金を科すとする新たな枠組みで"
            "合意が進んでいると主張。米国はこの枠組みを拒否している。"
        ),
        claim_text="イランは、米・イスラエル船籍を禁止し違反時に貨物価値最大20%の罰金を科す新枠組み"
                   "でオマーンと合意が進んでいると主張しているが、米国はこれを拒否している。",
        information_class="actor_claim", subject_entity_id=iran["entity_id"],
        issue_ids=[issue_case1["issue_id"]],
    )
    _document_and_claim(
        client, theme_id=theme_id, source=cnbc,
        title="US-Iran war: Trump says no talks, Hormuz blockade continues",
        url="https://www.cnbc.com/2026/08/18/us-iran-war-trump-hormuz-trump-ceasefire-expires-extension-.html",
        published_at="2026-08-18",
        extracted_text=(
            "CNBC報道（要約、Web検索で確認）: トランプ大統領は2026年8月18日、イランとの対話は"
            "行わず、ホルムズ海峡の封鎖状態が続くと表明。UAEは同時期にイランからの弾道ミサイル"
            "探知を報告した。"
        ),
        claim_text="トランプ大統領は2026年8月18日、イランとの対話を行わず封鎖状態が続くと表明した。",
        information_class="actor_claim", subject_entity_id=us["entity_id"],
        issue_ids=[issue_case1["issue_id"]],
    )
    _document_and_claim(
        client, theme_id=theme_id, source=axios,
        title="Trump backs off 20% fee for Strait of Hormuz shipments",
        url="https://www.axios.com/2026/07/14/trump-hormuz-toll-demand-trade-fee",
        published_at="2026-07-14",
        extracted_text=(
            "Axios報道（要約、Web検索で確認）: トランプ政権は2026年7月13日、ホルムズ海峡通航"
            "貨物の20%相当を「米国補償料」として課すと発表したが、サウジアラビア・UAE・カタール・"
            "バーレーン・クウェート等湾岸諸国の強い反発を受け、翌7月14日に撤回。代わりに対米投資"
            "協定の枠組みへ切り替えた。"
        ),
        claim_text="トランプ政権は2026年7月13日にホルムズ海峡通航貨物の20%相当の「補償料」導入を"
                   "発表したが、湾岸諸国の反発により翌日撤回し、対米投資協定の枠組みへ切り替えた"
                   "（費用負担要求というLeverage行使は試みられたが、外交コストの高さから1日で撤回"
                   "された）。",
        information_class="observed_fact", subject_entity_id=us["entity_id"],
        issue_ids=[issue_case1["issue_id"], issue_case2["issue_id"]],
    )
    _document_and_claim(
        client, theme_id=theme_id, source=bloomberg,
        title="Iran Seeks Bar on US Ships in Hormuz as Deal With Oman Advances",
        url="https://www.bloomberg.com/news/articles/2026-08-06/iran-seeks-bar-on-us-ships-in-hormuz-as-deal-with-oman-advances",
        published_at="2026-08-06",
        extracted_text=(
            "Bloomberg報道（要約、Web検索で確認）: イランはオマーンとの交渉が進む枠組みの一部として"
            "米国船籍のホルムズ海峡における恒久的な航行禁止を求めている。"
        ),
        claim_text="イランはオマーンとの交渉枠組みの一部として、米国船籍の恒久的な航行禁止を求めて"
                   "いる。",
        information_class="actor_claim", subject_entity_id=iran["entity_id"],
        issue_ids=[issue_case1["issue_id"]],
    )
    _document_and_claim(
        client, theme_id=theme_id, source=scmp,
        title="China's Wang Yi calls on Iran to ensure freedom and safe passage through Strait of Hormuz",
        url="https://www.scmp.com/news/china/diplomacy/article/3350259/chinas-wang-yi-calls-iran-ensure-freedom-and-safe-passage-through-strait-hormuz",
        published_at="2026-08-01",
        extracted_text=(
            "SCMP報道（要約、Web検索で確認）: 中国の王毅外相はイランに対し、ホルムズ海峡における"
            "航行の自由と安全な通航を確保するよう要請した。今回の危機で中国がイランへ直接公に要請"
            "したのは初めてとされる。中国は「実利的中立」の姿勢を取り、米・イラン双方に自制を求め"
            "つつ対話再開を仲介している。"
        ),
        claim_text="中国の王毅外相は、今回の危機で初めてイランへ直接公に航行の自由・安全な通航の"
                   "確保を要請した。中国は米・イラン双方への実利的中立姿勢を保ちつつ対話仲介を"
                   "試みている。",
        information_class="actor_claim", subject_entity_id=china["entity_id"],
        issue_ids=[issue_case3["issue_id"]],
    )
    _document_and_claim(
        client, theme_id=theme_id, source=npr,
        title="How China gets around the blockade of the Strait of Hormuz to get Iranian oil",
        url="https://www.npr.org/2026/08/02/nx-s1-5913374/how-china-gets-around-the-blockade-of-the-strait-of-hormuz-to-get-iranian-oil",
        published_at="2026-08-02",
        extracted_text=(
            "NPR報道（要約、Web検索で確認）: 中国は開戦後、原油備蓄を年初から16%積み増し、"
            "封鎖期間中の輸入を日量500万バレル超削減して価格急騰を緩和した。4月の停戦後から"
            "7月MoU失効までの間隙には、イラン産原油タンカー60隻超が中国向けに出荷されたと"
            "みられる。"
        ),
        claim_text="中国は原油備蓄積み増しと輸入調整で価格急騰の影響を緩和しつつ、停戦の隙間期間に"
                   "割引されたイラン産原油を大量に調達しており、危機を経済的に利用する「実利的中立」"
                   "の実態がうかがえる。",
        information_class="external_assessment", subject_entity_id=china["entity_id"],
        issue_ids=[issue_case3["issue_id"]],
    )

    # ── 5. Relationship（Case1-3に直接関わるペアのみ） ─────────────
    def _ensure_relationship(from_e, to_e, domain, rtype, **kwargs):
        existing = client.select("geo_relationships", {
            "select": "*", "from_entity_id": f"eq.{from_e['entity_id']}",
            "to_entity_id": f"eq.{to_e['entity_id']}", "relationship_type": f"eq.{rtype}"})
        if existing:
            return existing[0]
        # trendはcreate_relationship()の引数に無い（DB列はあるがサービス関数が未対応）ため、
        # 作成後にDB列を直接更新する（既存関数のシグネチャは変更しない）
        trend = kwargs.pop("trend", None)
        created = geo_relationship_service.create_relationship(
            client, from_entity_id=from_e["entity_id"], to_entity_id=to_e["entity_id"],
            relationship_domain=domain, relationship_type=rtype, actor_id=SEED_ACTOR_ID, **kwargs)
        if trend:
            client.update("geo_relationships", {"relationship_id": f"eq.{created['relationship_id']}"},
                          {"trend": trend})
            created["trend"] = trend
        return created

    _ensure_relationship(
        iran, oman, "diplomacy", "mediation_negotiation_channel", trend="strengthening",
        description="オマーンがホルムズ海峡将来管理を巡るイランの直接交渉相手・仲介役を務める",
        confidence="高")
    _ensure_relationship(
        iran, us, "military", "active_conflict_ceasefire", trend="volatile",
        leverage_direction="mutual",
        description="2026年2月開戦・4月停戦後も、ホルムズ海峡の扱いを巡り軍事的緊張が継続",
        confidence="高")
    _ensure_relationship(
        us, uae, "security", "security_provider_cost_dispute", trend="volatile",
        leverage_direction="from_has_leverage",
        description="米国はホルムズ海峡での安全保障提供を根拠に費用負担を求めたが、UAE等の反発で"
                    "即撤回された（2026年7月）",
        confidence="中")
    _ensure_relationship(
        china, iran, "energy", "major_oil_buyer_discount_purchaser", trend="strengthening",
        leverage_direction="to_has_leverage",
        description="中国は停戦の隙間期間に割引されたイラン産原油を大量調達しており、イランに"
                    "とって数少ない安定した石油輸出先",
        confidence="中")

    # ── 6. Asset / Dependency ──────────────────────────────────────
    existing_asset = client.select("geo_assets", {
        "select": "*", "entity_id": f"eq.{iran['entity_id']}", "asset_type": "eq.strait_canal_control"})
    if not existing_asset:
        client.insert("geo_assets", [{
            "entity_id": iran["entity_id"], "asset_type": "strait_canal_control",
            "description": "IRGCによる機雷敷設・武装スピードボート・沿岸ミサイルを用いたホルムズ海峡"
                           "への実効的な航行妨害能力（2026年3月以降、実際に行使され海峡を封鎖した"
                           "実績がある）",
            "confidence": "高",
        }])
    existing_dep = client.select("geo_dependencies", {
        "select": "*", "entity_id": f"eq.{saudi['entity_id']}", "dependency_type": "eq.export_market"})
    if not existing_dep:
        client.insert("geo_dependencies", [{
            "entity_id": saudi["entity_id"], "dependency_type": "export_market",
            "counterpart_entity_id": None,
            "description": "サウジアラビアを含む湾岸産油国は原油輸出のほぼ全量をホルムズ海峡経由の"
                           "海上輸送に依存しており、通航妨害・料金賦課は輸出収入へ直接的な打撃となる",
            "severity": "critical", "confidence": "高",
        }])

    # ── 7. Knowledge Item（status=candidateのみ。Active化はユーザーが実施） ──
    country_foundation_iran = _get_or_create_knowledge_item(
        client, knowledge_type="country_foundation", title="イラン: 2026年最高指導者暗殺後の意思決定構造",
        structured_content={
            "entity_id": iran["entity_id"],
            "political_system": "イスラム共和制。最高指導者を頂点とする体制",
            "decision_structure": "2026年2月28日の米・イスラエル攻撃でハメネイ師ら政府高官40人以上が"
                                   "死亡。3月9日、息子のモジュタバ・ハメネイ師が後継の最高指導者に選出"
                                   "されたが、IRGC司令官アフマド・ヴァヒディが選出プロセスを主導したと"
                                   "され、IRGCの実質的な影響力が体制内で強まっているとみられる。大統領"
                                   "はペゼシュキアン師",
            "executive_legislative_judicial_military_relations": "IRGCがホルムズ海峡の通航許可・警備を"
                                   "自国管轄と主張し実効支配しており、対外交渉（オマーンとの枠組み協議）"
                                   "の実務にも深く関与しているとみられる",
            "military_bases_alliances": "IRGC navy がホルムズ海峡沿岸に展開",
            "geographic_chokepoints": ["ホルムズ海峡"],
            "as_of_source": "Wikipedia『2026 Iranian leadership crisis』等の報道記事を基にした2026年8月"
                             "時点の要約（Web検索で確認、原文全文は未取得）",
        },
        information_class="internal_hypothesis", created_by=SEED_ACTOR_ID,
        summary="2026年2月の最高指導者暗殺後、後継体制はIRGCの影響力が強まった形で成立しており、"
                "ホルムズ海峡を巡る交渉・実力行使の実務もIRGCが主導している可能性が高い。",
        confidence="中",
        confidence_rationale="複数報道の要約に基づく暫定的な整理であり、一次資料（イラン政府発表等）"
                              "による裏付けは未確認。人間によるレビューが必要。",
    )

    precedent_mou = _get_or_create_knowledge_item(
        client, knowledge_type="precedent", title="2026年イスラマバードMoU：停戦後の暫定合意とその破綻",
        structured_content={
            "precedent_title": "2026年イスラマバードMoU：停戦後の暫定合意とその破綻",
            "question_examined": "軍事的停戦後に海峡通航の時限的な暫定合意を結んでも、長期的な管理"
                                  "枠組みが未確定なまま期限を迎えると再エスカレーションするか",
            "period": "2026年6月17日〜7月20日頃",
            "geography": "ホルムズ海峡",
            "actors": ["Iran", "United States", "Oman"],
            "background": "2026年2月28日の米・イスラエルによる攻撃・最高指導者暗殺を受けイランが"
                           "海峡を封鎖、4月7-8日に停戦したものの、通航正常化の枠組みが必要とされた",
            "triggering_event": "6月17日、トランプ・ペゼシュキアン両大統領がイスラマバードMoUに署名",
            "actor_objectives": [
                "米: 通航の安全確保と軍事的緊張の一時的緩和",
                "イラン: 海峡管理における自国の関与・発言力の確保",
            ],
            "instruments_used": ["米海軍によるイラン港湾封鎖の解除", "イランによる60日間の無料安全"
                                  "航行の約束", "海峡の将来管理をイラン・オマーン間協議へ委任"],
            "legal_and_institutional_conditions": "IMO事務局長はUNCLOS上、いかなる国も国際海峡での"
                                   "通航料・関税・差別的条件を課す法的根拠は無いと明言（イラン案・"
                                   "米国案の双方を否定）",
            "counterpart_responses": "60日の安全航行期限が近づく・経過する中、イランは事前承認なく"
                                   "通航した商船への攻撃を再開（カタールのLNG船含む9隻以上）",
            "outcome": "時限的な暫定合意は期限到来とともに崩壊し、7月以降イラン・米国双方が新たな"
                       "要求（イランの米・イスラエル船籍禁止案、米国の20%補償料案）を出しては"
                       "撤回・拒否される膠着状態が継続",
            "success_factors": ["軍事的な即時衝突回避には短期的に成功した"],
            "failure_factors": ["長期的な海峡管理の枠組みという核心的争点を未解決のまま時限合意の"
                                 "期限を設定したこと"],
            "applicable_conditions": ["軍事的停戦後、時限的な安全航行合意で長期的な統治・管轄争点を"
                                       "先送りする場合、期限到来時の再エスカレーションに備える必要がある"],
            "non_applicable_conditions": ["当事者間に第三者（オマーン等）の実効的な仲介チャネルが"
                                           "存在しない場合はこの限りではない"],
            "indicators_to_watch": ["新たな安全航行期限・枠組み合意の期限設定の有無", "オマーン等"
                                     "仲介国の交渉進捗報道"],
            "sources": [
                "UK House of Commons Library research briefing CBP-10636 "
                "(https://commonslibrary.parliament.uk/research-briefings/cbp-10636/)",
                "Al Jazeera 2026-07-09 "
                "(https://www.aljazeera.com/news/2026/7/9/strait-of-hormuz-what-has-happened-since-the-us-iran-mou-on-june-17)",
            ],
            "confidence": "中",
        },
        information_class="internal_hypothesis", created_by=SEED_ACTOR_ID,
        summary="時限的な安全航行の暫定合意は、長期的な海峡管理の争点を解決しないまま期限を迎えると"
                "再エスカレーションする、という2026年の実例。",
        confidence="中",
        confidence_rationale="複数報道に基づく整理。人間によるレビューが必要。",
    )

    leverage_card_us_uae = _get_or_create_knowledge_item(
        client, knowledge_type="leverage_card", title="米国→UAE: 海上安全保障提供を根拠にした費用負担要求",
        structured_content={
            "holder_entity_id": us["entity_id"], "target_entity_id": uae["entity_id"],
            "card_type": "security_provision_cost_leverage",
            "description": "米国はホルムズ海峡での海軍プレゼンス・護衛能力の提供を根拠に、湾岸諸国へ"
                            "通航貨物の一定割合（2026年7月時点で20%）を「補償料」として負担するよう"
                            "求めた",
            "objective": "海上安全保障提供のコストを受益国に転嫁し、対米投資協定等より有利な条件を"
                         "引き出す",
            "capability": "米海軍による海峡での軍事的プレゼンス・護衛・封鎖解除能力",
            "usability": "低〜中: 2026年7月13日に20%補償料を実際に発表したが、サウジ・UAE・カタール"
                         "・バーレーン・クウェート等の強い反発を受け翌日撤回されており、直接的な費用"
                         "徴収としての行使は実証的に困難だった",
            "target_dependency": "UAEを含む湾岸産油国は原油輸出のほぼ全量をホルムズ海峡経由の海上"
                                  "輸送に依存（geo_dependencies参照）",
            "domestic_tolerance": "米国内では同盟国への費用転嫁要求自体は一定の支持があるとみられる"
                                   "が、1日で撤回した事実は外交的コストの高さを示す",
            "expected_effect": "直接の料金徴収は失敗し、代わりに対米投資協定という別形態への切り替え"
                                "で応分負担を実質的に求める方向へ転換した",
            "expected_cost": "湾岸諸国との関係悪化、IMO等国際機関からの法的正当性の否定",
            "escalation_risk": "中: 湾岸諸国がイラン側の交渉（オマーン仲介枠組み）へ接近する誘因に"
                                "なりうる",
            "evidence": [
                "Axios 2026-07-14 (https://www.axios.com/2026/07/14/trump-hormuz-toll-demand-trade-fee)",
                "Bloomberg 2026-07-14 (https://www.bloomberg.com/news/articles/2026-07-14/trump-backs-off-20-fee-for-strait-of-hormuz-shipments)",
            ],
        },
        information_class="internal_hypothesis", created_by=SEED_ACTOR_ID,
        summary="米国は安全保障提供を根拠にした直接的な費用徴収（20%補償料）を試みたが、湾岸諸国の"
                "反発で1日で撤回しており、Leverageとしての実効性は限定的だったことが実例で示されて"
                "いる。",
        confidence="中",
        confidence_rationale="複数報道に基づく整理。人間によるレビューが必要。",
    )

    for item, issue in ((country_foundation_iran, issue_case1), (precedent_mou, issue_case1),
                        (precedent_mou, issue_case3), (leverage_card_us_uae, issue_case2)):
        existing_link = client.select("geo_knowledge_item_issues", {
            "select": "*", "knowledge_item_id": f"eq.{item['knowledge_item_id']}",
            "issue_id": f"eq.{issue['issue_id']}"})
        if not existing_link:
            geo_knowledge_service.link_to_issue(
                client, knowledge_item_id=item["knowledge_item_id"], issue_id=issue["issue_id"])

    print(f"theme_id={theme_id}")
    print(f"issue_case1={issue_case1['issue_id']} issue_case2={issue_case2['issue_id']} "
          f"issue_case3={issue_case3['issue_id']}")
    print(f"candidate knowledge items（人間レビュー待ち）: "
          f"{country_foundation_iran['knowledge_item_id']}, {precedent_mou['knowledge_item_id']}, "
          f"{leverage_card_us_uae['knowledge_item_id']}")


if __name__ == "__main__":
    main()
