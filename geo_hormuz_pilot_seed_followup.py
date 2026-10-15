# -*- coding: utf-8 -*-
"""Phase 3-0 Pilot: 候補3件(country_foundation/precedent/leverage_card)のレビュー指摘対応。

geo_hormuz_pilot_seed.py実行後、人間の詳細レビュー指摘（factual/logical誤り・出典不足）を
受けて以下を行う一度限りのフォローアップスクリプト（冪等）:
1. 不足していたSource/Document/Claimを追加登録（TIME/Gulf International Forum/CNBC pipeline
   記事/Bloomberg。いずれもSteward操作またはClaim登録であり、Knowledge Itemの
   candidate->active承認とは別の操作）
2. 新規Claimを対応するKnowledge Itemへgeo_knowledge_item_claims(relationship=supporting)で
   紐付ける
3. 3件のKnowledge Itemのstructured_content/summary/confidence_rationaleを、レビュー指摘に
   基づき修正した内容へ更新する（statusはcandidateのまま、承認処理は行わない）

使い方: python geo_hormuz_pilot_seed_followup.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from geo_db_client import SupabaseClient  # noqa: E402
from config_utils import load_config  # noqa: E402

import geo_claim_service  # noqa: E402
import geo_connector_base as base  # noqa: E402

SEED_ACTOR_ID = "phase3_pilot_seed_agent"

THEME_ID = "9a62d084-1442-45c9-96fc-50e2a0d3e1a9"
IRAN_ID = "63f60ff3-e318-42f4-9282-43d794e28f4a"
UAE_ID = "33badbe4-0092-498c-ac0b-c382dab9a70f"
US_ID = "671eeb94-62df-45f9-a1e4-5e3fa89a71ba"

KI_COUNTRY_FOUNDATION = "8bf037e3-586a-4c5d-be9f-a80e2a36cb95"
KI_PRECEDENT = "6be7294a-59ab-4d10-9cd6-1403ce1b28c6"
KI_LEVERAGE_CARD = "338dfdca-020d-4bf0-bdd5-2a8fa3fc0410"


def _get_or_create_source(client, *, name, url, source_type, trust_tier, source_owner=None):
    existing = client.select("geo_source_registry", {"select": "*", "name": f"eq.{name}"})
    source = existing[0] if existing else client.insert("geo_source_registry", [{
        "theme_id": THEME_ID, "name": name, "url": url, "source_type": source_type,
        "trust_tier": trust_tier, "source_owner": source_owner, "access_method": "manual",
        "automation_allowed": False,
    }])[0]
    if source.get("reference_availability") != "available":
        base.set_reference_availability(client, source_id=source["source_id"],
                                         reference_availability="available", actor_id=SEED_ACTOR_ID)
        source = client.select("geo_source_registry", {
            "select": "*", "source_id": f"eq.{source['source_id']}", "limit": "1"})[0]
    return source


def _document_and_claim(client, *, source, title, url, published_at, extracted_text,
                         claim_text, information_class, subject_entity_id=None):
    existing_docs = client.select("geo_documents", {"select": "*", "url": f"eq.{url}"})
    document = existing_docs[0] if existing_docs else base.save_as_document(
        client, theme_id=THEME_ID, source_id=source["source_id"], title=title,
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
    return document, claim


def _link_claim_to_item(client, *, knowledge_item_id, claim_id):
    existing = client.select("geo_knowledge_item_claims", {
        "select": "*", "knowledge_item_id": f"eq.{knowledge_item_id}", "claim_id": f"eq.{claim_id}"})
    if existing:
        return
    client.insert("geo_knowledge_item_claims", [{
        "knowledge_item_id": knowledge_item_id, "claim_id": claim_id, "relationship": "supporting",
    }], prefer="return=minimal")


def _update_knowledge_item(client, *, knowledge_item_id, summary, structured_content, confidence_rationale):
    """レビュー指摘に基づく内容修正。status=candidateのまま（承認処理はしない）、
    Claudeが用意した草稿の中身を直す操作であり、review_knowledge_itemとは別物。"""
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{knowledge_item_id}"}, {
        "summary": summary, "structured_content": structured_content,
        "confidence_rationale": confidence_rationale,
    })


def main():
    config = load_config()
    client = SupabaseClient(config)

    # ── 候補1: country_foundation の出典不足を補う ──────────────────
    time_mag = _get_or_create_source(client, name="TIME", url="https://time.com",
                                      source_type="news", trust_tier="D")
    gulf_forum = _get_or_create_source(client, name="Gulf International Forum",
                                        url="https://gulfif.org", source_type="other",
                                        trust_tier="C")

    _doc_succession, claim_succession = _document_and_claim(
        client, source=time_mag,
        title="Iran's Supreme Leader No Longer Reigns Supreme",
        url="https://time.com/article/2026/04/21/war-iran-mojtaba-khamenei-supreme-leader/",
        published_at="2026-04-21T00:00:00+00:00",
        extracted_text=(
            "TIME報道（要約、Web検索で確認）: 2026年2月28日の最高指導者ハメネイ師暗殺を受け、"
            "専門家会議は3月8日、次男のモジュタバ・ハメネイ師を後継の最高指導者に選出した。"
        ),
        claim_text="2026年2月28日の最高指導者暗殺を受け、専門家会議が3月8日、モジュタバ・ハメネイ師を後継の最高指導者に選出した。",
        information_class="observed_fact", subject_entity_id=IRAN_ID,
    )

    _doc_irgc, claim_irgc = _document_and_claim(
        client, source=time_mag,
        title="Iran's Supreme Leader No Longer Reigns Supreme",
        url="https://time.com/article/2026/04/21/war-iran-mojtaba-khamenei-supreme-leader/#irgc-control",
        published_at="2026-04-21T00:00:00+00:00",
        extracted_text=(
            "TIME報道（要約、Web検索で確認）: IRGC司令官アフマド・ヴァヒディ、国家安全保障最高評議会"
            "書記モハマド・バゲル・ゾルガドル等が「静かな構造的クーデター」を主導し、モジュタバ・ハメネイ"
            "の権威を借りる形でIRGCが国家運営の実質的な決定権を握っているとみられる。モジュタバは就任以来"
            "検証可能な公の場での登場・音声・映像を一切公開していない。"
        ),
        claim_text=(
            "IRGCがモジュタバ・ハメネイ選出プロセスを主導し、体制運営における実質的な決定権を掌握して"
            "いる(「静かな構造的クーデター」)とみられる。モジュタバは就任以来、検証可能な公の場での登場・"
            "音声・映像を一切公開しておらず、政権は書面声明とAI生成とみられる画像のみに依存している。"
        ),
        information_class="external_assessment", subject_entity_id=IRAN_ID,
    )

    _doc_gulf, claim_gulf = _document_and_claim(
        client, source=gulf_forum,
        title="Mojtaba Khamenei's Iran and the Politics of Succession",
        url="https://gulfif.org/mojtaba-khameneis-iran-and-the-politics-of-succession/",
        published_at="2026-05-01T00:00:00+00:00",
        extracted_text=(
            "Gulf International Forum分析（要約、Web検索で確認、原文全文は未取得）: "
            "モジュタバ・ハメネイはIRGC内に強い人脈（アフマド・ヴァヒディ、元IRGC情報部門トップ"
            "ホセイン・タエブ等）を持つ中堅聖職者であり、体制内では以前から後継候補と目されていた。"
        ),
        claim_text=(
            "モジュタバ・ハメネイはIRGC司令官アフマド・ヴァヒディや元IRGC情報部門トップのホセイン・"
            "タエブ等と強い人脈を持つ中堅聖職者であり、体制内で以前から後継候補と目されていた。"
        ),
        information_class="external_assessment", subject_entity_id=IRAN_ID,
    )

    for cid in (claim_succession["claim_id"], claim_irgc["claim_id"], claim_gulf["claim_id"]):
        _link_claim_to_item(client, knowledge_item_id=KI_COUNTRY_FOUNDATION, claim_id=cid)

    # ── 候補3: leverage_card の出典不足（UAEパイプライン・Bloomberg）を補う ──
    cnbc = client.select("geo_source_registry", {"select": "*", "name": "eq.CNBC"})[0]
    bloomberg = client.select("geo_source_registry", {"select": "*", "name": "eq.Bloomberg"})[0]

    _doc_pipeline, claim_pipeline = _document_and_claim(
        client, source=cnbc,
        title="UAE fast tracks second West-East oil pipeline to bypass Strait of Hormuz",
        url="https://www.cnbc.com/2026/05/15/uae-west-east-oil-pipeline-strait-hormuz-iran-war.html",
        published_at="2026-05-15T00:00:00+00:00",
        extracted_text=(
            "CNBC報道（要約、Web検索で確認）: UAEはHabshan-Fujairah(ADCOP)パイプライン経由で"
            "ホルムズ海峡を通らず原油を輸出する既存能力（約180万バレル/日）を保有しており、"
            "この能力を倍増させる第二パイプラインの建設を前倒ししている。2026年5月20日時点で"
            "完成度は約50%、2027年の稼働を予定。"
        ),
        claim_text=(
            "UAEはHabshan-Fujairah(ADCOP)パイプライン経由でホルムズ海峡を通らず原油を輸出する"
            "既存能力（約180万バレル/日）を保有し、これを倍増させる第二パイプラインが2026年5月時点で"
            "完成度約50%まで進んでいる（2027年稼働予定）。"
        ),
        information_class="observed_fact", subject_entity_id=UAE_ID,
    )

    _doc_bbg, claim_bbg = _document_and_claim(
        client, source=bloomberg,
        title="Trump Backs Off 20% Fee for Strait of Hormuz Shipments",
        url="https://www.bloomberg.com/news/articles/2026-07-14/trump-backs-off-20-fee-for-strait-of-hormuz-shipments",
        published_at="2026-07-14T00:00:00+00:00",
        extracted_text=(
            "Bloomberg報道（要約、Web検索で確認）: トランプ政権は2026年7月13日に発表した"
            "ホルムズ海峡通航貨物への20%「補償料」を、サウジ・UAE等湾岸諸国の強い反発を受け"
            "翌14日に撤回した。"
        ),
        claim_text="トランプ政権は2026年7月13日発表の20%補償料を、湾岸諸国の反発を受け翌14日に撤回した。",
        information_class="observed_fact", subject_entity_id=US_ID,
    )

    for cid in (claim_pipeline["claim_id"], claim_bbg["claim_id"]):
        _link_claim_to_item(client, knowledge_item_id=KI_LEVERAGE_CARD, claim_id=cid)

    # ── 候補1: country_foundation の内容修正 ────────────────────────
    _update_knowledge_item(
        client, knowledge_item_id=KI_COUNTRY_FOUNDATION,
        summary=(
            "2026年2月の最高指導者暗殺後、専門家会議が3月にモジュタバ・ハメネイ師を後継に選出した。"
            "IRGCが体制運営に強い影響力を持つとの評価はExternal Assessmentとして別途保持し、"
            "本Foundationには含めない。"
        ),
        structured_content={
            "entity_id": IRAN_ID,
            "as_of_source": "TIME(2026-04-21)等の報道記事を基にした2026年8月時点の要約（Web検索で確認、原文全文は未取得）",
            "political_system": "イスラム共和制。最高指導者を頂点とする体制",
            "decision_structure": (
                "2026年2月28日の米・イスラエル攻撃でハメネイ師ら政府高官40人以上が死亡。"
                "専門家会議が3月8-9日、次男のモジュタバ・ハメネイ師を後継の最高指導者に選出。"
                "大統領はペゼシュキアン師。"
            ),
            "geographic_chokepoints": ["ホルムズ海峡"],
            "military_bases_alliances": "IRGC海軍がホルムズ海峡沿岸に展開(出典未特定、要追加確認)",
            "executive_legislative_judicial_military_relations": (
                "行政(大統領)・議会・司法・最高指導者(軍を含む最終権限)から成る従来のイスラム共和制の"
                "統治機構を形式上維持。IRGCの実質的な影響力に関する評価は、本Foundationとは分離し"
                "External Assessmentとして別途保持する（related_external_assessment_claim_ids参照）。"
            ),
            "related_external_assessment_claim_ids": [claim_succession["claim_id"], claim_irgc["claim_id"], claim_gulf["claim_id"]],
        },
        confidence_rationale=(
            "構造的に確認可能な事実（後継選出の経緯・正式な統治機構）に限定。IRGCの実質支配に関する"
            "評価はTIME(2026-04-21)・Gulf International Forumの分析に基づくExternal Assessmentとして"
            "分離済み。military_bases_alliancesは出典未特定のため要追加確認。人間によるレビューが必要。"
        ),
    )

    # ── 候補2: precedent の内容修正（因果関係の訂正） ───────────────
    _update_knowledge_item(
        client, knowledge_item_id=KI_PRECEDENT,
        summary=(
            "長期的な海峡管理体制を先送りしたまま署名された時限的な暫定合意は、期限到来を待たず"
            "核心的争点の再燃で早期に脆弱性を露呈した、という2026年の実例。"
        ),
        structured_content={
            "actors": ["Iran", "United States", "Oman"],
            "period": "2026年6月17日〜（8月時点で係争継続中）",
            "geography": "ホルムズ海峡",
            "precedent_title": "2026年イスラマバードMoU：停戦後の暫定合意とその脆弱性",
            "background": (
                "2026年2月28日の米・イスラエルによる攻撃・最高指導者暗殺を受けイランが海峡を封鎖、"
                "4月7-8日に停戦したものの、通航正常化の枠組みが必要とされた"
            ),
            "triggering_event": "6月17日、トランプ・ペゼシュキアン両大統領がイスラマバードMoUに署名",
            "instruments_used": [
                "米海軍によるイラン港湾封鎖の解除",
                "イランによる60日間の無料安全航行の約束",
                "海峡の将来管理をイラン・オマーン間協議へ委任",
            ],
            "outcome": (
                "海峡の長期管理体制という核心的争点を先送りしたまま署名された暫定合意は、署名から"
                "わずか3週間後（2026年7月7日前後）に商船への攻撃再開という形で早くも動揺した。60日の"
                "無料航行期限（8月16日頃相当）そのものが引き金になったわけではなく、期限到来のはるか"
                "前に核心的争点の再燃という形で合意の脆弱性が露呈した。以降、双方が一方的な新提案"
                "（米: 20%補償料→1日で撤回、イラン: 米・イスラエル船籍禁止+違反時最大20%罰金の新枠組み"
                "→米が拒否）を出しては撤回・拒否される膠着状態が8月時点まで継続している。"
            ),
            "failure_factors": [
                "長期的な海峡管理の枠組みという核心的争点を、時限的な暫定措置（60日間の無料航行）と"
                "引き換えに先送りしたまま、実質合意に至らなかったこと",
            ],
            "success_factors": ["軍事的な即時衝突回避には短期的に成功した"],
            "actor_objectives": [
                "米: 通航の安全確保と軍事的緊張の一時的緩和",
                "イラン: 海峡管理における自国の関与・発言力の確保",
            ],
            "question_examined": (
                "軍事的停戦後に海峡通航の時限的な暫定合意を結んでも、長期的な管理枠組みが未確定なまま"
                "残ると、期限到来を待たずに再エスカレーションするか"
            ),
            "applicable_conditions": [
                "軍事的停戦後、時限的な安全航行合意で長期的な統治・管轄争点を先送りする場合、期限とは"
                "無関係に核心的争点の再燃で早期に再エスカレーションしうることに備える必要がある",
            ],
            "non_applicable_conditions": [
                "当事者間に第三者（オマーン等）の実効的な仲介チャネルが存在しない場合はこの限りではない",
            ],
            "counterpart_responses": (
                "MoU署名からおよそ3週間後（2026年7月7日前後）、イランは事前承認なく通航した商船複数隻"
                "への攻撃を再開したと複数の独立した報道（CNN/Axios/CNBC/Time等、7月7-17日）で確認されて"
                "いる。Al Jazeera(7/9)はこれを「カタールのLNG船Al Rekayyatを含む9隻以上」と報じているが、"
                "この隻数・船名の詳細は当該記事の要約（Web検索経由、原文全文未取得）にのみ依拠しており、"
                "他の一次資料での再確認はできていない。"
            ),
            "indicators_to_watch": [
                "新たな安全航行期限・枠組み合意の期限設定の有無",
                "オマーン等仲介国の交渉進捗報道",
            ],
            "sources": [
                "UK House of Commons Library research briefing CBP-10636 "
                "(https://commonslibrary.parliament.uk/research-briefings/cbp-10636/) — 6/17署名時点まで"
                "の内容のみ対応、それ以降の事象は非対応",
                "Al Jazeera 2026-07-09 (https://www.aljazeera.com/news/2026/7/9/strait-of-hormuz-what-has-happened-since-the-us-iran-mou-on-june-17) — 7/9時点までの攻撃再開の状況",
                "Axios 2026-07-14 / Bloomberg 2026-07-14 — 米20%補償料・撤回",
            ],
        },
        confidence_rationale=(
            "因果関係を「60日期限到来→崩壊」から「未解決の根本争点→暫定合意の脆弱性→期限前の"
            "再エスカレーション」へ訂正。IMO事務局長の法的見解は出典未確認のため削除。"
            "Al Rekayyat/9隻以上の詳細はAl Jazeera記事の検索経由要約にのみ依拠し原文未確認のため、"
            "人間レビューで残すか削除するか判断が必要。CBP-10636とAl Jazeeraの出典を対応期間ごとに"
            "分離した。"
        ),
    )

    # ── 候補3: leverage_card の内容修正 ──────────────────────────────
    _update_knowledge_item(
        client, knowledge_item_id=KI_LEVERAGE_CARD,
        summary=(
            "米国は安全保障提供を根拠にした直接的な費用徴収（20%補償料）を試みたが、湾岸諸国の反発で"
            "1日で撤回しており、Leverageとしての実効性は限定的だったことが実例で示されている。"
            "UAEについては海峡依存度が相対的に低い点も踏まえる必要がある。"
        ),
        structured_content={
            "holder_entity_id": US_ID,
            "target_entity_id": UAE_ID,
            "card_type": "security_provision_cost_leverage",
            "description": (
                "米国はホルムズ海峡での海軍プレゼンス・護衛能力の提供を根拠に、湾岸諸国へ何らかの形での"
                "費用負担を求めた（2026年7月時点では通航貨物の20%の「補償料」という形態）"
            ),
            "objective": (
                "海上安全保障提供（海軍プレゼンス・護衛・封鎖解除）のコストについて、受益国である湾岸"
                "産油国に何らかの形での負担分担を求める。具体的な負担形態は通航料（7/13発表・7/14撤回）"
                "から対米投資協定の枠組みへと変遷しており、最終的に何を得ようとしているかは現時点の"
                "報道からは確定的に読み取れない。"
            ),
            "capability": "米海軍による海峡での軍事的プレゼンス・護衛・封鎖解除能力",
            "usability": (
                "低〜中: 2026年7月13日に20%補償料を実際に発表したが、湾岸諸国の強い反発を受け翌日"
                "撤回されており、直接的な費用徴収としての行使は実証的に困難だった"
            ),
            "target_dependency": (
                "湾岸産油国の中でも海峡依存度には差があり一律ではない。UAEは国内のHabshan-Fujairah"
                "(ADCOP)パイプライン経由で海峡を通らずに原油を輸出する既存の迂回能力（約180万バレル/日）"
                "を保有し、2027年までにこれを倍増させる第二パイプラインが2026年5月時点で完成度約50%まで"
                "進んでいる。したがって「ほぼ全量を海峡経由に依存」と一律に言えるのはUAE以外の一部産油国"
                "（迂回インフラを持たない国）であり、UAEについては依存度は相対的に低く、かつ低下傾向に"
                "ある。"
            ),
            "domestic_tolerance": (
                "米国内では同盟国への費用転嫁要求自体は一定の支持があるとみられるが、1日で撤回した"
                "事実は外交的コストの高さを示す"
            ),
            "escalation_risk": "中: 湾岸諸国がイラン側の交渉（オマーン仲介枠組み）へ接近する誘因になりうる",
            "expected_cost": "湾岸諸国との関係悪化",
            "expected_effect": (
                "直接の料金徴収は失敗し、代わりに対米投資協定という別形態への切り替えで応分負担を"
                "実質的に求める方向へ転換した"
            ),
            "observed_or_reported_claim_ids": [claim_pipeline["claim_id"], claim_bbg["claim_id"]],
        },
        confidence_rationale=(
            "target_dependencyをUAEの実際の迂回インフラ（CNBC 2026-05-15/05-20）を踏まえて是正し、"
            "objectiveを一次報道で確認できる範囲（費用転嫁の試行→投資協定への転換）に限定した。"
            "Observed/Reportedな部分（20%補償料の発表・撤回、UAEパイプライン能力）と、Assessment"
            "（usability/escalation_risk/domestic_toleranceの評価）を分離。"
        ),
    )

    print("candidate1(country_foundation) updated, claims:", claim_succession["claim_id"], claim_irgc["claim_id"], claim_gulf["claim_id"])
    print("candidate2(precedent) updated (no new claims, existing 4ea65098/2053e434 reused)")
    print("candidate3(leverage_card) updated, claims:", claim_pipeline["claim_id"], claim_bbg["claim_id"])


if __name__ == "__main__":
    sys.exit(main())
