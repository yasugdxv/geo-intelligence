"""地政学ナレッジ＆インテリジェンスDB Phase2B-3A-1: geo_statement_candidate_service.pyのテスト。

GKG V2.1QUOTATIONS/V2.1ALLNAMESのパース、statement_kind分類、speaker候補計算（ALLNAMES由来は
"person"へデフォルトしない）、Episode Guard（accepted authoritative、rejected除外、
unresolved_hold）、annotation単位fingerprint idempotency、Human Review（accepted 4項目必須化・
rejection_reason必須化・巻き戻し不可）を検証する。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_statement_candidate_service as svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


class _StatementFakeClient(FakeSupabaseClient):
    """geo_statement_candidate_apply_replace / geo_statement_candidate_update_status RPCの
    実際のPL/pgSQL挙動（annotation_id単位snapshot比較・derived metadata更新・sibling無しの
    単一行Human Review・accepted 4項目必須化）をPythonで再現するFake。"""

    def rpc(self, fn_name, params=None):
        if fn_name == "geo_statement_candidate_apply_replace":
            return self._apply_replace(params or {})
        if fn_name == "geo_statement_candidate_update_status":
            return self._update_status(params or {})
        return super().rpc(fn_name, params)

    def _apply_replace(self, p):
        annotation_id = p["p_annotation_id"]
        annotations = self.tables.get("geo_external_signal_annotations", [])
        annotation = next((a for a in annotations if a["annotation_id"] == annotation_id), None)
        if annotation is None:
            raise svc.StatementCandidateValidationError(
                f"statement_candidate_annotation_not_found: {annotation_id}")

        rows = self.tables.get("geo_statement_candidates", [])
        current_frozen = sorted(
            r["fingerprint"] for r in rows
            if r["annotation_id"] == annotation_id and r["candidate_status"] in ("reviewed", "accepted", "rejected"))
        current_candidate = sorted(
            r["fingerprint"] for r in rows
            if r["annotation_id"] == annotation_id and r["candidate_status"] == "candidate")

        def canon(lst):
            return sorted(lst or [])

        if (canon(p.get("p_expected_frozen_fingerprints")) != current_frozen
                or canon(p.get("p_expected_candidate_fingerprints")) != current_candidate):
            raise svc.StatementCandidateConflict("statement_candidate_conflict: snapshot changed since read, retry")

        new_rows = p.get("p_new_rows") or []
        new_fingerprints = {r["fingerprint"] for r in new_rows}

        other_rows = [r for r in rows if r["annotation_id"] != annotation_id]
        kept_this_annotation = [
            r for r in rows if r["annotation_id"] == annotation_id
            and (r["candidate_status"] in ("reviewed", "accepted", "rejected") or r["fingerprint"] in new_fingerprints)
        ]
        deleted = sum(
            1 for r in rows if r["annotation_id"] == annotation_id and r["candidate_status"] == "candidate"
            and r["fingerprint"] not in new_fingerprints)
        self.tables["geo_statement_candidates"] = other_rows + kept_this_annotation

        current_by_fp = {r["fingerprint"]: r for r in kept_this_annotation}
        inserted = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in new_rows:
            fp = row["fingerprint"]
            if fp in current_frozen:
                continue
            if fp in current_candidate and fp in current_by_fp:
                current_by_fp[fp].update({
                    "statement_kind": row["statement_kind"],
                    "speaker_candidates": row.get("speaker_candidates") or [],
                    "speaker_attribution_status": row["speaker_attribution_status"],
                    "episode_link_status": row["episode_link_status"],
                    "linked_actor_episode_candidate_ids": row.get("linked_actor_episode_candidate_ids") or [],
                    "episode_cluster_id": row.get("episode_cluster_id"),
                    "episode_evidence_status": row.get("episode_evidence_status"),
                    "split_candidate_id": row.get("split_candidate_id"),
                    "raw_metadata": row.get("raw_metadata"),
                })
                continue
            new_row = {
                "statement_candidate_id": self._next_id("statement_candidate_id"),
                "external_signal_id": annotation["external_signal_id"], "annotation_id": annotation_id,
                "quote_offset": row["quote_offset"], "quote_length": row["quote_length"],
                "quote_text": row["quote_text"], "fingerprint": fp, "statement_kind": row["statement_kind"],
                "speaker_candidates": row.get("speaker_candidates") or [],
                "speaker_attribution_status": row["speaker_attribution_status"],
                "episode_link_status": row["episode_link_status"],
                "linked_actor_episode_candidate_ids": row.get("linked_actor_episode_candidate_ids") or [],
                "episode_cluster_id": row.get("episode_cluster_id"),
                "episode_evidence_status": row.get("episode_evidence_status"),
                "split_candidate_id": row.get("split_candidate_id"),
                "candidate_status": "candidate", "rejection_reason": None,
                "confirmed_statement_kind": None, "confirmed_statement_text": None,
                "confirmed_speaker_name": None, "confirmed_speaker_type": None,
                "reviewed_at": None, "reviewed_by": None, "review_notes": None,
                "raw_metadata": row.get("raw_metadata"), "is_demo": annotation.get("is_demo", False),
                "created_at": now_iso, "updated_at": now_iso,
            }
            self.tables.setdefault("geo_statement_candidates", []).append(new_row)
            current_by_fp[fp] = new_row
            inserted += 1

        return {"inserted": inserted, "deleted": deleted}

    def _update_status(self, p):
        statement_candidate_id = p["p_statement_candidate_id"]
        new_status = p["p_new_status"]
        rows = self.tables.get("geo_statement_candidates", [])
        row = next((r for r in rows if r["statement_candidate_id"] == statement_candidate_id), None)
        if row is None:
            raise ValueError(f"statement_candidate_not_found: {statement_candidate_id}")

        allowed = svc._ALLOWED_TRANSITIONS.get(row["candidate_status"], set())
        if new_status not in allowed:
            raise ValueError(f"statement_candidate_invalid_transition: {row['candidate_status']} -> {new_status}")

        if new_status == "rejected" and not p.get("p_rejection_reason"):
            raise ValueError(f"statement_candidate_rejection_reason_required: {statement_candidate_id}")

        if new_status == "accepted":
            kind = p.get("p_confirmed_statement_kind") or row.get("confirmed_statement_kind")
            text = p.get("p_confirmed_statement_text") or row.get("confirmed_statement_text")
            name = p.get("p_confirmed_speaker_name") or row.get("confirmed_speaker_name")
            stype = p.get("p_confirmed_speaker_type") or row.get("confirmed_speaker_type")
            if (kind not in ("direct_quote", "attributed_statement") or not text or not name
                    or stype not in ("person", "organization")):
                raise ValueError(f"statement_candidate_accepted_requires_confirmation: {statement_candidate_id}")

        now_iso = datetime.now(timezone.utc).isoformat()
        row["candidate_status"] = new_status
        row["reviewed_by"] = p.get("p_reviewed_by")
        row["review_notes"] = p.get("p_review_notes")
        row["reviewed_at"] = now_iso
        if new_status == "rejected":
            row["rejection_reason"] = p.get("p_rejection_reason")
        for key in ("confirmed_statement_kind", "confirmed_statement_text", "confirmed_speaker_name",
                    "confirmed_speaker_type"):
            pkey = "p_" + key
            if p.get(pkey):
                row[key] = p[pkey]
        return {"statement_candidate_id": statement_candidate_id, "candidate_status": new_status}


def _gkg_columns(quotations="", allnames=""):
    cols = [""] * 27
    cols[22] = quotations
    cols[23] = allnames
    return cols


def _annotation(annotation_id, external_signal_id, provider_record_id, columns,
                 persons=None, organizations=None, is_demo=False):
    return {
        "annotation_id": annotation_id, "external_signal_id": external_signal_id,
        "provider_record_id": provider_record_id, "persons": persons or [], "organizations": organizations or [],
        "raw_metadata": {"columns": columns}, "is_demo": is_demo,
    }


def _signal(external_signal_id, title):
    return {"external_signal_id": external_signal_id, "title": title}


def _actor_candidate(cid, external_signal_ids, is_demo=False, candidate_status="candidate"):
    return {"actor_episode_candidate_id": cid, "external_signal_ids": list(external_signal_ids),
            "candidate_status": candidate_status, "is_demo": is_demo}


def _cluster_member(cluster_id, candidate_id):
    return {"episode_cluster_id": cluster_id, "actor_episode_candidate_id": candidate_id}


def _split_candidate(split_id, candidate_type, candidate_status, member_ids, is_demo=False):
    return {"split_candidate_id": split_id, "candidate_type": candidate_type, "candidate_status": candidate_status,
            "member_candidate_ids": list(member_ids), "is_demo": is_demo}


# ---------------------------------------------------------------------
# GKG生列パース
# ---------------------------------------------------------------------

def test_parse_gkg_quotations_real_data_shape():
    cols = _gkg_columns(quotations="282|34||faster , heavier and more painful#581|30||rules of the game have changed")
    quotes = svc._parse_gkg_quotations(cols)
    assert len(quotes) == 2
    assert quotes[0] == {"offset": 282, "length": 34, "verb": "", "quote_text": "faster , heavier and more painful"}
    assert quotes[1]["quote_text"] == "rules of the game have changed"


def test_parse_gkg_quotations_filters_short_snippets():
    cols = _gkg_columns(quotations="10|3||hi#282|34||faster , heavier and more painful")
    quotes = svc._parse_gkg_quotations(cols)
    assert len(quotes) == 1
    assert quotes[0]["quote_text"] == "faster , heavier and more painful"


def test_parse_gkg_allnames_real_data_shape():
    cols = _gkg_columns(allnames="Foreign Minister Bruno Rodriguez,1436;United States,1282")
    names = svc._parse_gkg_allnames(cols)
    assert names == [{"name": "Foreign Minister Bruno Rodriguez", "char_offset": 1436},
                      {"name": "United States", "char_offset": 1282}]


# ---------------------------------------------------------------------
# statement_kind分類
# ---------------------------------------------------------------------

def test_classify_statement_kind_direct_quote_from_title_match():
    title = "Iran warns of  more painful  response , plans new  restricted  maritime zone"
    quote = {"quote_text": "faster , heavier and more painful", "verb": ""}
    assert svc._classify_statement_kind(title, quote, "unknown") == "direct_quote_candidate"


def test_classify_statement_kind_attributed_from_proximate_speaker():
    title = "How US financial sanctions are intensifying pressure on Cuba"
    quote = {"quote_text": "low-intensity confrontation", "verb": ""}
    assert svc._classify_statement_kind(title, quote, "proximate_name") == "attributed_statement_candidate"


def test_classify_statement_kind_defaults_to_quotation_fragment():
    title = "Some unrelated headline"
    quote = {"quote_text": "Iran efforts to acquire weapons of mass destruction", "verb": ""}
    assert svc._classify_statement_kind(title, quote, "unknown") == "quotation_fragment"


# ---------------------------------------------------------------------
# Speaker候補計算
# ---------------------------------------------------------------------

def test_speaker_candidates_proximate_name_bruno_rodriguez():
    quote = {"offset": 1238, "length": 27, "verb": "", "quote_text": "low-intensity confrontation"}
    allnames = [{"name": "Foreign Minister Bruno Rodriguez", "char_offset": 1436},
                {"name": "United States", "char_offset": 1282}]
    ranked = svc._compute_speaker_candidates(quote, [], [], allnames)
    status = svc._resolve_speaker_attribution_status(quote, ranked)
    assert status == "proximate_name"
    assert ranked[0]["name"] == "United States"  # 実際に一番近いのはUnited States(distance=44)


def test_speaker_candidates_unknown_when_far():
    quote = {"offset": 1255, "length": 25, "verb": "", "quote_text": "not been there to help me"}
    allnames = [{"name": "Andy Burnham", "char_offset": 176}]
    ranked = svc._compute_speaker_candidates(quote, [], [], allnames)
    status = svc._resolve_speaker_attribution_status(quote, ranked)
    assert status == "unknown"


def test_speaker_candidates_ambiguous_when_two_candidates_tied():
    quote = {"offset": 1000, "length": 10, "verb": "", "quote_text": "some quoted phrase here"}
    persons = [{"name": "Person A", "char_offset": "1010"}, {"name": "Person B", "char_offset": "1020"}]
    ranked = svc._compute_speaker_candidates(quote, persons, [], [])
    status = svc._resolve_speaker_attribution_status(quote, ranked)
    assert status == "ambiguous"


def test_allnames_speaker_type_defaults_to_unknown_not_person():
    quote = {"offset": 2037, "length": 10, "verb": "", "quote_text": "the disruption of international trade"}
    allnames = [{"name": "Foreign Ministry", "char_offset": 2142},
                {"name": "Argentine President Javier Milei", "char_offset": 4385}]
    ranked = svc._compute_speaker_candidates(quote, [], [], allnames)
    by_name = {c["name"]: c for c in ranked}
    assert by_name["Foreign Ministry"]["speaker_type"] == "organization"
    # 肩書("President")があっても組織キーワードが無い限りunknownのまま、personへデフォルトしない
    assert by_name["Argentine President Javier Milei"]["speaker_type"] == "unknown"


def test_persons_and_organizations_sources_keep_gdelt_ner_typing():
    quote = {"offset": 100, "length": 10, "verb": "", "quote_text": "quoted phrase"}
    persons = [{"name": "Mohsen Rezaei", "char_offset": "110"}]
    organizations = [{"name": "Iran Supreme National Security Council", "char_offset": "120"}]
    ranked = svc._compute_speaker_candidates(quote, persons, organizations, [])
    by_name = {c["name"]: c for c in ranked}
    assert by_name["Mohsen Rezaei"]["speaker_type"] == "person"
    assert by_name["Iran Supreme National Security Council"]["speaker_type"] == "organization"


# ---------------------------------------------------------------------
# Episode Guard解決（accepted authoritative, rejected除外, unresolved_hold）
# ---------------------------------------------------------------------

def test_resolve_episode_context_for_candidate_normal_when_no_split_candidates():
    indices = {"candidate_to_cluster": {"c1": "cluster1"}, "split_by_member": {}}
    ctx = svc._resolve_episode_context_for_candidate("c1", indices)
    assert ctx["episode_evidence_status"] == "normal"


def test_resolve_episode_context_for_candidate_accepted_split_group():
    indices = {"candidate_to_cluster": {"c1": "cluster1"},
               "split_by_member": {"c1": [_split_candidate("s1", "split_group", "accepted", ["c1", "c2"])]}}
    ctx = svc._resolve_episode_context_for_candidate("c1", indices)
    assert ctx["episode_evidence_status"] == "split_group"
    assert ctx["split_candidate_id"] == "s1"


def test_resolve_episode_context_for_candidate_accepted_artifact_group_excludes():
    indices = {"candidate_to_cluster": {"c1": "cluster1"},
               "split_by_member": {"c1": [_split_candidate("s1", "artifact_group", "accepted", ["c1"])]}}
    ctx = svc._resolve_episode_context_for_candidate("c1", indices)
    assert ctx["episode_evidence_status"] == "artifact_excluded"


def test_resolve_episode_context_for_candidate_accepted_outlier():
    indices = {"candidate_to_cluster": {"c1": "cluster1"},
               "split_by_member": {"c1": [_split_candidate("s1", "outlier", "accepted", ["c1"])]}}
    ctx = svc._resolve_episode_context_for_candidate("c1", indices)
    assert ctx["episode_evidence_status"] == "outlier_pending"


def test_resolve_episode_context_for_candidate_rejected_split_candidate_falls_back_to_normal():
    indices = {"candidate_to_cluster": {"c1": "cluster1"},
               "split_by_member": {"c1": [_split_candidate("s1", "artifact_group", "rejected", ["c1"])]}}
    ctx = svc._resolve_episode_context_for_candidate("c1", indices)
    assert ctx["episode_evidence_status"] == "normal"


def test_resolve_episode_context_for_candidate_unresolved_proposal_any_type_becomes_hold():
    for candidate_type in ("split_group", "artifact_group", "outlier"):
        for status in ("candidate", "reviewed"):
            indices = {"candidate_to_cluster": {"c1": "cluster1"},
                       "split_by_member": {"c1": [_split_candidate("s1", candidate_type, status, ["c1"])]}}
            ctx = svc._resolve_episode_context_for_candidate("c1", indices)
            assert ctx["episode_evidence_status"] == "unresolved_hold", (candidate_type, status)


def test_resolve_episode_context_for_candidate_accepted_takes_priority_over_unresolved():
    indices = {"candidate_to_cluster": {"c1": "cluster1"},
               "split_by_member": {"c1": [
                   _split_candidate("s1", "split_group", "accepted", ["c1"]),
                   _split_candidate("s2", "artifact_group", "candidate", ["c1"]),
               ]}}
    ctx = svc._resolve_episode_context_for_candidate("c1", indices)
    assert ctx["episode_evidence_status"] == "split_group"
    assert ctx["split_candidate_id"] == "s1"


def test_resolve_episode_context_for_candidate_multiple_accepted_is_conflict_not_silently_picked():
    indices = {"candidate_to_cluster": {"c1": "cluster1"},
               "split_by_member": {"c1": [
                   _split_candidate("s1", "split_group", "accepted", ["c1"]),
                   _split_candidate("s2", "outlier", "accepted", ["c1"]),
               ]}}
    ctx = svc._resolve_episode_context_for_candidate("c1", indices)
    assert ctx["episode_evidence_status"] == "unresolved_hold"
    assert ctx["conflict"] == "multiple_accepted_split_candidates"
    assert sorted(ctx["conflicting_split_candidate_ids"]) == ["s1", "s2"]


def test_resolve_episode_context_unlinked_when_no_actor_candidates():
    ctx = svc._resolve_episode_context("sig1", {}, {"candidate_to_cluster": {}, "split_by_member": {}})
    assert ctx["episode_link_status"] == "unlinked"


def test_resolve_episode_context_linked_when_multiple_candidates_same_cluster():
    signal_index = {"sig1": ["c1", "c2"]}
    guard_indices = {"candidate_to_cluster": {"c1": "cluster1", "c2": "cluster1"}, "split_by_member": {}}
    ctx = svc._resolve_episode_context("sig1", signal_index, guard_indices)
    assert ctx["episode_link_status"] == "linked"
    assert ctx["episode_cluster_id"] == "cluster1"
    assert ctx["episode_evidence_status"] == "normal"


def test_resolve_episode_context_ambiguous_when_distinct_clusters():
    signal_index = {"sig1": ["c1", "c2"]}
    guard_indices = {"candidate_to_cluster": {"c1": "cluster1", "c2": "cluster2"}, "split_by_member": {}}
    ctx = svc._resolve_episode_context("sig1", signal_index, guard_indices)
    assert ctx["episode_link_status"] == "ambiguous"


def test_resolve_episode_context_returns_none_when_only_context_is_artifact_excluded():
    signal_index = {"sig1": ["c1"]}
    guard_indices = {"candidate_to_cluster": {"c1": "cluster1"},
                      "split_by_member": {"c1": [_split_candidate("s1", "artifact_group", "accepted", ["c1"])]}}
    assert svc._resolve_episode_context("sig1", signal_index, guard_indices) is None


# ---------------------------------------------------------------------
# 実データ形状の再現（root08 Hormuz split_group相当のannotation経由生成）＋冪等性
# ---------------------------------------------------------------------

def _base_tables():
    return {
        "geo_external_signal_annotations": [
            _annotation("ann1", "sig1", "rec1",
                        _gkg_columns(
                            quotations="282|34||faster , heavier and more painful",
                            allnames="Iranian Parliament Speaker Mohammad Baqer Qalibaf,494"),
                        persons=[{"name": "Mohsen Rezaei", "char_offset": "1154"}]),
        ],
        "geo_external_signals": [_signal("sig1", "Iran warns of  more painful  response")],
        "geo_actor_episode_candidates": [_actor_candidate("cand1", ["sig1"])],
        "geo_episode_cluster_members": [_cluster_member("cluster1", "cand1")],
        "geo_episode_split_candidates": [],
        "geo_statement_candidates": [],
    }


def test_generate_statement_candidates_produces_direct_quote_linked_normal():
    client = _StatementFakeClient(tables=_base_tables())
    result = svc.generate_statement_candidates(client, is_demo=False)
    assert result["annotations_processed"] == 1
    assert result["inserted"] == 1
    rows = client.tables["geo_statement_candidates"]
    assert len(rows) == 1
    row = rows[0]
    assert row["statement_kind"] == "direct_quote_candidate"
    assert row["episode_link_status"] == "linked"
    assert row["episode_evidence_status"] == "normal"
    assert row["external_signal_id"] == "sig1"


def test_generate_statement_candidates_is_idempotent_by_fingerprint():
    client = _StatementFakeClient(tables=_base_tables())
    svc.generate_statement_candidates(client, is_demo=False)
    id_before = client.tables["geo_statement_candidates"][0]["statement_candidate_id"]
    svc.generate_statement_candidates(client, is_demo=False)
    assert len(client.tables["geo_statement_candidates"]) == 1
    assert client.tables["geo_statement_candidates"][0]["statement_candidate_id"] == id_before


def test_generate_statement_candidates_does_not_touch_other_annotations():
    tables = _base_tables()
    tables["geo_external_signal_annotations"].append(
        _annotation("ann2", "sig2", "rec2", _gkg_columns(quotations="10|30||some other quoted fragment here")))
    tables["geo_external_signals"].append(_signal("sig2", "Some other headline"))
    tables["geo_actor_episode_candidates"].append(_actor_candidate("cand2", ["sig2"]))
    tables["geo_episode_cluster_members"].append(_cluster_member("cluster2", "cand2"))
    client = _StatementFakeClient(tables=tables)
    svc.generate_statement_candidates(client, is_demo=False)
    assert len(client.tables["geo_statement_candidates"]) == 2
    ann2_id = next(r["statement_candidate_id"] for r in client.tables["geo_statement_candidates"]
                   if r["annotation_id"] == "ann2")

    # ann1のannotationだけ内容が変わる状況（新しいquotationが加わる）を作り、再生成してもann2側は無傷
    client.tables["geo_external_signal_annotations"][0]["raw_metadata"]["columns"] = _gkg_columns(
        quotations="282|34||faster , heavier and more painful#900|30||an entirely new quoted phrase here")
    svc.generate_statement_candidates(client, is_demo=False)
    rows = client.tables["geo_statement_candidates"]
    ann2_rows = [r for r in rows if r["annotation_id"] == "ann2"]
    assert len(ann2_rows) == 1
    assert ann2_rows[0]["statement_candidate_id"] == ann2_id
    ann1_rows = [r for r in rows if r["annotation_id"] == "ann1"]
    assert len(ann1_rows) == 2


def test_generate_statement_candidates_min_quote_length_filters_noise():
    tables = _base_tables()
    tables["geo_external_signal_annotations"][0]["raw_metadata"]["columns"] = _gkg_columns(quotations="10|3||hi")
    client = _StatementFakeClient(tables=tables)
    result = svc.generate_statement_candidates(client, is_demo=False)
    assert result["inserted"] == 0
    assert client.tables["geo_statement_candidates"] == []


def test_generate_statement_candidates_preserves_frozen_rows():
    client = _StatementFakeClient(tables=_base_tables())
    svc.generate_statement_candidates(client, is_demo=False)
    row = client.tables["geo_statement_candidates"][0]
    svc.update_statement_candidate_status(
        client, statement_candidate_id=row["statement_candidate_id"], candidate_status="reviewed",
        reviewed_by="analyst1")

    svc.generate_statement_candidates(client, is_demo=False)
    row_after = client.tables["geo_statement_candidates"][0]
    assert row_after["statement_candidate_id"] == row["statement_candidate_id"]
    assert row_after["candidate_status"] == "reviewed"


def test_apply_replace_sets_external_signal_id_and_is_demo_from_annotation():
    client = _StatementFakeClient(tables=_base_tables())
    client.tables["geo_external_signal_annotations"][0]["is_demo"] = True
    svc.generate_statement_candidates(client, is_demo=True)
    row = client.tables["geo_statement_candidates"][0]
    assert row["external_signal_id"] == "sig1"
    assert row["is_demo"] is True


# ---------------------------------------------------------------------
# Human Review状態機械（accepted 4項目必須化、rejection_reason必須化、巻き戻し不可）
# ---------------------------------------------------------------------

def test_accepted_requires_all_four_confirmed_fields():
    client = _StatementFakeClient(tables=_base_tables())
    svc.generate_statement_candidates(client, is_demo=False)
    row = client.tables["geo_statement_candidates"][0]
    scid = row["statement_candidate_id"]

    try:
        svc.update_statement_candidate_status(client, statement_candidate_id=scid, candidate_status="accepted",
                                               reviewed_by="analyst1")
        assert False, "ValueErrorが送出されるはず"
    except ValueError:
        pass

    # candidate側の語彙(direct_quote_candidate)を渡しても拒否される
    try:
        svc.update_statement_candidate_status(
            client, statement_candidate_id=scid, candidate_status="accepted", reviewed_by="analyst1",
            confirmed_statement_kind="direct_quote_candidate", confirmed_statement_text="text",
            confirmed_speaker_name="X", confirmed_speaker_type="person")
        assert False, "ValueErrorが送出されるはず"
    except ValueError:
        pass

    result = svc.update_statement_candidate_status(
        client, statement_candidate_id=scid, candidate_status="accepted", reviewed_by="analyst1",
        confirmed_statement_kind="direct_quote", confirmed_statement_text="The response will be faster and heavier",
        confirmed_speaker_name="Mohammad Baqer Qalibaf", confirmed_speaker_type="person")
    assert result["candidate_status"] == "accepted"


def test_accepted_rejected_when_only_confirmed_statement_text_missing():
    client = _StatementFakeClient(tables=_base_tables())
    svc.generate_statement_candidates(client, is_demo=False)
    scid = client.tables["geo_statement_candidates"][0]["statement_candidate_id"]
    try:
        svc.update_statement_candidate_status(
            client, statement_candidate_id=scid, candidate_status="accepted", reviewed_by="analyst1",
            confirmed_statement_kind="direct_quote", confirmed_speaker_name="X", confirmed_speaker_type="person")
        assert False, "confirmed_statement_text未設定でValueErrorが送出されるはず"
    except ValueError:
        pass


def test_rejection_reason_required():
    client = _StatementFakeClient(tables=_base_tables())
    svc.generate_statement_candidates(client, is_demo=False)
    scid = client.tables["geo_statement_candidates"][0]["statement_candidate_id"]
    try:
        svc.update_statement_candidate_status(client, statement_candidate_id=scid, candidate_status="rejected",
                                               reviewed_by="analyst1")
        assert False, "ValueErrorが送出されるはず"
    except ValueError:
        pass
    result = svc.update_statement_candidate_status(
        client, statement_candidate_id=scid, candidate_status="rejected", reviewed_by="analyst1",
        rejection_reason="interpretive_fragment")
    assert result["candidate_status"] == "rejected"


def test_no_rollback_transition_from_accepted_or_rejected():
    assert "candidate" not in svc._ALLOWED_TRANSITIONS.get("accepted", set())
    assert "candidate" not in svc._ALLOWED_TRANSITIONS.get("rejected", set())
    assert svc._ALLOWED_TRANSITIONS.get("accepted", set()) == set()
    assert svc._ALLOWED_TRANSITIONS.get("rejected", set()) == set()


def test_update_statement_candidate_status_does_not_touch_other_tables():
    client = _StatementFakeClient(tables=_base_tables())
    svc.generate_statement_candidates(client, is_demo=False)
    scid = client.tables["geo_statement_candidates"][0]["statement_candidate_id"]
    svc.update_statement_candidate_status(
        client, statement_candidate_id=scid, candidate_status="accepted", reviewed_by="analyst1",
        confirmed_statement_kind="direct_quote", confirmed_statement_text="text",
        confirmed_speaker_name="X", confirmed_speaker_type="person")
    assert not any(
        t in ("geo_episode_clusters", "geo_actor_episode_candidates", "geo_entities", "geo_knowledge_items")
        for t, _, _ in client.updated)
