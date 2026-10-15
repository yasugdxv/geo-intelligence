"""地政学ナレッジ＆インテリジェンスDB Phase2B-3A-1: Statement Candidate。

GKG annotation（geo_external_signal_annotations）のV2.1QUOTATIONS（これまで一度もパースされて
いなかった生列）から引用・声明のcandidateを抽出し、Human Review向けの提案層として提示する。

【最重要方針】speaker identityはGDELT Actor1/Actor2やGKG Personsから自動確定しない。
quotation/statement本文中の明示的attribution evidenceのみを候補材料とし、確定
（confirmed_statement_kind/confirmed_statement_text/confirmed_speaker_name/confirmed_speaker_type）
はHuman Reviewのみが行う。acceptedは「Statement/Speakerが確認済み」の状態を意味し、GDELT
Evidenceのみでは遷移できない（geo_statement_candidate_update_status RPCが4項目全て揃っている
ことを強制する）。

Phase2B-2BのEpisode Guard（split_group/artifact_group/outlier）を再利用する。acceptedな
artifact_group Split Candidateに該当する場合のみStatement Candidate行を生成しない。まだ
acceptedでない（candidate/reviewed状態の）Split/Artifact/Outlier Proposalが該当memberを
覆っている場合はunresolved_holdとして行を生成する（黙って除外しない）。

DELETE/INSERT/UPDATEは`geo_statement_candidate_apply_replace`RPC
（sql/2026-09-08_geo_statement_candidates.sql）1回にまとめて行う（annotation_id単位でatomic）。
Human Review状態更新は`geo_statement_candidate_update_status`RPC1回にまとめて行う。Python側から
geo_statement_candidatesへ直接client.insert/update/deleteしない。

article body取得は前提としない。Human Review前はActor Intelligence本体（geo_entities/
geo_knowledge_items）へは一切書き込まない。
"""
import hashlib
import re

import requests

from geo_external_signal_service import _select_in_chunked, _select_all_paginated

ALGORITHM_VERSION = "v1"  # fingerprintに含める。アルゴリズム変更時はここを上げる。

MIN_QUOTE_LENGTH = 20          # これ未満は雑音として生成しない
SPEAKER_PROXIMITY_EXPLICIT_CHARS = 30    # GKG verb併記＋直近隣接（将来のbody取得時用、現状データでは未到達）
SPEAKER_PROXIMITY_CLOSE_CHARS = 300      # これ以内の最寄り候補を proximate_name とする
SPEAKER_PROXIMITY_AMBIGUOUS_GAP_CHARS = 30  # 最寄りと次点の差がこれ未満なら ambiguous
TITLE_MATCH_MIN_WORDS = 2      # titleとquote_textの共有フレーズがこの語数以上ならdirect_quote_candidate寄り

MAX_REPLACE_RETRIES = 3

_ALLOWED_TRANSITIONS = {
    "candidate": {"reviewed", "accepted", "rejected"},
    "reviewed": {"accepted", "rejected"},
}
_FROZEN_STATUSES = ("reviewed", "accepted", "rejected")

_ORG_KEYWORDS = ("ministry", "council", "department", "committee", "agency", "corps", "court",
                  "office", "bureau", "commission", "party", "administration")

_UPDATE_STATUS_KNOWN_ERROR_PREFIXES = (
    "statement_candidate_not_found",
    "statement_candidate_invalid_transition",
    "statement_candidate_rejection_reason_required",
    "statement_candidate_accepted_requires_confirmation",
)


class StatementCandidateConflict(Exception):
    """同一annotation_idへの同時regenerate、またはPython側SELECTからRPC呼び出しまでの間の
    行変化により、RPC内のsnapshot比較が不一致になった場合に送出する。
    generate_statement_candidatesが自動的にリトライする。"""


class StatementCandidateValidationError(Exception):
    """annotation_idが存在しない等、呼び出し元のバグを示す。リトライしない。"""


def _bool_filter(value: bool) -> str:
    return "eq.true" if value else "eq.false"


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------
# GKG生列パース（annotation.raw_metadata.columnsから都度アドホックに読む。
# geo_connector_gdelt_stream.pyのGKGパーサ自体は変更しない）
# ---------------------------------------------------------------------

def _parse_gkg_quotations(raw_columns: list) -> list:
    if len(raw_columns) <= 22 or not raw_columns[22]:
        return []
    quotes = []
    for chunk in raw_columns[22].split("#"):
        parts = chunk.split("|")
        if len(parts) < 4:
            continue
        offset, length = _safe_int(parts[0]), _safe_int(parts[1])
        if offset is None or length is None:
            continue
        quote_text = parts[3].strip()
        if len(quote_text) < MIN_QUOTE_LENGTH:
            continue
        quotes.append({"offset": offset, "length": length, "verb": parts[2].strip(), "quote_text": quote_text})
    return quotes


def _parse_gkg_allnames(raw_columns: list) -> list:
    if len(raw_columns) <= 23 or not raw_columns[23]:
        return []
    items = []
    for part in raw_columns[23].split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, offset = part.rpartition(",")
        char_offset = _safe_int(offset)
        if not name or char_offset is None:
            continue
        items.append({"name": name.strip(), "char_offset": char_offset})
    return items


# ---------------------------------------------------------------------
# statement_kind分類
# ---------------------------------------------------------------------

def _normalize_for_match(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _shares_significant_phrase(title_norm: str, quote_norm: str, min_words: int = TITLE_MATCH_MIN_WORDS) -> bool:
    if not title_norm or not quote_norm:
        return False
    quote_words = quote_norm.split()
    if len(quote_words) < min_words:
        return quote_norm in title_norm
    for i in range(len(quote_words) - min_words + 1):
        if " ".join(quote_words[i:i + min_words]) in title_norm:
            return True
    return False


def _classify_statement_kind(signal_title: str, quote: dict, speaker_attribution_status: str) -> str:
    """抽出時のベストエフォート分類。journalist/analyst interpretationは抽出時には判定しない
    （Human Reviewがrejected+rejection_reason='interpretive_fragment'で除外を確定する）。"""
    title_norm = _normalize_for_match(signal_title)
    quote_norm = _normalize_for_match(quote["quote_text"])
    if _shares_significant_phrase(title_norm, quote_norm):
        return "direct_quote_candidate"
    if quote.get("verb"):
        return "direct_quote_candidate"
    if speaker_attribution_status in ("proximate_name", "explicit"):
        return "attributed_statement_candidate"
    return "quotation_fragment"


# ---------------------------------------------------------------------
# Speaker候補計算
# ---------------------------------------------------------------------

def _speaker_type_for_allnames(name: str) -> str:
    lower = (name or "").lower()
    if any(kw in lower for kw in _ORG_KEYWORDS):
        return "organization"
    return "unknown"  # personへは絶対にデフォルトしない


def _compute_speaker_candidates(quote: dict, persons: list, organizations: list, allnames: list) -> list:
    offset = quote["offset"]
    candidates = []
    for p in persons or []:
        co = _safe_int(p.get("char_offset"))
        if co is None:
            continue
        candidates.append({"name": p.get("name"), "speaker_type": "person", "source": "persons",
                            "distance_chars": abs(co - offset), "role_text": p.get("name")})
    for o in organizations or []:
        co = _safe_int(o.get("char_offset"))
        if co is None:
            continue
        candidates.append({"name": o.get("name"), "speaker_type": "organization", "source": "organizations",
                            "distance_chars": abs(co - offset), "role_text": o.get("name")})
    for a in allnames or []:
        co = _safe_int(a.get("char_offset"))
        if co is None:
            continue
        candidates.append({
            "name": a.get("name"), "speaker_type": _speaker_type_for_allnames(a.get("name")),
            "source": "allnames", "distance_chars": abs(co - offset), "role_text": a.get("name"),
        })
    candidates.sort(key=lambda c: c["distance_chars"])
    return candidates


def _resolve_speaker_attribution_status(quote: dict, ranked_candidates: list) -> str:
    if not ranked_candidates:
        return "unknown"
    nearest = ranked_candidates[0]["distance_chars"]
    if nearest > SPEAKER_PROXIMITY_CLOSE_CHARS:
        return "unknown"
    if nearest <= SPEAKER_PROXIMITY_EXPLICIT_CHARS and quote.get("verb"):
        return "explicit"
    if len(ranked_candidates) >= 2:
        gap = ranked_candidates[1]["distance_chars"] - nearest
        if gap < SPEAKER_PROXIMITY_AMBIGUOUS_GAP_CHARS:
            return "ambiguous"
    return "proximate_name"


# ---------------------------------------------------------------------
# Episode Guard解決（Phase2B-2B資産の参照。accepted authoritative、8回目レビュー修正①）
# ---------------------------------------------------------------------

def _build_signal_to_actor_candidates_index(client, *, is_demo: bool) -> dict:
    candidates = _select_all_paginated(client, "geo_actor_episode_candidates", {
        "select": "actor_episode_candidate_id,external_signal_ids",
        "is_demo": _bool_filter(is_demo), "candidate_status": "neq.rejected",
    })
    index = {}
    for c in candidates:
        for sid in c.get("external_signal_ids") or []:
            index.setdefault(sid, []).append(c["actor_episode_candidate_id"])
    return index


def _build_episode_guard_indices(client, *, is_demo: bool) -> dict:
    member_rows = _select_all_paginated(client, "geo_episode_cluster_members", {
        "select": "episode_cluster_id,actor_episode_candidate_id",
    })
    candidate_to_cluster = {r["actor_episode_candidate_id"]: r["episode_cluster_id"] for r in member_rows}

    split_rows = _select_all_paginated(client, "geo_episode_split_candidates", {
        "select": "split_candidate_id,candidate_type,candidate_status,member_candidate_ids",
        "is_demo": _bool_filter(is_demo),
    })
    split_by_member = {}
    for r in split_rows:
        for cid in r.get("member_candidate_ids") or []:
            split_by_member.setdefault(cid, []).append(r)

    return {"candidate_to_cluster": candidate_to_cluster, "split_by_member": split_by_member}


def _resolve_episode_context_for_candidate(actor_episode_candidate_id: str, indices: dict) -> dict:
    cluster_id = indices["candidate_to_cluster"].get(actor_episode_candidate_id)
    if cluster_id is None:
        return {"episode_cluster_id": None, "episode_evidence_status": "normal", "split_candidate_id": None}

    split_rows = indices["split_by_member"].get(actor_episode_candidate_id, [])
    accepted_rows = [r for r in split_rows if r["candidate_status"] == "accepted"]
    unresolved_rows = [r for r in split_rows if r["candidate_status"] in ("candidate", "reviewed")]

    if len(accepted_rows) > 1:
        # data-integrity conflict: silentに1件を選ばない。
        return {
            "episode_cluster_id": cluster_id, "episode_evidence_status": "unresolved_hold",
            "split_candidate_id": None, "conflict": "multiple_accepted_split_candidates",
            "conflicting_split_candidate_ids": sorted(r["split_candidate_id"] for r in accepted_rows),
        }
    if len(accepted_rows) == 1:
        row = accepted_rows[0]
        if row["candidate_type"] == "artifact_group":
            return {"episode_evidence_status": "artifact_excluded"}
        if row["candidate_type"] == "split_group":
            return {"episode_cluster_id": cluster_id, "episode_evidence_status": "split_group",
                     "split_candidate_id": row["split_candidate_id"]}
        return {"episode_cluster_id": cluster_id, "episode_evidence_status": "outlier_pending",
                 "split_candidate_id": row["split_candidate_id"]}

    if unresolved_rows:
        # 未解決のProposal（何件・どのcandidate_typeでも）は一律unresolved_holdに畳み込む。
        # 取得順や件数でsplit_group/artifact_group等を決めない。
        return {"episode_cluster_id": cluster_id, "episode_evidence_status": "unresolved_hold",
                 "split_candidate_id": None}

    return {"episode_cluster_id": cluster_id, "episode_evidence_status": "normal", "split_candidate_id": None}


def _resolve_episode_context(external_signal_id: str, signal_to_candidates_index: dict, guard_indices: dict):
    candidate_ids = signal_to_candidates_index.get(external_signal_id, [])
    if not candidate_ids:
        return {"episode_link_status": "unlinked", "linked_actor_episode_candidate_ids": [],
                "episode_cluster_id": None, "episode_evidence_status": None, "split_candidate_id": None}

    contexts = []
    seen_keys = set()
    for cid in candidate_ids:
        ctx = _resolve_episode_context_for_candidate(cid, guard_indices)
        key = (ctx.get("episode_cluster_id"), ctx.get("episode_evidence_status"), ctx.get("split_candidate_id"))
        if key not in seen_keys:
            seen_keys.add(key)
            contexts.append(ctx)

    linked_ids = sorted(candidate_ids)
    if len(contexts) == 1:
        ctx = contexts[0]
        if ctx.get("episode_evidence_status") == "artifact_excluded":
            return None
        return {
            "episode_link_status": "linked", "linked_actor_episode_candidate_ids": linked_ids,
            "episode_cluster_id": ctx.get("episode_cluster_id"),
            "episode_evidence_status": ctx.get("episode_evidence_status"),
            "split_candidate_id": ctx.get("split_candidate_id"),
        }

    return {
        "episode_link_status": "ambiguous", "linked_actor_episode_candidate_ids": linked_ids,
        "episode_cluster_id": None, "episode_evidence_status": None, "split_candidate_id": None,
        "all_contexts": contexts,
    }


# ---------------------------------------------------------------------
# Fingerprintと上位エントリポイント
# ---------------------------------------------------------------------

def _compute_fingerprint(external_signal_id: str, provider_record_id: str, quote_offset: int,
                          quote_length: int) -> str:
    digest_input = f"{ALGORITHM_VERSION}:{external_signal_id}:{provider_record_id}:{quote_offset}:{quote_length}"
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()


def _build_statement_candidates_for_annotation(annotation: dict, signal: dict, signal_to_candidates_index: dict,
                                                guard_indices: dict) -> list:
    cols = (annotation.get("raw_metadata") or {}).get("columns") or []
    quotes = _parse_gkg_quotations(cols)
    if not quotes:
        return []

    episode_ctx = _resolve_episode_context(annotation["external_signal_id"], signal_to_candidates_index,
                                            guard_indices)
    if episode_ctx is None:
        return []  # acceptedなartifact_group -> 完全除外

    allnames = _parse_gkg_allnames(cols)
    persons = annotation.get("persons") or []
    organizations = annotation.get("organizations") or []
    signal_title = signal.get("title") or ""

    rows = []
    for quote in quotes:
        ranked = _compute_speaker_candidates(quote, persons, organizations, allnames)
        attribution_status = _resolve_speaker_attribution_status(quote, ranked)
        statement_kind = _classify_statement_kind(signal_title, quote, attribution_status)
        fingerprint = _compute_fingerprint(
            annotation["external_signal_id"], annotation["provider_record_id"], quote["offset"], quote["length"])
        rows.append({
            "fingerprint": fingerprint,
            "quote_offset": quote["offset"], "quote_length": quote["length"], "quote_text": quote["quote_text"],
            "statement_kind": statement_kind,
            "speaker_candidates": ranked,
            "speaker_attribution_status": attribution_status,
            "episode_link_status": episode_ctx["episode_link_status"],
            "linked_actor_episode_candidate_ids": episode_ctx.get("linked_actor_episode_candidate_ids") or [],
            "episode_cluster_id": episode_ctx.get("episode_cluster_id"),
            "episode_evidence_status": episode_ctx.get("episode_evidence_status"),
            "split_candidate_id": episode_ctx.get("split_candidate_id"),
            "raw_metadata": {
                "algorithm_version": ALGORITHM_VERSION,
                "gkg_quote_tuple": quote,
                "considered_speaker_candidates": ranked,
                "episode_guard_snapshot": episode_ctx,
            },
        })
    return rows


def _call_apply_replace_rpc(client, params: dict) -> dict:
    try:
        return client.rpc("geo_statement_candidate_apply_replace", params)
    except (StatementCandidateConflict, StatementCandidateValidationError):
        raise
    except requests.exceptions.HTTPError as exc:
        message = ""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                message = (response.json() or {}).get("message", "") or ""
            except ValueError:
                message = ""
        if message.startswith("statement_candidate_conflict"):
            raise StatementCandidateConflict(message) from exc
        if message.startswith("statement_candidate_annotation_not_found"):
            raise StatementCandidateValidationError(message) from exc
        raise


def generate_statement_candidates(client, *, is_demo: bool = False) -> dict:
    """is_demoスコープの全annotationのうちV2.1QUOTATIONSが非空の行を対象に、annotationごとに
    candidate状態のStatement Candidate行を毎回ゼロから再構築する。reviewed/accepted/rejectedの
    行には一切触れない（freeze）。statement_candidate_conflict発生時はannotation単位で
    MAX_REPLACE_RETRIES回まで自動リトライする。"""
    annotations = _select_all_paginated(client, "geo_external_signal_annotations", {
        "select": "*", "is_demo": _bool_filter(is_demo),
    })
    annotations_with_quotes = [
        a for a in annotations
        if len((a.get("raw_metadata") or {}).get("columns") or []) > 22
        and ((a.get("raw_metadata") or {}).get("columns") or [])[22]
    ]

    signal_ids = list({a["external_signal_id"] for a in annotations_with_quotes})
    signals = _select_in_chunked(
        client, "geo_external_signals", select="external_signal_id,title",
        in_column="external_signal_id", in_values=signal_ids) if signal_ids else []
    signal_by_id = {s["external_signal_id"]: s for s in signals}

    signal_to_candidates_index = _build_signal_to_actor_candidates_index(client, is_demo=is_demo)
    guard_indices = _build_episode_guard_indices(client, is_demo=is_demo)

    annotations_processed = 0
    statement_kind_counts = {}
    inserted_total = deleted_total = 0

    for annotation in annotations_with_quotes:
        annotation_id = annotation["annotation_id"]
        for _attempt in range(MAX_REPLACE_RETRIES):
            signal = signal_by_id.get(annotation["external_signal_id"], {})
            new_rows = _build_statement_candidates_for_annotation(
                annotation, signal, signal_to_candidates_index, guard_indices)
            existing = client.select("geo_statement_candidates", {
                "select": "candidate_status,fingerprint", "annotation_id": f"eq.{annotation_id}",
            })
            expected_frozen = sorted(r["fingerprint"] for r in existing if r["candidate_status"] in _FROZEN_STATUSES)
            expected_candidate = sorted(r["fingerprint"] for r in existing if r["candidate_status"] == "candidate")
            try:
                rpc_result = _call_apply_replace_rpc(client, {
                    "p_annotation_id": annotation_id,
                    "p_expected_frozen_fingerprints": expected_frozen,
                    "p_expected_candidate_fingerprints": expected_candidate,
                    "p_new_rows": new_rows,
                })
            except StatementCandidateConflict:
                continue
            annotations_processed += 1
            inserted_total += rpc_result.get("inserted", 0)
            deleted_total += rpc_result.get("deleted", 0)
            for row in new_rows:
                statement_kind_counts[row["statement_kind"]] = statement_kind_counts.get(row["statement_kind"], 0) + 1
            break
        else:
            raise ValueError(
                f"generate_statement_candidates: statement_candidate_conflictが{MAX_REPLACE_RETRIES}回連続で"
                f"発生しました (annotation_id={annotation_id})")

    return {
        "annotations_processed": annotations_processed,
        "inserted": inserted_total,
        "deleted": deleted_total,
        "statement_kind_counts": statement_kind_counts,
    }


def list_statement_candidates(client, *, external_signal_id: str = None, episode_link_status: str = None,
                               statement_kind: str = None, candidate_status: str = None,
                               limit: int = 50, offset: int = 0) -> dict:
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit), "offset": str(offset)}
    if external_signal_id:
        params["external_signal_id"] = f"eq.{external_signal_id}"
    if episode_link_status:
        params["episode_link_status"] = f"eq.{episode_link_status}"
    if statement_kind:
        params["statement_kind"] = f"eq.{statement_kind}"
    if candidate_status:
        params["candidate_status"] = f"eq.{candidate_status}"
    return {"statement_candidates": client.select("geo_statement_candidates", params)}


def _call_update_status_rpc(client, params: dict) -> dict:
    try:
        return client.rpc("geo_statement_candidate_update_status", params)
    except requests.exceptions.HTTPError as exc:
        message = ""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                message = (response.json() or {}).get("message", "") or ""
            except ValueError:
                message = ""
        if message.startswith(_UPDATE_STATUS_KNOWN_ERROR_PREFIXES):
            raise ValueError(message) from exc
        raise


def update_statement_candidate_status(client, *, statement_candidate_id: str, candidate_status: str,
                                       reviewed_by: str, review_notes: str = None, rejection_reason: str = None,
                                       confirmed_statement_kind: str = None, confirmed_statement_text: str = None,
                                       confirmed_speaker_name: str = None, confirmed_speaker_type: str = None) -> dict:
    """geo_statement_candidate_update_status RPCを1回呼ぶ薄いラッパー。RPC側のガードは最終防衛線
    であり、Python側でも同等の事前検証を行い分かりやすいエラーを返す。acceptedへの遷移では
    confirmed_statement_kind/confirmed_statement_text/confirmed_speaker_name/confirmed_speaker_type
    の4項目が（今回の呼び出しか、既存行の値かのどちらかで）揃っている必要がある。
    accepted/rejectedから`candidate`への巻き戻しはサポートしない（_ALLOWED_TRANSITIONSに無い）。"""
    if candidate_status == "rejected" and not rejection_reason:
        raise ValueError("rejection_reason is required when rejecting a statement candidate")

    if candidate_status == "accepted":
        existing_rows = client.select("geo_statement_candidates", {
            "select": "confirmed_statement_kind,confirmed_statement_text,confirmed_speaker_name,"
                      "confirmed_speaker_type",
            "statement_candidate_id": f"eq.{statement_candidate_id}", "limit": "1",
        })
        existing = existing_rows[0] if existing_rows else {}
        effective_kind = confirmed_statement_kind or existing.get("confirmed_statement_kind")
        effective_text = confirmed_statement_text or existing.get("confirmed_statement_text")
        effective_name = confirmed_speaker_name or existing.get("confirmed_speaker_name")
        effective_type = confirmed_speaker_type or existing.get("confirmed_speaker_type")
        if effective_kind not in ("direct_quote", "attributed_statement"):
            raise ValueError(
                "confirmed_statement_kind must be 'direct_quote' or 'attributed_statement' to accept")
        if not effective_text:
            raise ValueError("confirmed_statement_text is required to accept a statement candidate")
        if not effective_name:
            raise ValueError("confirmed_speaker_name is required to accept a statement candidate")
        if effective_type not in ("person", "organization"):
            raise ValueError("confirmed_speaker_type must be 'person' or 'organization' to accept")

    _call_update_status_rpc(client, {
        "p_statement_candidate_id": statement_candidate_id, "p_new_status": candidate_status,
        "p_reviewed_by": reviewed_by, "p_review_notes": review_notes, "p_rejection_reason": rejection_reason,
        "p_confirmed_statement_kind": confirmed_statement_kind,
        "p_confirmed_statement_text": confirmed_statement_text,
        "p_confirmed_speaker_name": confirmed_speaker_name, "p_confirmed_speaker_type": confirmed_speaker_type,
    })
    rows = client.select("geo_statement_candidates", {
        "select": "*", "statement_candidate_id": f"eq.{statement_candidate_id}", "limit": "1",
    })
    return rows[0] if rows else None
