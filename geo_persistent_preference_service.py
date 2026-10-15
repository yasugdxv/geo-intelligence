"""地政学インテリジェンス基盤: 地政学専門家AI v1.2 Persistent Preference再利用サービス。

既存のActive `actor_profile` Knowledge Item（`geo_knowledge_items`,
`knowledge_type=actor_profile`）の`structured_content.persistent_preference`を、
Stage 0でプロンプトへ再利用するための読み取りヘルパー（常時有効）と、Stage1の分析結果に
基づいて更新Candidateを提案する（人間レビュー前提、Active化は一切しない）ための
post-processingヘルパー（Feature Flagで既定無効）を提供する。

保存先は既存の`geo_knowledge_items`（`knowledge_type=actor_profile`、`structured_content`は
schemaレスjsonbで`additionalProperties: true`）をそのまま使い、新しいテーブル・カラムは
一切追加しない（DBマイグレーションなし）。承認・旧itemのsuperseded遷移は既存の
`geo_knowledge_service.review_knowledge_item`を人間が個別に呼ぶ（本モジュールは
Candidateの作成までを担当し、自動承認・自動supersedeは行わない）。
"""
import hashlib
import os
from datetime import datetime, timezone

import geo_knowledge_service

# Candidate自動生成のFeature Flag。既定は無効（管理者が明示的に有効化するまで作られない）。
PP_CANDIDATES_ENABLED_ENV = "GEO_EXPERT_PP_CANDIDATES_ENABLED"


def candidates_enabled() -> bool:
    return os.environ.get(PP_CANDIDATES_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


def _evidence_fingerprint(evidence: dict) -> str:
    """支持・反証Evidence 1件のFingerprint（重複防止用）。event_or_behavior・date_or_period・
    sourceを正規化してハッシュ化し、同じ出来事が繰り返し「新しいEvidence」として扱われ
    Candidateが際限なく作られるのを防ぐ。"""
    normalized = "|".join([
        (evidence.get("event_or_behavior") or "").strip(),
        (evidence.get("date_or_period") or "").strip(),
        (evidence.get("source") or "").strip(),
    ])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def get_active_actor_profile(foundation_pack: dict, entity_id: str) -> dict | None:
    """Foundation Packの`5_objectives_core_interests`（既にactive化されたactor_profileのみ、
    `geo_foundation_pack_service.build_foundation_pack`が既に取得済み）から、entity_idが
    一致するものを1件返す。新しいDB取得は行わない（既存の取得結果を再利用するのみ）。"""
    if not foundation_pack or not entity_id:
        return None
    for item in foundation_pack.get("5_objectives_core_interests") or []:
        content = item.get("structured_content") or {}
        if content.get("entity_id") == entity_id:
            return item
    return None


def format_persistent_preference_block(foundation_pack: dict, entity_names: dict) -> str:
    """Stage1プロンプトへ追記する、既存Persistent Preference仮説のブロックを組み立てる
    （Stage0、決定的処理、LLMを呼ばない）。見つからない場合は空文字を返す。"""
    if not foundation_pack:
        return ""
    lines = []
    for item in foundation_pack.get("5_objectives_core_interests") or []:
        content = item.get("structured_content") or {}
        pp = content.get("persistent_preference")
        if not pp:
            continue
        entity_id = content.get("entity_id")
        name = entity_names.get(entity_id, entity_id)
        lines.append(
            f"- {name}: {pp.get('statement')}"
            f"（as_of: {pp.get('as_of')}, confidence: {pp.get('confidence')}）"
        )
    if not lines:
        return ""
    return (
        "\n【既存のPersistent Preference仮説（DB、要再検証）】\n" + "\n".join(lines) +
        "\nこれを起点に、新しい支持・反証Evidenceがある場合のみ更新すること。理由なく作り直さない。"
        "既存仮説と比べて結論やconfidenceが実質的に変わった場合はpersistent_preference_change="
        "\"revised\"、根拠が増えただけで結論が変わらない場合は\"reinforced\"を設定すること。"
    )


def propose_persistent_preference_candidates(client, *, response_json: dict, foundation_pack: dict,
                                              actor_id: str) -> list:
    """Stage1（+Stage2）完了後のpost-processingとして呼ぶ（Feature Flag無効時は何もしない）。
    各`key_actor_profiles[]`について、以下をすべて満たす場合のみCandidateを1件作成する:
    (1) `persistent_preference_change == "revised"`（結論・confidenceの実質的変更）
    (2) Fingerprintで重複しない新しいEvidenceが1件以上ある
    (3) 同一entityに対して`status in (candidate, in_review)`のactor_profileが既に無い
    いずれかを満たさない場合はCandidateを作らない（鮮度切れのみの場合の再調査要求は、
    呼び出し元がidentified_knowledge_gapsを見て別途扱う。本関数の責務はCandidate作成のみ）。
    例外は呼び出し元で握りつぶされる想定（本処理の失敗が回答返却をブロックしないよう、
    run_expert_request_staged側でtry/exceptする）。
    戻り値: 作成したCandidateのdict一覧（何も作らなければ空リスト）。"""
    if not candidates_enabled() or not foundation_pack:
        return []

    created = []
    for kap in response_json.get("key_actor_profiles") or []:
        entity_id = kap.get("entity_id")
        if not entity_id or kap.get("persistent_preference_change") != "revised":
            continue

        active_item = get_active_actor_profile(foundation_pack, entity_id)
        if not active_item:
            continue  # 既存Active Profileが無いentityへの新規作成はPhase Bスコープ外

        existing_content = active_item.get("structured_content") or {}
        existing_pp = existing_content.get("persistent_preference") or {}
        existing_fingerprints = set(existing_pp.get("evidence_fingerprints") or [])

        candidate_evidence = kap.get("persistent_preference_evidence") or []
        fingerprinted = [(ev, _evidence_fingerprint(ev)) for ev in candidate_evidence]
        genuinely_new = [fp for _, fp in fingerprinted if fp not in existing_fingerprints]
        if not genuinely_new:
            continue

        pending_for_entity = [
            p for p in client.select("geo_knowledge_items", {
                "select": "knowledge_item_id,structured_content", "knowledge_type": "eq.actor_profile",
                "status": "in.(candidate,in_review)",
            })
            if (p.get("structured_content") or {}).get("entity_id") == entity_id
        ]
        if pending_for_entity:
            continue

        new_content = dict(existing_content)
        new_content["persistent_preference"] = {
            "statement": kap.get("persistent_preference"),
            "as_of": datetime.now(timezone.utc).date().isoformat(),
            "confidence": kap.get("confidence"),
            "supporting_evidence": [ev for ev in candidate_evidence if ev.get("supports_or_contradicts") == "supports"],
            "contradicting_evidence": [ev for ev in candidate_evidence if ev.get("supports_or_contradicts") == "contradicts"],
            "evidence_fingerprints": sorted(existing_fingerprints | set(genuinely_new)),
        }
        candidate = geo_knowledge_service.create_knowledge_item(
            client, knowledge_type="actor_profile", title=active_item.get("title") or "",
            structured_content=new_content,
            information_class=active_item.get("information_class") or "internal_hypothesis",
            created_by=actor_id, status="candidate", supersedes_id=active_item["knowledge_item_id"],
            # originは既存CHECK制約（geo_knowledge_items_origin_check）の許可値のみ使える。
            # 「Case分析から抽出された候補」を意味するcase_candidate_extractionが最も近い。
            origin="case_candidate_extraction",
        )
        created.append(candidate)
    return created
