import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_engine as engine  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

THEME_ID = "theme-1"


def test_build_source_pack_without_theme_returns_minimal_text():
    client = FakeSupabaseClient()
    source_pack = engine.build_source_pack(client, None, "台湾情勢について教えて")
    assert source_pack["theme"] is None
    assert source_pack["document_ids"] == []


def test_build_source_pack_includes_events_and_observations():
    client = FakeSupabaseClient(tables={
        "geo_themes": [{"theme_id": THEME_ID, "name_ja": "台湾有事", "code": "taiwan_strait"}],
        "geo_events": [{
            "event_id": "e-1", "theme_id": THEME_ID, "actor": "PLA", "action": "越境",
            "verified": True, "created_at": "2026-08-01T00:00:00+00:00",
        }],
        "geo_evidence": [{"evidence_id": "ev-1", "event_id": "e-1", "quote": "10機が越境した"}],
        "geo_documents": [{"document_id": "doc-1", "theme_id": THEME_ID, "title": "PLA機の越境について国防部発表", "fetched_at": "2026-08-01T00:00:00+00:00"}],
        "geo_indicator_definitions": [{
            "indicator_id": "ind-1", "theme_id": THEME_ID, "indicator_code": "MIL-01",
            "name": "PLA航空活動", "mvp_enabled": True,
        }],
        "geo_observations": [{
            "observation_id": "obs-1", "indicator_id": "ind-1", "raw_value": 3.0,
            "insufficient_data": False, "is_mock": False, "as_of": "2026-08-01T00:00:00+00:00",
        }],
    })

    source_pack = engine.build_source_pack(client, THEME_ID, "PLAの越境状況は？")

    assert source_pack["theme"]["name_ja"] == "台湾有事"
    assert "PLA" in source_pack["text"]
    assert "10機が越境した" in source_pack["text"]
    assert "MIL-01" in source_pack["text"]
    assert source_pack["document_ids"] == ["doc-1"]
    assert source_pack["event_ids"] == ["e-1"]


def test_build_source_pack_resolves_evidence_source_type_deterministically():
    """Phase R1 Chunk2: evidence_id→source_typeが、geo_evidence.document_id→
    geo_documents.source_id→geo_source_registry.source_typeの経路から決定論的に
    解決されることを確認する（LLMの自己申告に依存しない）。document_id/source_idが
    無いevidenceはマップに含まれない（呼び出し側でunknown扱いになる）ことも確認する。"""
    client = FakeSupabaseClient(tables={
        "geo_themes": [{"theme_id": THEME_ID, "name_ja": "台湾有事", "code": "taiwan_strait"}],
        "geo_events": [{
            "event_id": "e-1", "theme_id": THEME_ID, "actor": "PLA", "action": "越境",
            "verified": True, "created_at": "2026-08-01T00:00:00+00:00",
        }],
        "geo_evidence": [
            {"evidence_id": "ev-official", "event_id": "e-1", "quote": "国防部発表", "document_id": "doc-official"},
            {"evidence_id": "ev-news", "event_id": "e-1", "quote": "通信社報道", "document_id": "doc-news"},
            {"evidence_id": "ev-no-doc", "event_id": "e-1", "quote": "文書に紐付かない証言"},
        ],
        "geo_documents": [
            {"document_id": "doc-official", "theme_id": THEME_ID, "title": "国防部発表", "source_id": "src-official"},
            {"document_id": "doc-news", "theme_id": THEME_ID, "title": "通信社報道", "source_id": "src-news"},
        ],
        "geo_source_registry": [
            {"source_id": "src-official", "theme_id": THEME_ID, "name": "国防部", "source_type": "official_site"},
            {"source_id": "src-news", "theme_id": THEME_ID, "name": "通信社", "source_type": "news"},
        ],
    })
    source_pack = engine.build_source_pack(client, THEME_ID, "PLAの越境状況は？")
    assert source_pack["evidence_source_types"] == {
        "ev-official": "primary_official", "ev-news": "current_news",
    }
    assert "ev-no-doc" not in source_pack["evidence_source_types"]


def _mixed_documents_client():
    """プーチン/ウクライナ関連の質問に対し、無関係なサステナビリティレポートが混入していた
    実際のバグ（docs/expert-ai-validation/m10a_case2_media_increment/media_activation_trace.md）
    を再現するfixture: サステナ関連10本 + ウクライナ関連1本を同一DBに同居させる。"""
    sustainability_titles = [
        "EUDR施行直前：森林破壊規制が原料調達と産地外交をどう変えるか",
        "CBAM本格運用：炭素価格が新しい通商摩擦の軸になる",
        "EU包装規則（PPWR）適用開始：循環経済が素材安全保障政策になる",
        "強制労働規制の地政学化：EUの製品禁止と米国の関税措置が供給網を二重に圧迫",
        "紅海・ホルムズ海峡リスク：物流安全保障とScope 3が衝突する",
    ]
    documents = [
        {"document_id": f"sus-{i}", "title": title, "extracted_text": title,
         "fetched_at": f"2026-09-01T05:{36 + i}:00+00:00"}
        for i, title in enumerate(sustainability_titles)
    ]
    documents.append({
        "document_id": "ukr-1", "title": "ウクライナ戦況アップデート：プーチン政権の停戦姿勢",
        "extracted_text": "プーチン大統領はNATOの拡大を引き続き警戒している。",
        "fetched_at": "2026-08-31T10:00:00+00:00",
    })
    return FakeSupabaseClient(tables={"geo_documents": documents})


def test_build_source_pack_query_relevance_excludes_unrelated_sustainability_docs():
    client = _mixed_documents_client()
    source_pack = engine.build_source_pack(
        client, None, "現在のプーチンのウクライナ戦争に対する目的と、今後取り得る方向性を分析してください。")
    assert source_pack["document_ids"] == ["ukr-1"]


def test_build_source_pack_query_relevance_excludes_unrelated_ukraine_doc():
    client = _mixed_documents_client()
    source_pack = engine.build_source_pack(
        client, None, "EUDR施行によって原料調達や産地外交はどう変わるか説明してください。")
    assert "ukr-1" not in source_pack["document_ids"]
    assert "sus-0" in source_pack["document_ids"]


def _hormuz_taiwan_contamination_client():
    """Phase 10-R(Source Pack Retrieval Precision Hotfix、ユーザー指示): ホルムズ海峡
    クエリの"MoU"（Strong Anchor、Latin語3文字の略語）が、無関係な台湾海峡記事の英文中の
    "mounted"/"mountain"/"mouth"へsubstring一致し、Source Pack上位に混入した実際の
    バグ（Phase 10 Gold Regression、Hormuz production run、document_id=0ecc16ff-.../
    6f49164b-.../1d0a36df-...で確認）を再現するfixture。当初「海峡」という汎用Kanji語が
    原因と仮説を立てたが、実文書を直接調査した結果、実際の混入原因はKatakana/Kanjiの
    Genericフィルタではなく、この"MoU"のASCII Latin語word boundary問題だったと判明した
    （診断の訂正、ユーザー報告済み）。"""
    return FakeSupabaseClient(tables={"geo_documents": [
        {"document_id": "taiwan-strait-doc", "theme_id": THEME_ID,
         "title": "Record ships spotted around Taiwan Strait last month",
         "extracted_text": "The Coast Guard's patrol cutter is pictured camera-mounted on a "
                            "mountain overlooking the mouth of the strait.",
         "fetched_at": "2026-09-10T00:00:00+00:00"},
        {"document_id": "hormuz-doc", "theme_id": THEME_ID,
         "title": "イラン、ホルムズ海峡でのMoU後も通航妨害を継続",
         "extracted_text": "イランはホルムズ海峡でオマーンとの交渉枠組みの一部として通航制限を主張している。",
         "fetched_at": "2026-08-01T00:00:00+00:00"},
    ]})


def test_build_source_pack_ascii_acronym_substring_inside_unrelated_word_does_not_admit_offtopic_document():
    """Phase 10-R fixture A(訂正版): "MoU"が"mounted"/"mountain"/"mouth"等、無関係な
    英単語の内部にsubstring一致するだけでは台湾記事を候補化しないことを確認する
    （実際に発生したHormuz production run contaminationの再現テスト）。"""
    client = _hormuz_taiwan_contamination_client()
    source_pack = engine.build_source_pack(
        client, THEME_ID,
        "イランは2026年のイスラマバードMoUに基づき、ホルムズ海峡の自由な通航を許容する方向に"
        "向かうか、それとも通航料徴収という形で海峡管理への実効支配を継続しようとするか。")
    assert "taiwan-strait-doc" not in source_pack["document_ids"]


def test_build_source_pack_genuine_hormuz_document_still_admitted():
    """Phase 10-R fixture B: 同じqueryで、Strong Anchor（イラン/ホルムズ/MoU）に実際に
    独立した語として一致するホルムズ関連文書は候補化されることを確認する（fixture Aの裏返し）。"""
    client = _hormuz_taiwan_contamination_client()
    source_pack = engine.build_source_pack(
        client, THEME_ID,
        "イランは2026年のイスラマバードMoUに基づき、ホルムズ海峡の自由な通航を許容する方向に"
        "向かうか、それとも通航料徴収という形で海峡管理への実効支配を継続しようとするか。")
    assert "hormuz-doc" in source_pack["document_ids"]


def test_build_source_pack_ascii_acronym_genuine_word_boundary_mention_still_matches():
    """Phase 10-R: "MoU"が独立した語として実際に出現する英文文書（word boundary一致）は
    正しく候補化されることを確認する（word boundary判定自体が過剰に厳しくなっていないか）。"""
    client = FakeSupabaseClient(tables={"geo_documents": [
        {"document_id": "en-mou-doc", "theme_id": THEME_ID,
         "title": "Iran and Oman sign new MoU on Strait of Hormuz transit rules",
         "extracted_text": "The MoU, signed in Islamabad, defers long-term strait management "
                            "to further Iran-Oman talks.",
         "fetched_at": "2026-06-18T00:00:00+00:00"},
    ]})
    source_pack = engine.build_source_pack(
        client, THEME_ID,
        "イランは2026年のイスラマバードMoUに基づき、ホルムズ海峡の自由な通航を許容する方向に"
        "向かうか、それとも通航料徴収という形で海峡管理への実効支配を継続しようとするか。")
    assert "en-mou-doc" in source_pack["document_ids"]


def test_build_source_pack_taiwan_query_still_admits_taiwan_document():
    """Phase 10-R fixture C: 逆に台湾海峡についてのqueryでは、台湾（Strong Anchor）に
    一致する台湾記事が正常に候補化される（Hotfixが過剰にRecallを落とさないことの確認）。"""
    client = FakeSupabaseClient(tables={"geo_documents": [
        {"document_id": "taiwan-strait-doc", "theme_id": THEME_ID,
         "title": "台湾海峡における中国海軍の活動が急増",
         "extracted_text": "台湾周辺で中国海警の艦艇が確認される頻度が増加している。",
         "fetched_at": "2026-09-10T00:00:00+00:00"},
    ]})
    source_pack = engine.build_source_pack(
        client, THEME_ID, "台湾海峡における中国海軍の最近の活動状況を分析してください。")
    assert "taiwan-strait-doc" in source_pack["document_ids"]


def test_build_source_pack_strong_anchor_match_ranked_above_generic_only_match():
    """Phase 10-R fixture D: document_limitで絞られる場面でも、Strong Anchor（word
    boundary一致するMoU）に一致した文書がGeneric一致のみの文書より先に採用されることを
    確認する（採用上限に達した場合、Generic-onlyの文書が押し出される）。"""
    generic_only_docs = [
        {"document_id": f"generic-{i}", "theme_id": THEME_ID,
         "title": "海峡の自由な通航に関する一般的な方向性の議論",
         "extracted_text": "海峡の自由な通航について、今後の方向性を検討する。",
         "fetched_at": f"2026-09-{10 + i:02d}T00:00:00+00:00"}
        for i in range(3)
    ]
    client = FakeSupabaseClient(tables={"geo_documents": generic_only_docs + [
        {"document_id": "hormuz-doc", "theme_id": THEME_ID,
         "title": "イラン、ホルムズ海峡でのMoU後も通航妨害を継続",
         "extracted_text": "イランはホルムズ海峡でオマーンとの交渉枠組みの一部として通航制限を主張している。",
         "fetched_at": "2026-08-01T00:00:00+00:00"},
    ]})
    source_pack = engine.build_source_pack(
        client, THEME_ID,
        "イランは2026年のイスラマバードMoUに基づき、ホルムズ海峡の自由な通航を許容する方向に"
        "向かうか、それとも通航料徴収という形で海峡管理への実効支配を継続しようとするか。",
        document_limit=2)
    assert source_pack["document_ids"][0] == "hormuz-doc"


def test_build_source_pack_generic_only_fallback_when_query_has_no_strong_anchor():
    """Phase 10-R fixture F: query自体にStrong Anchor（Katakana/Latin）が1件も無く、
    Generic語も1件しか抽出できない場合、Generic 2件以上要求ルールでRetrievalが常に
    ゼロ件になってしまわないよう、1件一致へfallbackすることを確認する。"""
    client = FakeSupabaseClient(tables={"geo_documents": [
        {"document_id": "doc-1", "theme_id": THEME_ID, "title": "規制強化の動向",
         "extracted_text": "今後の規制強化について", "fetched_at": "2026-09-01T00:00:00+00:00"},
    ]})
    source_pack = engine.build_source_pack(client, THEME_ID, "規制強化の動向を教えてください")
    assert source_pack["document_ids"] == ["doc-1"]


def test_build_source_pack_theme_id_filters_in_addition_to_query_relevance():
    client = FakeSupabaseClient(tables={
        "geo_themes": [{"theme_id": THEME_ID, "name_ja": "台湾有事", "code": "taiwan_strait"}],
        "geo_documents": [
            {"document_id": "doc-in-theme", "theme_id": THEME_ID, "title": "PLA機の越境report",
             "fetched_at": "2026-08-01T00:00:00+00:00"},
            {"document_id": "doc-other-theme", "theme_id": "other-theme", "title": "PLA機の越境report",
             "fetched_at": "2026-08-01T00:00:00+00:00"},
        ],
    })
    source_pack = engine.build_source_pack(client, THEME_ID, "PLAの越境について")
    assert source_pack["document_ids"] == ["doc-in-theme"]


def test_build_source_pack_returns_empty_when_no_query_relevant_documents():
    client = _mixed_documents_client()
    source_pack = engine.build_source_pack(client, None, "北朝鮮のKim Jong Unの意思決定について")
    assert source_pack["document_ids"] == []
    assert source_pack["text"] == "（参照できる情報がありません）"


def test_build_source_pack_extra_document_ids_included_despite_no_keyword_match():
    """Phase R1 Chunk4: External Researchが取得した英語文書は、日本語クエリ由来の
    Keywordとtitle/extracted_textがILIKEで一致しないことがある——extra_document_ids
    指定時はkeyword一致に関わらず必ず含まれることを確認する。"""
    client = FakeSupabaseClient(tables={"geo_documents": [
        {"document_id": "en-doc-1", "theme_id": THEME_ID,
         "title": "Commerce Department Admits Failure To Enforce AI Export Controls on China",
         "extracted_text": "Commerce Department Admits Failure To Enforce AI Export Controls on China",
         "fetched_at": "2026-09-03T00:00:00+00:00"},
    ]})
    source_pack = engine.build_source_pack(
        client, THEME_ID, "現在の米中間の半導体・AI規制をめぐる局面について分析してください。",
        extra_document_ids=["en-doc-1"])
    assert source_pack["document_ids"] == ["en-doc-1"]
    assert "en-doc-1" in source_pack["text"]


def test_build_source_pack_extra_document_ids_not_duplicated_when_already_keyword_matched():
    client = FakeSupabaseClient(tables={"geo_documents": [
        {"document_id": "doc-1", "theme_id": THEME_ID, "title": "半導体規制report",
         "extracted_text": "半導体規制について", "fetched_at": "2026-09-03T00:00:00+00:00"},
    ]})
    source_pack = engine.build_source_pack(
        client, THEME_ID, "半導体規制について教えてください", extra_document_ids=["doc-1"])
    assert source_pack["document_ids"] == ["doc-1"]


WELL_FORMED_RESPONSE = {
    "one_line_assessment": "PLAが中間線越境を続けている。",
    "claims": [{"claim_type": "fact", "text": "PLA機が越境した", "source_ref": "e-1"}],
    "stakeholders": [{"actor": "PLA"}],
    "main_scenario": {
        "description": "圧力の段階的強化", "confidence": "中", "supporting_evidence": ["頻度増加"],
        "selection_rationale": "越境頻度が過去水準を上回っており、他の仮説より説明力が高いため",
    },
    "strongest_counterargument": "訓練の一環の可能性",
    "retraction_conditions": ["頻度が減少した場合"],
    "business_impact": {"direct": None, "indirect": "警戒感の上昇", "unrelated_note": None},
    # Session2: case_analysisモードの必須項目（geo_expert_quality_guard._key_stakeholder_v12_check）
    "key_point_assessment": {
        "candidate_key_points": [{
            "key_point": "PLAの越境頻度が段階的に引き上げられているか", "why_it_could_be_key": "頻度増加の主因",
            "control_type": "unknown", "what_it_explains": "越境頻度の増加傾向", "what_it_does_not_explain": "個別の停止条件",
        }],
        "primary_key_point": "PLAの越境頻度が段階的に引き上げられているか", "selection_rationale": "頻度データが最も一貫して説明する",
        "hinge_structure": {"primary_hinge": "PLAの作戦方針"},
        "control_type": "unknown", "supporting_evidence_ids": None,
        "disconfirming_signal": "越境頻度が明確に減少に転じた場合", "confidence": "中",
    },
    # v1.2: case_analysisモードの必須項目（geo_expert_quality_guard._key_stakeholder_v12_check）
    "key_stakeholder_assessment": {
        "determination": "single", "selected_actor_ids": ["pla"],
        "rationale": "軍事作戦の実施可否を決定できるのはPLA中央軍事委員会のみのため",
        "confidence": "中",
    },
    "key_actor_profiles": [{
        "actor_ref": "pla", "actor": "PLA", "confidence": "中",
        "formal_role": "台湾海峡での軍事的圧力行使を担う実行部隊",
        "persistent_preference": "軍事的圧力を段階的に強めることで政治的譲歩を引き出す志向",
        "persistent_preference_evidence": [{
            "event_or_behavior": "過去の中間線越境頻度の段階的増加", "supports_or_contradicts": "supports",
            "source": "過去のEvent記録",
        }],
        "actor_intelligence_provenance": {
            "actor_intelligence_status": "sufficient", "profile_source": "actor_intelligence_candidate",
            "historical_episode_ids": [], "pattern_ids": [], "exception_episode_ids": [],
            "supporting_evidence_ids": [], "general_knowledge_used": False,
        },
        "actor_model_challenge": {
            "challenge_question": "段階的圧力という持続的傾向が、今回の越境頻度増加の説明として"
                                    "本当に適用できるか、単発の運用上の事情に過ぎない可能性はないか",
            "outcome": "inconclusive", "reasoning": None,
        },
    }],
}


def _mock_llm_client(response: dict) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response)))],
    )
    return client


def test_format_structural_context_resolves_entity_names_for_untagged_relationship_partner():
    """Phase3-0 Gap1 Integration Fix回帰テスト: build_foundation_packはIssueに未タグの
    Entityどうしのrelationshipも補完するため（geo_foundation_pack_service参照）、
    その相手側EntityがCaseのIssueに紐づくEntity一覧には含まれないことがある。この場合に
    Entity名の代わりにentity_id（UUID）がそのままプロンプトへ漏れないことを確認する
    （実DBでChina-Iran relationshipにて実際に発生したバグ）。"""
    client = FakeSupabaseClient(tables={
        "geo_cases": [{"case_id": "case-1", "case_title": "t", "original_question": "q"}],
        "geo_case_issues": [{"case_id": "case-1", "issue_id": "issue-1"}],
        "geo_entities": [
            {"entity_id": "iran", "canonical_name": "Iran", "entity_type": "country"},
            {"entity_id": "china", "canonical_name": "China", "entity_type": "country"},
        ],
        # IranのみがCaseのIssueにタグ付けされている（Chinaは未タグ）
        "geo_entity_issues": [{"entity_id": "iran", "issue_id": "issue-1"}],
        "geo_relationships": [{
            "relationship_id": "rel-1", "from_entity_id": "china", "to_entity_id": "iran",
            "relationship_type": "major_oil_buyer_discount_purchaser", "relationship_domain": "energy",
            "trend": "strengthening", "description": "割引原油の大量購入", "is_demo": False,
        }],
    })
    import geo_foundation_pack_service
    pack = geo_foundation_pack_service.build_foundation_pack(client, "case-1")

    text = engine._format_structural_context(client, pack)

    assert "china" not in text  # entity_id（小文字）がそのまま漏れていない
    assert "China→Iran" in text


def test_run_expert_request_persists_all_records():
    client = FakeSupabaseClient(tables={
        "geo_themes": [{"theme_id": THEME_ID, "name_ja": "台湾有事", "code": "taiwan_strait"}],
        "geo_events": [{
            "event_id": "e-1", "theme_id": THEME_ID, "actor": "PLA", "action": "越境",
            "verified": True, "created_at": "2026-08-01T00:00:00+00:00",
        }],
    })
    azure_client = _mock_llm_client(WELL_FORMED_RESPONSE)

    result = engine.run_expert_request(
        client, azure_client, "gpt-4o", "case_analysis", THEME_ID, "PLAの越境状況を分析して",
    )

    assert result["request"]["mode"] == "case_analysis"
    assert result["response"]["response_json"]["one_line_assessment"] == WELL_FORMED_RESPONSE["one_line_assessment"]
    assert result["quality_evaluation"]["failed_count"] == 0
    assert len(client.tables["geo_expert_requests"]) == 1
    assert len(client.tables["geo_source_packs"]) == 1
    assert len(client.tables["geo_expert_responses"]) == 1
    assert len(client.tables["geo_quality_evaluations"]) == 1


# ==================== Phase R1 Chunk3: Institution Intelligence ====================

def test_format_institution_intelligence_block_empty_when_no_institutions():
    assert engine._format_institution_intelligence_block({"institutions": []}) == ""
    assert engine._format_institution_intelligence_block({}) == ""


def test_format_institution_intelligence_block_includes_anti_personification_note():
    """擬人化防止の注意書きが実際にプロンプト文字列へ含まれることを確認する
    （「志向」「性格」を持つ主体として扱わないこと、という明示的な指示）。"""
    pack = {"institutions": [{
        "entity_id": "bis-id", "canonical_name": "BIS", "entity_type": "government",
        "profile_source": "institution_intelligence_active",
        "mandate": "輸出管理規則の執行",
        "decision_authority": {"description": "省令改定", "veto_players": ["Commerce Department"],
                                "decision_process": "行政手続き"},
        "constraints": ["NDAAの範囲内"],
        "internal_stakeholders": [{"name_or_role": "産業安全保障局", "position_or_influence": "規則起草"}],
        "external_dependencies": [{"dependency": "同盟国の輸出管理協調", "description": None}],
        "historical_decision_patterns": [{"pattern": "段階的な規制強化", "confidence": "中"}],
        "known_exceptions": [{"description": "緊急許可の例外運用", "confidence": "低"}],
    }]}
    text = engine._format_institution_intelligence_block(pack)
    assert "志向" in text and "性格" in text
    assert "BIS（entity_id: bis-id" in text
    assert "mandate: 輸出管理規則の執行" in text
    assert "Commerce Department" in text
    assert "段階的な規制強化" in text


# ── Geopolitical News DB / External Intelligence Layer Phase1: External Signal統合 ──

def test_build_source_pack_default_excludes_external_signals():
    """include_external_signals未指定（既定False）ではExternal Signal Retrieverを一切
    呼ばない（既存の全呼び出し元への後方互換を壊さない）。"""
    client = FakeSupabaseClient()
    source_pack = engine.build_source_pack(client, THEME_ID, "台湾海峡の最近の動きは？")
    assert source_pack["external_signals"] == []
    assert "External Signals" not in source_pack["text"]


def test_build_source_pack_include_external_signals_adds_independent_section(monkeypatch):
    """include_external_signals=True時のみExternal Signalsセクションが追加され、
    document_ids/evidence_idsとは独立したキーとして返る（Evidence Discipline非破壊の確認）。"""
    import geo_external_signal_retrieval_service

    fake_signals = [{
        "external_signal_id": "sig-1", "title": "Taiwan Strait tensions rise",
        "source_name": "Reuters", "source_domain": "reuters.com", "source_country": "United States",
        "published_at": "2026-09-05T00:00:00+00:00", "source_role": "discovery",
        "evidence_status": "unverified",
    }]
    monkeypatch.setattr(geo_external_signal_retrieval_service, "search_external_signals",
                         lambda client, query_text, **kwargs: fake_signals)

    client = FakeSupabaseClient()
    source_pack = engine.build_source_pack(
        client, THEME_ID, "台湾海峡の最近の動きは？", include_external_signals=True)

    assert source_pack["external_signals"] == fake_signals
    assert "External Signals" in source_pack["text"]
    assert "discovery/unverified" in source_pack["text"]
    assert "[external_signal_id: sig-1]" in source_pack["text"]
    assert source_pack["document_ids"] == []
    assert source_pack["evidence_ids"] == []


# ── Phase2A: External Event統合 ─────────────────────────────────────────

def test_build_source_pack_default_excludes_external_events():
    client = FakeSupabaseClient()
    source_pack = engine.build_source_pack(client, THEME_ID, "台湾海峡の最近の動きは？")
    assert source_pack["external_events"] == []
    assert "External Events" not in source_pack["text"]


# ==================== Foundation Context Builder v1 ====================
# Acceptance Test（E01-Late Regression、ユーザー指示）で発見・修正したRedundancy Filter
# バグの回帰テスト。実際に見つかったバグ: 重複判定の比較対象(existing_lower)がquery_text
# （分析依頼文）のみを見ており、Call1へ実際渡すsource_pack本文（already_known_text、
# BASE_FACTS相当）を見ていなかったため、既知情報の重複文書がRetrieval capを専有し、
# 本当に不足しているEvidenceが押し出されていた。

_FOUNDATION_QUERY_TEXT = "How is Zeldania currently managing access through the Corvia strait?"
_FOUNDATION_ALREADY_KNOWN_TEXT = (
    "Zeldania has long maintained an alternative pipeline route that bypasses "
    "the Corvia strait entirely, allowing continued oil exports even during blockades. "
    "(Reuters, 2026-01-10)"
)
_FOUNDATION_REDUNDANT_TEXT = (
    "Zeldania has long maintained an alternative pipeline route that bypasses "
    "the Corvia strait entirely, allowing continued oil exports even during blockades."
)
_FOUNDATION_NEW_TEXT = (
    "Zeldania announced new selective restrictions on foreign-flagged vessels "
    "transiting the Corvia strait, targeting only rival-aligned shipping while allowing "
    "others to pass."
)


def test_build_foundation_context_redundancy_check_uses_already_known_text_not_query_text_alone():
    """既知重複文書(redundant)は、query_textだけを比較対象にすると重複と判定されず
    誤って残ってしまう（Acceptance Testで実際に発生したバグ）。already_known_textに
    source_pack本文を渡すことで正しく除外されることを確認する。"""
    client = FakeSupabaseClient(tables={"geo_documents": [
        {"document_id": "doc-redundant", "extracted_text": _FOUNDATION_REDUNDANT_TEXT,
         "title": "Zeldania pipeline bypass", "published_at": "2026-01-10"},
    ]})

    # already_known_text省略時はquery_textのみとの比較になり、バグ再現時と同じ挙動
    # （query_textにはpipeline/bypass等の語が無いため重複と判定されず残ってしまう）。
    _, diag_without = engine.build_foundation_context(
        client, _FOUNDATION_QUERY_TEXT, as_of_date="2026-08-09")
    assert diag_without["rejected_redundant"] == 0
    assert diag_without["kept_total"] == 1

    # already_known_textにsource_pack本文（BASE_FACTS相当）を渡すと正しく除外される。
    context_text, diag_with = engine.build_foundation_context(
        client, _FOUNDATION_QUERY_TEXT, as_of_date="2026-08-09",
        already_known_text=_FOUNDATION_ALREADY_KNOWN_TEXT)
    assert diag_with["rejected_redundant"] == 1
    assert diag_with["kept_total"] == 0
    assert "doc-redundant" not in context_text


def test_build_foundation_context_keeps_genuinely_new_document_despite_already_known_text():
    """already_known_textとほぼ無関係の新規情報は、重複判定に引っかからず残ることを
    確認する（Redundancy Filterが既知情報と無関係な新規Evidenceまで水増しで除外して
    しまわないことのGate）。"""
    client = FakeSupabaseClient(tables={"geo_documents": [
        {"document_id": "doc-new", "extracted_text": _FOUNDATION_NEW_TEXT,
         "title": "Zeldania selective vessel restrictions", "published_at": "2026-07-01"},
    ]})

    context_text, diagnostics = engine.build_foundation_context(
        client, _FOUNDATION_QUERY_TEXT, as_of_date="2026-08-09",
        already_known_text=_FOUNDATION_ALREADY_KNOWN_TEXT)

    assert diagnostics["rejected_redundant"] == 0
    assert diagnostics["kept_total"] == 1
    assert "doc-new" in context_text


def test_build_foundation_context_excludes_future_documents():
    """as_of_dateより後の文書はTemporal Contamination対策として除外される
    （future evidence = 0 Gate）。"""
    client = FakeSupabaseClient(tables={"geo_documents": [
        {"document_id": "doc-future", "extracted_text": _FOUNDATION_NEW_TEXT,
         "title": "Zeldania future escalation", "published_at": "2026-09-01"},
    ]})

    context_text, diagnostics = engine.build_foundation_context(
        client, _FOUNDATION_QUERY_TEXT, as_of_date="2026-08-09",
        already_known_text=_FOUNDATION_ALREADY_KNOWN_TEXT)

    assert diagnostics["rejected_future"] == 1
    assert diagnostics["kept_total"] == 0
    assert "doc-future" not in context_text


def test_build_foundation_context_excludes_synthetic_and_stub_documents():
    """合成テストコーパス（TEST CORPUSマーカー）と、本文が無い/titleと同一のスタブ文書は
    Material Contextとして採用しない（synthetic/test evidence = 0 Gate）。"""
    client = FakeSupabaseClient(tables={"geo_documents": [
        {"document_id": "doc-synthetic",
         "extracted_text": "TEST CORPUS: Zeldania Corvia synthetic filler text for AI-to-AI Test Queries.",
         "title": "Zeldania Corvia synthetic filler", "published_at": "2026-07-01"},
        {"document_id": "doc-stub", "title": "Zeldania Corvia stub record",
         "extracted_text": "Zeldania Corvia stub record", "published_at": "2026-07-01"},
    ]})

    context_text, diagnostics = engine.build_foundation_context(
        client, _FOUNDATION_QUERY_TEXT, as_of_date="2026-08-09",
        already_known_text=_FOUNDATION_ALREADY_KNOWN_TEXT)

    # Corpus Integrity P0（2026-09-22）: doc-syntheticはTEST CORPUS markerを持つため、
    # 今は_relevant_rows_by_keywords側（_is_synthetic_test_corpus_row）で候補にすら
    # ならず除外される——build_foundation_context自身のrejected_stub_or_synthetic
    # カウントに乗るのはdoc-stub（text==titleのスタブ判定）の1件のみになる。
    # doc-synthetic自体が排除される、というこのテストの本来の意図は変わらない。
    assert diagnostics["rejected_stub_or_synthetic"] == 1
    assert diagnostics["kept_total"] == 0
    assert "doc-synthetic" not in context_text
    assert "doc-stub" not in context_text


def test_build_foundation_context_truncates_long_document_text_per_item():
    """Taiwan Blockade Case実Retrievalで発見: 実Corpusのextracted_textは数千〜数万字に
    及ぶことがあり、1件がToken量上限（_FOUNDATION_CONTEXT_MAX_CHARS）を専有すると、
    本来複数件入るはずの他のEvidenceがcap外へ押し出される。1件あたりの上限
    （_FOUNDATION_CONTEXT_MAX_ITEM_CHARS）で切り詰め、複数件が同時に入ることを確認する。"""
    long_text_1 = "Zeldania Corvia incident report. " + ("Detail sentence about the incident. " * 60)
    long_text_2 = "Zeldania Corvia follow-up analysis. " + ("Another detail sentence follows. " * 60)
    client = FakeSupabaseClient(tables={"geo_documents": [
        {"document_id": "doc-long-1", "extracted_text": long_text_1,
         "title": "Zeldania Corvia incident report", "published_at": "2026-07-01"},
        {"document_id": "doc-long-2", "extracted_text": long_text_2,
         "title": "Zeldania Corvia follow-up analysis", "published_at": "2026-07-05"},
    ]})

    context_text, diagnostics = engine.build_foundation_context(
        client, _FOUNDATION_QUERY_TEXT, as_of_date="2026-08-09",
        already_known_text=_FOUNDATION_ALREADY_KNOWN_TEXT)

    assert diagnostics["kept_total"] == 2
    assert "doc-long-1" in context_text and "doc-long-2" in context_text
    assert len(long_text_1) > engine._FOUNDATION_CONTEXT_MAX_ITEM_CHARS
    assert "…" in context_text


def test_build_foundation_context_prioritizes_relevance_over_pure_date_order():
    """Taiwan Blockade Case実Retrievalで発見: Layer1判定はKeyword集合のいずれか1件でも
    一致すれば通過するため、"China"のような単発の広範な語だけで一致した無関係文書が、
    date昇順だけの並べ替えでは本来主題に近い（複数Keywordに一致する）文書より先に
    採用されてしまう。relevance（一致Keyword数）を優先することで、日付が後でも
    より主題に近い文書が採用されることを確認する。"""
    noise_docs = [
        {"document_id": f"doc-noise-{i}",
         "extracted_text": f"Corvia trade policy note number {i}, unrelated to the Need query topic.",
         "title": f"Corvia trade policy note {i}", "published_at": f"2026-01-{i:02d}"}
        for i in range(1, 8)  # 7件、date昇順で先頭を専有する「Corvia」のみ一致のnoise
    ]
    target_doc = {
        "document_id": "doc-target",
        "extracted_text": "Zeldania Corvia access vessels report, directly on topic for the Need query.",
        "title": "Zeldania Corvia access vessels report", "published_at": "2026-08-01",
    }
    client = FakeSupabaseClient(tables={"geo_documents": noise_docs + [target_doc]})

    context_text, diagnostics = engine.build_foundation_context(
        client, _FOUNDATION_QUERY_TEXT, as_of_date="2026-08-09",
        already_known_text=_FOUNDATION_ALREADY_KNOWN_TEXT)

    assert "doc-target" in context_text


def test_build_source_pack_include_external_events_adds_independent_section(monkeypatch):
    import geo_external_event_service

    fake_events = [{
        "external_event_id": "ev-1", "actor1_name": "UNITED STATES", "actor2_name": "CHINA",
        "event_code": "112", "event_date": "2026-09-01",
    }]
    monkeypatch.setattr(geo_external_event_service, "search_recent_events_for_theme",
                         lambda client, **kwargs: fake_events)

    client = FakeSupabaseClient()
    source_pack = engine.build_source_pack(
        client, THEME_ID, "台湾海峡の最近の動きは？", include_external_events=True)

    assert source_pack["external_events"] == fake_events
    assert "External Events" in source_pack["text"]
    assert "unverified" in source_pack["text"]
    assert "[external_event_id: ev-1]" in source_pack["text"]
    assert source_pack["document_ids"] == []
    assert source_pack["evidence_ids"] == []
    assert source_pack["external_signals"] == []


# ==================== Foundation Context Resolver（Phase D、ユーザー指示） ====================
# geo_case_issues/geo_entity_issuesという手動Curated Linkが無いCaseでも、Actor
# Intelligenceが機能するようにするRuntime Activationの回帰テスト。

def test_resolve_runtime_actor_candidates_finds_curated_via_issue_link():
    client = FakeSupabaseClient(tables={
        "geo_case_issues": [{"case_id": "case-1", "issue_id": "issue-1"}],
        "geo_entity_issues": [{"issue_id": "issue-1", "entity_id": "trump-id"}],
        "geo_entities": [{"entity_id": "trump-id", "entity_type": "person",
                           "canonical_name": "Donald Trump"}],
    })
    result = engine.resolve_runtime_actor_candidates(
        client, case_id="case-1", query_text="", source_pack={"text": ""})
    assert result["entity_ids"] == {"trump-id"}
    assert result["activation_method"]["trump-id"] == "curated"


def test_resolve_runtime_actor_candidates_finds_situation_relevant_via_name_match():
    """Issue Linkが無くても、query_text/Source Pack本文にEntity名が現れれば
    situation_relevantとして解決される。"""
    client = FakeSupabaseClient(tables={
        "geo_entities": [
            {"entity_id": "china-id", "entity_type": "country", "canonical_name": "China"},
            {"entity_id": "hormuz-id", "entity_type": "strait", "canonical_name": "Strait of Hormuz"},
        ],
    })
    result = engine.resolve_runtime_actor_candidates(
        client, case_id=None, query_text="Recent China semiconductor policy",
        source_pack={"text": ""})
    assert result["entity_ids"] == {"china-id"}  # straitはACTOR_ENTITY_TYPES対象外
    assert result["activation_method"]["china-id"] == "situation_relevant"


def test_resolve_runtime_actor_candidates_curated_not_overwritten_by_situation_match():
    """同じEntityがCurated Linkにも名前一致にも該当する場合、"curated"のまま
    （優先順位が崩れない）。"""
    client = FakeSupabaseClient(tables={
        "geo_case_issues": [{"case_id": "case-1", "issue_id": "issue-1"}],
        "geo_entity_issues": [{"issue_id": "issue-1", "entity_id": "china-id"}],
        "geo_entities": [{"entity_id": "china-id", "entity_type": "country", "canonical_name": "China"}],
    })
    result = engine.resolve_runtime_actor_candidates(
        client, case_id="case-1", query_text="China policy", source_pack={"text": ""})
    assert result["activation_method"]["china-id"] == "curated"


def test_resolve_runtime_actor_candidates_empty_when_no_match():
    """Structure-first: 何も見つからなければActorを無理に作らず空を返す。"""
    client = FakeSupabaseClient(tables={
        "geo_entities": [{"entity_id": "china-id", "entity_type": "country", "canonical_name": "China"}],
    })
    result = engine.resolve_runtime_actor_candidates(
        client, case_id=None, query_text="今日の天気はどうですか", source_pack={"text": ""})
    assert result["entity_ids"] == set()
    assert result["activation_method"] == {}


# ==================== Context Availability / Activation Telemetry（Phase C） ====================

def test_build_foundation_context_status_reports_not_configured_when_flags_off(monkeypatch):
    monkeypatch.delenv("GEO_EXPERT_FOUNDATION_CONTEXT_V1_ENABLED", raising=False)
    monkeypatch.delenv("GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED", raising=False)
    monkeypatch.delenv("GEO_EXPERT_MEDIA_INTELLIGENCE_ENABLED", raising=False)
    status = engine.build_foundation_context_status(
        mode="case_analysis", profile="standard", source_pack={"text": "x", "document_ids": [], "event_ids": []},
        foundation_context_text="", foundation_context_diagnostics=None, case_id=None,
        foundation_pack=None, structural_text="", actor_intelligence_pack=None,
        actor_intelligence_text="", runtime_actor_candidates=None,
        media_intelligence_pack=None, media_intelligence_text="")
    assert status["situation_trajectory"]["status"] == "NOT_CONFIGURED"
    assert status["actor_intelligence"]["status"] == "NOT_CONFIGURED"
    assert status["relationship_intelligence"]["status"] == "NOT_CONFIGURED"
    assert status["media_perception"]["mechanism_a_pattern_profile"]["status"] == "NOT_CONFIGURED"
    assert status["structural_context"]["status"] == "NOT_APPLICABLE"  # case_id無し


def test_build_foundation_context_status_reports_ready_for_populated_source_pack():
    status = engine.build_foundation_context_status(
        mode="case_analysis", profile="standard",
        source_pack={"text": "some real content", "document_ids": ["d1"], "event_ids": ["e1"]},
        foundation_context_text="", foundation_context_diagnostics=None, case_id=None,
        foundation_pack=None, structural_text="", actor_intelligence_pack=None,
        actor_intelligence_text="", runtime_actor_candidates=None,
        media_intelligence_pack=None, media_intelligence_text="")
    assert status["source"]["status"] == "READY"
    assert status["source"]["item_count"] == 2
    assert status["source"]["call1_included"] is True


def test_build_foundation_context_status_empty_source_pack_reports_empty():
    status = engine.build_foundation_context_status(
        mode="case_analysis", profile="standard",
        source_pack={"text": "（参照できる情報がありません）", "document_ids": [], "event_ids": []},
        foundation_context_text="", foundation_context_diagnostics=None, case_id=None,
        foundation_pack=None, structural_text="", actor_intelligence_pack=None,
        actor_intelligence_text="", runtime_actor_candidates=None,
        media_intelligence_pack=None, media_intelligence_text="")
    assert status["source"]["status"] == "EMPTY"


def test_build_foundation_context_status_actor_ready_reports_activation_method(monkeypatch):
    monkeypatch.setenv("GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED", "1")
    pack = {"key_actors": [{"entity_id": "china-id", "canonical_name": "China",
                             "relevant_relationships": [{"relationship_id": "rel-1"}],
                             "directional_relationship_perceptions": []}],
            "known_ids": {"rel-1"}}
    status = engine.build_foundation_context_status(
        mode="case_analysis", profile="standard", source_pack={"text": "x", "document_ids": [], "event_ids": []},
        foundation_context_text="", foundation_context_diagnostics=None, case_id=None,
        foundation_pack=None, structural_text="", actor_intelligence_pack=pack,
        actor_intelligence_text="【Actor Intelligence Pack】China...",
        runtime_actor_candidates={"activation_method": {"china-id": "situation_relevant"}},
        media_intelligence_pack=None, media_intelligence_text="")
    assert status["actor_intelligence"]["status"] == "READY"
    assert status["actor_intelligence"]["activation_method"] == ["situation_relevant"]
    assert status["relationship_intelligence"]["status"] == "READY"
    assert status["relationship_intelligence"]["item_count"] == 1
    assert "geo_relationships(actor_intelligence)" in status["relationship_intelligence"]["sources"]


# ---- Phase 6A-4(Repair/Rebinding、ユーザー指示) ----

def _entity_client(**extra_tables):
    tables = {
        "geo_entities": [
            {"entity_id": "iran-id", "canonical_name": "Iran", "name_en": "Iran",
             "name_ja": None, "local_name": None, "entity_type": "country"},
        ],
    }
    tables.update(extra_tables)
    return FakeSupabaseClient(tables=tables)


def test_rebind_leaves_visible_entity_id_untouched():
    client = _entity_client()
    response = {"key_actor_profiles": [{"actor_ref": "a1", "actor": "Iran", "entity_id": "iran-id"}]}
    touched = engine._rebind_or_unresolve_invalid_entity_references(
        client, response, stage1_visible_entity_ids={"iran-id"})
    assert touched == []
    assert response["key_actor_profiles"][0]["entity_id"] == "iran-id"


def test_rebind_unresolves_entity_id_not_found_in_geo_entities():
    """TSMC Caseの実例（document_idがentity_idとして誤用された）の回帰テスト。"""
    client = _entity_client()
    response = {"key_actor_profiles": [
        {"actor_ref": "actor_tw_trade", "actor": "台湾貿易当局", "entity_id": "doc-id-not-an-entity"},
    ]}
    touched = engine._rebind_or_unresolve_invalid_entity_references(
        client, response, stage1_visible_entity_ids=set())
    assert response["key_actor_profiles"][0]["entity_id"] is None
    assert touched == [{
        "actor_ref": "actor_tw_trade", "original_invalid_reference": "doc-id-not-an-entity",
        "resolved_entity_id": None, "resolution_method": "not_found_in_geo_entities", "confidence": None,
    }]


def test_rebind_keeps_entity_id_when_real_entity_name_matches_actor_label():
    client = _entity_client()
    response = {"key_actor_profiles": [{"actor_ref": "a1", "actor": "Iran", "entity_id": "iran-id"}]}
    # stage1_visible_entity_idsに含まれない（=今回Stage1へは提示しなかった）が、
    # geo_entitiesには実在し、かつ名前が一致するため rebind される。
    touched = engine._rebind_or_unresolve_invalid_entity_references(
        client, response, stage1_visible_entity_ids=set())
    assert response["key_actor_profiles"][0]["entity_id"] == "iran-id"
    assert touched == [{
        "actor_ref": "a1", "original_invalid_reference": "iran-id",
        "resolved_entity_id": "iran-id", "resolution_method": "name_matched_existing_entity",
        "confidence": "high",
    }]


def test_rebind_unresolves_entity_id_when_name_does_not_match():
    client = _entity_client()
    response = {"key_actor_profiles": [
        {"actor_ref": "a1", "actor": "Saudi Arabia", "entity_id": "iran-id"},
    ]}
    touched = engine._rebind_or_unresolve_invalid_entity_references(
        client, response, stage1_visible_entity_ids=set())
    assert response["key_actor_profiles"][0]["entity_id"] is None
    assert touched[0]["resolution_method"] == "name_mismatch_unresolved"


def test_rebind_never_creates_new_entities():
    """DBへの新規Entity自動作成が一切行われないこと（geo_entitiesテーブルへのinsertなし）。"""
    client = _entity_client()
    response = {"key_actor_profiles": [
        {"actor_ref": "a1", "actor": "Unknown Actor", "entity_id": "totally-fabricated-id"},
    ]}
    before = len(client.tables["geo_entities"])
    engine._rebind_or_unresolve_invalid_entity_references(
        client, response, stage1_visible_entity_ids=set())
    assert len(client.tables["geo_entities"]) == before


def test_rebind_ignores_profiles_without_entity_id():
    client = _entity_client()
    response = {"key_actor_profiles": [{"actor_ref": "a1", "actor": "TSMC", "entity_id": None}]}
    touched = engine._rebind_or_unresolve_invalid_entity_references(
        client, response, stage1_visible_entity_ids=set())
    assert touched == []
    assert response["key_actor_profiles"][0]["entity_id"] is None


# ---- Phase 6E-5/6E-6(Entity Identity Integrity、ユーザー指示) ----
# Regression Fixture A-F（ユーザー指定）。A/B/CはIdentity Consistency invariant
# （entity_idはStage1提示範囲内で正当だが、actor名がそのcanonical_nameと矛盾する
# ケース）を検証する。DはNamespace Integrity（既存rebind、document_id誤用の回帰）、
# Eは同一run内でのIntegrity Violation検出、Fは既存正常Caseでのregressionなしを検証する。

_XI_ENTITY_ID = "f34571d3-4742-41b7-8648-5b3ee13b7720"


def test_fixture_a_canonical_xi_entity_id_with_correct_name_is_valid():
    """A: canonical Xi entity_id + "Xi Jinping" → valid（何も変更しない）。"""
    response = {"key_actor_profiles": [{"actor_ref": "a1", "actor": "Xi Jinping", "entity_id": _XI_ENTITY_ID}]}
    mismatches = engine._resolve_entity_identity_consistency(
        response, stage1_visible_entity_names={_XI_ENTITY_ID: "Xi Jinping"})
    assert mismatches == []
    assert response["key_actor_profiles"][0]["entity_id"] == _XI_ENTITY_ID


def test_fixture_b_canonical_xi_entity_id_with_approved_alias_is_valid():
    """B: canonical Xi entity_id + approved alias → valid。"""
    response = {"key_actor_profiles": [
        {"actor_ref": "a1", "actor": "習近平国家主席（Xi Jinping）", "entity_id": _XI_ENTITY_ID},
    ]}
    mismatches = engine._resolve_entity_identity_consistency(
        response, stage1_visible_entity_names={_XI_ENTITY_ID: "Xi Jinping"})
    assert mismatches == []
    assert response["key_actor_profiles"][0]["entity_id"] == _XI_ENTITY_ID


def test_fixture_c_canonical_xi_entity_id_with_unrelated_name_is_invalid():
    """C: canonical Xi entity_id + "Taiwan trade authority" → invalid/unresolved。
    6E-5で埋めた穴の直接回帰: entity_idがstage1_visible_entity_ids内で正当でも、
    LLMが付けたactor名が矛盾する場合はnull化されること。"""
    response = {"key_actor_profiles": [
        {"actor_ref": "actor_tw_trade", "actor": "Taiwan trade authority", "entity_id": _XI_ENTITY_ID},
    ]}
    mismatches = engine._resolve_entity_identity_consistency(
        response, stage1_visible_entity_names={_XI_ENTITY_ID: "Xi Jinping"})
    assert response["key_actor_profiles"][0]["entity_id"] is None
    assert mismatches == [{
        "entity_id": _XI_ENTITY_ID, "model_supplied_name": "Taiwan trade authority",
        "canonical_name": "Xi Jinping", "identity_match_status": "mismatch",
        "resolution_method": "identity_mismatch_unresolved",
    }]


def test_fixture_d_document_id_used_as_entity_id_is_invalid():
    """D: document_idをentity_idとして使用 → invalid（既存6A-4 rebindの回帰確認）。"""
    client = _entity_client()
    response = {"key_actor_profiles": [
        {"actor_ref": "actor_tw_trade", "actor": "台湾貿易当局", "entity_id": "doc-id-not-an-entity"},
    ]}
    touched = engine._rebind_or_unresolve_invalid_entity_references(
        client, response, stage1_visible_entity_ids=set())
    assert response["key_actor_profiles"][0]["entity_id"] is None
    assert touched[0]["resolution_method"] == "not_found_in_geo_entities"


def test_fixture_e_same_entity_id_bound_to_different_actor_names_within_one_run_is_detected():
    """E: 同じentity_idが1 run内で異なるactor nameへbind → integrity violation検出。
    6E-5のIdentity Consistency invariantは各profileを独立に検証するため、この状況
    （同一entity_idを異なる名前で複数profileが名乗る）では、canonical_nameと一致しない
    方が確実にmismatch検出される（一致する側のみ有効なentity_idとして残る）。"""
    response = {"key_actor_profiles": [
        {"actor_ref": "a1", "actor": "Xi Jinping", "entity_id": _XI_ENTITY_ID},
        {"actor_ref": "a2", "actor": "Taiwan trade authority", "entity_id": _XI_ENTITY_ID},
    ]}
    mismatches = engine._resolve_entity_identity_consistency(
        response, stage1_visible_entity_names={_XI_ENTITY_ID: "Xi Jinping"})
    assert len(mismatches) == 1
    assert mismatches[0]["model_supplied_name"] == "Taiwan trade authority"
    assert response["key_actor_profiles"][0]["entity_id"] == _XI_ENTITY_ID
    assert response["key_actor_profiles"][1]["entity_id"] is None


def test_fixture_f_existing_normal_case_no_regression():
    """F: Hormuz等既存正常Case → regressionなし。entity_idが未知（名前情報を持たない）
    な場合は判定不能としてスキップし、何も変更しないこと。"""
    response = {"key_actor_profiles": [
        {"actor_ref": "a1", "actor": "Iran", "entity_id": "iran-id"},
    ]}
    mismatches = engine._resolve_entity_identity_consistency(
        response, stage1_visible_entity_names={})
    assert mismatches == []
    assert response["key_actor_profiles"][0]["entity_id"] == "iran-id"


# ---- Phase 6A-1(Saudi-Israel Actor Routing、ユーザー指示、Observability専用) ----

def test_detect_unused_rich_actor_intelligence_flags_unused_rich_entity():
    """Saudi-Israel Caseの実例回帰テスト: partial以上のActor Intelligenceが
    利用可能だったentity_idが、最終key_actor_profilesのどのentity_idとしても
    使われなかった場合に検出する（内容は一切変更しない）。"""
    actor_intelligence_pack = {"key_actors": [
        {"entity_id": "trump-id", "canonical_name": "Donald Trump", "actor_intelligence_status": "partial"},
        {"entity_id": "us-id", "canonical_name": "United States", "actor_intelligence_status": "insufficient"},
    ]}
    response = {"key_actor_profiles": [{"actor_ref": "actor_us", "actor": "United States", "entity_id": "us-id"}]}
    unused = engine._detect_unused_rich_actor_intelligence(actor_intelligence_pack, response)
    assert unused == [{"entity_id": "trump-id", "canonical_name": "Donald Trump",
                        "actor_intelligence_status": "partial"}]
    # 内容は変更しない
    assert response["key_actor_profiles"][0]["entity_id"] == "us-id"


def test_detect_unused_rich_actor_intelligence_empty_when_rich_entity_is_used():
    actor_intelligence_pack = {"key_actors": [
        {"entity_id": "trump-id", "canonical_name": "Donald Trump", "actor_intelligence_status": "partial"},
    ]}
    response = {"key_actor_profiles": [{"actor_ref": "a1", "actor": "Trump", "entity_id": "trump-id"}]}
    assert engine._detect_unused_rich_actor_intelligence(actor_intelligence_pack, response) == []


def test_detect_unused_rich_actor_intelligence_ignores_insufficient_status():
    actor_intelligence_pack = {"key_actors": [
        {"entity_id": "x-id", "canonical_name": "X", "actor_intelligence_status": "insufficient"},
    ]}
    response = {"key_actor_profiles": []}
    assert engine._detect_unused_rich_actor_intelligence(actor_intelligence_pack, response) == []


def test_detect_unused_rich_actor_intelligence_empty_when_no_pack():
    assert engine._detect_unused_rich_actor_intelligence(None, {"key_actor_profiles": []}) == []


# ---- Phase 7-1A(Institutionally-Constrained Controller Packaging、ユーザー指示) ----

def test_build_institutional_controller_context_surfaces_unresolved_congress_mention():
    """US-China半導体Caseの実データ回帰: Trumpのstructural_constraints内の「議会」が、
    Foundation Pack/Actor Intelligence Packのどのcanonical_nameにも一致しないため、
    entity_id無しのCandidateとして検出されること。"""
    source_pack_text = "半導体輸出規制に関する報道。"
    actor_intelligence_text = (
        "- Donald Trump（entity_id: trump-id）\n"
        "  core_profile.structural_constraints: 米国憲法上の三権分立・議会の予算/条約承認権による制約"
    )
    foundation_pack = {"2_relevant_entities": [
        {"entity_id": "trump-id", "canonical_name": "Donald Trump"},
        {"entity_id": "bis-id", "canonical_name": "Bureau of Industry and Security (BIS)"},
    ]}
    actor_intelligence_pack = {"key_actors": [{"entity_id": "trump-id", "canonical_name": "Donald Trump"}]}
    candidates = engine._build_institutional_controller_context(
        source_pack_text=source_pack_text, actor_intelligence_text=actor_intelligence_text,
        foundation_pack=foundation_pack, actor_intelligence_pack=actor_intelligence_pack,
        institution_intelligence_pack=None)
    assert candidates == [{
        "controller_label": "議会", "canonical_entity_id": None,
        "actor_intelligence_status": "unavailable_no_canonical_entity",
        "sources": ["actor_intelligence_pack"],
    }]


def test_build_institutional_controller_context_excludes_already_resolved_entities():
    """既にFoundation Pack/Institution Intelligence Packでentity_id解決済みの主体
    （BIS等）は、たとえテキスト中に接尾語パターンが含まれていても重複掲載しない。"""
    source_pack_text = "米国商務省（輸出管理当局）が規則を発表した。"
    foundation_pack = {"2_relevant_entities": [
        {"entity_id": "bis-id", "canonical_name": "米国商務省（輸出管理当局）"},
    ]}
    institution_intelligence_pack = {"institutions": [
        {"entity_id": "bis-id", "canonical_name": "米国商務省（輸出管理当局）"},
    ]}
    candidates = engine._build_institutional_controller_context(
        source_pack_text=source_pack_text, actor_intelligence_text="",
        foundation_pack=foundation_pack, actor_intelligence_pack=None,
        institution_intelligence_pack=institution_intelligence_pack)
    assert candidates == []


def test_build_institutional_controller_context_empty_when_no_mentions():
    candidates = engine._build_institutional_controller_context(
        source_pack_text="通常の報道記事。", actor_intelligence_text="",
        foundation_pack=None, actor_intelligence_pack=None, institution_intelligence_pack=None)
    assert candidates == []


def test_format_institutional_controller_context_block_empty_for_no_candidates():
    assert engine._format_institutional_controller_context_block([]) == ""


def test_format_institutional_controller_context_block_does_not_force_usage():
    text = engine._format_institutional_controller_context_block([
        {"controller_label": "議会", "canonical_entity_id": None,
         "actor_intelligence_status": "unavailable_no_canonical_entity", "sources": ["source_pack"]},
    ])
    assert "議会" in text
    assert "使用を強制しない" in text


# ---- Phase 7-2K-A/B/C(ユーザー指示): Deterministic Candidate Set → LLM Materiality
# Selection。PHASE_7_2J_KEY_ACTOR_SELECTION_VALIDATION_V1.mdで確認した通り、3種の
# 異なるPrompt介入がすべて失敗したため、責務分離（System=Candidate Builder、
# LLM=Materiality Selection）へ再設計した。

def test_build_actor_candidate_set_dedupes_across_packs_and_sets_richness():
    foundation_pack = {"2_relevant_entities": [
        {"entity_id": "us-id", "canonical_name": "United States", "entity_type": "country"},
        {"entity_id": "trump-id", "canonical_name": "Donald Trump", "entity_type": "person"},
    ]}
    actor_pack = {"key_actors": [
        {"entity_id": "trump-id", "canonical_name": "Donald Trump",
         "actor_intelligence_status": "partial", "selection_reasons": ["issue_linked"]},
    ]}
    institution_pack = {"institutions": [
        {"entity_id": "bis-id", "canonical_name": "BIS", "entity_type": "ministry",
         "profile_source": "institution_intelligence_active"},
    ]}
    candidates = engine._build_deterministic_actor_candidate_set(
        None, foundation_pack=foundation_pack, actor_intelligence_pack=actor_pack,
        institution_intelligence_pack=institution_pack)
    by_id = {c["entity_id"]: c for c in candidates}
    assert set(by_id) == {"us-id", "trump-id", "bis-id"}
    # Trumpはfoundation_pack+actor_packの両方に出現するが1件へdedupeされ、
    # actor_packの情報でintelligence_availableが正しく立つ
    assert by_id["trump-id"]["intelligence_available"] is True
    assert by_id["trump-id"]["intelligence_richness"] == "partial"
    assert by_id["us-id"]["intelligence_available"] is False
    assert by_id["bis-id"]["intelligence_available"] is True
    assert by_id["bis-id"]["intelligence_richness"] == "active"


def test_build_actor_candidate_set_empty_when_no_packs():
    assert engine._build_deterministic_actor_candidate_set(
        None, foundation_pack=None, actor_intelligence_pack=None, institution_intelligence_pack=None) == []


def test_build_actor_candidate_set_resolves_entity_type_via_db_for_actor_pack_only_entries():
    """2026-09-21回帰: Actor Intelligence Pack `key_actors`はentity_type自体を持たない
    （Person中心設計の名残）ため、以前はentity_type="person"と一律決め打ちしていた。
    1-hop Relationship展開（Phase 7-2X）経由でcountry種別のEntityも混入するため、実DBへ
    問い合わせて正しいentity_typeへ補正することを確認する（"United States"がpersonでは
    なくcountryとして解決される）。"""
    from tests._fakes import FakeSupabaseClient
    client = FakeSupabaseClient(tables={"geo_entities": [
        {"entity_id": "us-country-id", "canonical_name": "United States", "entity_type": "country"},
    ]})
    actor_pack = {"key_actors": [
        {"entity_id": "us-country-id", "canonical_name": "United States",
         "actor_intelligence_status": "insufficient", "selection_reasons": ["high_leverage_relationship"]},
    ]}
    candidates = engine._build_deterministic_actor_candidate_set(
        client, foundation_pack=None, actor_intelligence_pack=actor_pack,
        institution_intelligence_pack=None)
    assert candidates[0]["entity_type"] == "country"


# ---- Phase 7-2K-R(ユーザー指示): Role-aware Candidate Context ----
# situation_roleは既存のcurated data(entity_type分類・決定的scoring reason・
# Institution Intelligenceのmandate)だけから導出する。model world knowledgeによる
# 補完・hardcoded person-specific role・current-office assumptionは一切使わない。

def test_derive_situation_role_uses_only_existing_signals_no_world_knowledge():
    """Donald Trumpのような人物に対して"大統領"等のofficeを断定しないこと
    （そのようなfieldがDBに存在しないため）——entity_type分類とscoring reasonの
    翻訳のみで構成されることを確認する。"""
    role = engine._derive_situation_role(
        entity_type="person", relationship_to_issue=["issue_linked", "veto_power"],
        institution_mandate=None)
    assert "個人主体" in role
    assert "拒否権" in role
    assert "大統領" not in role and "president" not in role.lower()
    assert "首相" not in role


def test_derive_situation_role_country_vs_person_distinct_labels():
    person_role = engine._derive_situation_role(
        entity_type="person", relationship_to_issue=["issue_linked", "veto_power"],
        institution_mandate=None)
    country_role = engine._derive_situation_role(
        entity_type="country", relationship_to_issue=["situation_relevant", "high_leverage_relationship"],
        institution_mandate=None)
    assert person_role != country_role
    assert "個人主体" in person_role
    assert "国家レベル主体" in country_role


def test_derive_situation_role_includes_institution_mandate_when_available():
    role = engine._derive_situation_role(
        entity_type="ministry", relationship_to_issue=[],
        institution_mandate="輸出管理規則の策定・執行")
    assert "輸出管理規則の策定・執行" in role


def test_derive_situation_role_unknown_when_no_safe_signal():
    assert engine._derive_situation_role(
        entity_type=None, relationship_to_issue=[], institution_mandate=None) == "unknown"


def test_build_actor_candidate_set_attaches_situation_role_per_candidate():
    foundation_pack = {"2_relevant_entities": [
        {"entity_id": "trump-id", "canonical_name": "Donald Trump", "entity_type": "person"},
    ]}
    actor_pack = {"key_actors": [
        {"entity_id": "trump-id", "canonical_name": "Donald Trump",
         "actor_intelligence_status": "partial", "selection_reasons": ["issue_linked", "veto_power"]},
    ]}
    candidates = engine._build_deterministic_actor_candidate_set(
        None, foundation_pack=foundation_pack, actor_intelligence_pack=actor_pack,
        institution_intelligence_pack=None)
    assert candidates[0]["situation_role"] != "unknown"
    assert "個人主体" in candidates[0]["situation_role"]


def test_format_actor_candidate_set_block_includes_situation_role():
    candidates = [
        {"entity_id": "trump-id", "name": "Donald Trump", "entity_type": "person",
         "situation_role": "個人主体、entity_type分類上、この局面で拒否権を持ちうる主体",
         "intelligence_available": True, "intelligence_richness": "partial",
         "source_reason": ["issue_linked"]},
    ]
    text = engine._format_actor_candidate_set_block(candidates)
    assert "situation_role" in text
    assert "個人主体" in text


def test_format_actor_candidate_set_block_empty_for_no_candidates():
    assert engine._format_actor_candidate_set_block([]) == ""


def test_format_actor_candidate_set_block_lists_all_candidates_without_ranking():
    candidates = [
        {"entity_id": "us-id", "name": "United States", "entity_type": "country",
         "intelligence_available": False, "intelligence_richness": "none",
         "source_reason": ["foundation_pack_relevant_entity"]},
        {"entity_id": "trump-id", "name": "Donald Trump", "entity_type": "person",
         "intelligence_available": True, "intelligence_richness": "partial",
         "source_reason": ["issue_linked"]},
    ]
    text = engine._format_actor_candidate_set_block(candidates)
    assert "United States" in text and "us-id" in text
    assert "Donald Trump" in text and "trump-id" in text
    assert "架空のIDを作らない" in text


def test_validate_actor_candidate_selection_flags_hallucinated_id():
    candidates = [{"entity_id": "trump-id"}, {"entity_id": "us-id"}]
    stage1_data = {"actor_candidate_selection": [
        {"candidate_entity_id": "trump-id", "materiality_reason": "r", "role_in_situation": "x", "confidence": "中"},
        {"candidate_entity_id": "fabricated-id-999", "materiality_reason": "r", "role_in_situation": "x", "confidence": "低"},
    ], "actor_candidate_gaps": []}
    diagnostics = engine._validate_actor_candidate_selection(stage1_data, candidates)
    assert diagnostics["matched_count"] == 1
    assert diagnostics["hallucinated_candidate_ids"] == ["fabricated-id-999"]
    assert diagnostics["candidate_set_size"] == 2


def test_validate_actor_candidate_selection_all_valid():
    candidates = [{"entity_id": "trump-id"}]
    stage1_data = {"actor_candidate_selection": [
        {"candidate_entity_id": "trump-id", "materiality_reason": "r", "role_in_situation": "x", "confidence": "高"},
    ]}
    diagnostics = engine._validate_actor_candidate_selection(stage1_data, candidates)
    assert diagnostics["hallucinated_candidate_ids"] == []
    assert diagnostics["matched_count"] == 1


# ---- Phase 7-2L(ユーザー指示): Selection-to-Profile Handoff Integrity ----
# PHASE_7_2K_R Run3で、actor_candidate_selectionでは選択・理由付けされた主体が
# 最終key_actor_profilesに現れないという内部矛盾が観測された。この関数は検知のみ
# 行う（entity_id書き換え・自動profile生成は一切しない）。

def test_validate_selection_profile_handoff_none_when_module_not_used():
    assert engine._validate_selection_profile_handoff({"key_actor_profiles": []}, None) is None


def test_validate_selection_profile_handoff_clean_when_consistent():
    selection_diagnostics = {"selected": [
        {"entity_id": "trump-id", "name": "Donald Trump"},
        {"entity_id": "us-id", "name": "United States"},
    ]}
    response = {"key_actor_profiles": [
        {"actor": "Donald Trump", "entity_id": "trump-id"},
        {"actor": "United States", "entity_id": "us-id"},
    ]}
    result = engine._validate_selection_profile_handoff(response, selection_diagnostics)
    assert result == {"selected_but_no_profile": [], "profile_but_not_selected": []}


def test_validate_selection_profile_handoff_detects_selected_but_no_profile():
    """PHASE_7_2K_R Run3の再現: Trumpはselectedだがkey_actor_profilesに存在しない。"""
    selection_diagnostics = {"selected": [
        {"entity_id": "us-id", "name": "United States"},
        {"entity_id": "mbs-id", "name": "Mohammed bin Salman"},
        {"entity_id": "netanyahu-id", "name": "Benjamin Netanyahu"},
        {"entity_id": "trump-id", "name": "Donald Trump"},
    ]}
    response = {"key_actor_profiles": [
        {"actor": "United States", "entity_id": "us-id"},
        {"actor": "Mohammed bin Salman", "entity_id": "mbs-id"},
        {"actor": "Benjamin Netanyahu", "entity_id": "netanyahu-id"},
    ]}
    result = engine._validate_selection_profile_handoff(response, selection_diagnostics)
    assert result["selected_but_no_profile"] == [{"entity_id": "trump-id", "name": "Donald Trump"}]
    assert result["profile_but_not_selected"] == []
    # 自動補完・entity_id書き換えをしていないこと(responseは呼び出し前後で不変)
    assert len(response["key_actor_profiles"]) == 3


def test_validate_selection_profile_handoff_detects_profile_but_not_selected():
    selection_diagnostics = {"selected": [{"entity_id": "us-id", "name": "United States"}]}
    response = {"key_actor_profiles": [
        {"actor": "United States", "entity_id": "us-id"},
        {"actor": "Unexpected Actor", "entity_id": "surprise-id"},
    ]}
    result = engine._validate_selection_profile_handoff(response, selection_diagnostics)
    assert result["profile_but_not_selected"] == [{"entity_id": "surprise-id", "actor": "Unexpected Actor"}]


# ---- Phase 7-1A Telemetry(ユーザー指示): Controller Intelligence Utilization ----

def test_classify_controller_utilization_used_when_promoted_with_causal_role():
    response = {"key_actor_profiles": [
        {"actor": "Bureau of Industry and Security (BIS)", "entity_id": "bis-id",
         "driving_forces": ["規制運用"]},
    ]}
    results = engine.classify_controller_intelligence_utilization(
        entity_backed_candidates=[{"entity_id": "bis-id", "canonical_name": "Bureau of Industry and Security (BIS)"}],
        label_only_candidates=[], response=response)
    assert results == [{"controller_label": "Bureau of Industry and Security (BIS)",
                         "canonical_entity_id": "bis-id", "status": "USED"}]


def test_classify_controller_utilization_used_for_label_only_self_assigned_actor():
    """TSMCの台湾貿易当局のような、entity_id無しでも自己命名actor_ref＋因果的役割
    込みで存在する場合はUSEDとする。"""
    response = {"key_actor_profiles": [
        {"actor": "台湾の貿易・産業当局（経済部国際貿易署など）", "entity_id": None,
         "driving_forces": ["対米協力"]},
    ]}
    results = engine.classify_controller_intelligence_utilization(
        entity_backed_candidates=[], label_only_candidates=[
            {"controller_label": "台湾当局", "canonical_entity_id": None,
             "actor_intelligence_status": "unavailable_no_canonical_entity", "sources": ["source_pack"]},
        ], response=response)
    assert results[0]["status"] == "USED"


def test_classify_controller_utilization_data_readiness_limited_when_no_entity_and_unused():
    response = {"key_actor_profiles": [{"actor": "Donald Trump", "entity_id": "trump-id"}]}
    results = engine.classify_controller_intelligence_utilization(
        entity_backed_candidates=[], label_only_candidates=[
            {"controller_label": "議会", "canonical_entity_id": None,
             "actor_intelligence_status": "unavailable_no_canonical_entity", "sources": ["actor_intelligence_pack"]},
        ], response=response)
    assert results == [{"controller_label": "議会", "canonical_entity_id": None,
                         "status": "DATA_READINESS_LIMITED"}]


def test_classify_controller_utilization_unavailable_when_entity_backed_but_ignored():
    response = {"key_actor_profiles": [{"actor": "Donald Trump", "entity_id": "trump-id"}]}
    results = engine.classify_controller_intelligence_utilization(
        entity_backed_candidates=[{"entity_id": "xi-id", "canonical_name": "Xi Jinping"}],
        label_only_candidates=[], response=response)
    assert results == [{"controller_label": "Xi Jinping", "canonical_entity_id": "xi-id",
                         "status": "UNAVAILABLE"}]


def test_classify_controller_utilization_available_when_mentioned_in_narrative_only():
    response = {
        "key_actor_profiles": [{"actor": "Donald Trump", "entity_id": "trump-id"}],
        "key_point_assessment": {"control_structure_summary": "Xi Jinpingの承認が必要となる。"},
    }
    results = engine.classify_controller_intelligence_utilization(
        entity_backed_candidates=[{"entity_id": "xi-id", "canonical_name": "Xi Jinping"}],
        label_only_candidates=[], response=response)
    assert results == [{"controller_label": "Xi Jinping", "canonical_entity_id": "xi-id",
                         "status": "AVAILABLE"}]


def test_classify_controller_utilization_considered_not_material_when_explicitly_rejected():
    response = {
        "key_actor_profiles": [{"actor": "Donald Trump", "entity_id": "trump-id"}],
        "key_point_assessment": {"rejected_candidates": [
            {"key_point": "Xi Jinpingの個人的裁量",
             "why_not_primary": "Xi Jinpingの裁量より制度的制約の方が決定的であるため。"},
        ]},
    }
    results = engine.classify_controller_intelligence_utilization(
        entity_backed_candidates=[{"entity_id": "xi-id", "canonical_name": "Xi Jinping"}],
        label_only_candidates=[], response=response)
    assert results == [{"controller_label": "Xi Jinping", "canonical_entity_id": "xi-id",
                         "status": "CONSIDERED_NOT_MATERIAL"}]
