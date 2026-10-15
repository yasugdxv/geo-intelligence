"""
Geo系テスト用共通フェイク。
Supabase(PostgREST)への実アクセスをせず、geo_db_client.SupabaseClient と
同じインターフェース（select/insert/update）を持つ簡易フェイクを提供する。
（ファイル名を conftest.py にしていないのは、pytestのfixture自動読込の仕組みと
  混同しないようにするため。使う側が明示的にimportする単なるヘルパーモジュール）
"""
import threading
from datetime import datetime, timezone


class FakeSupabaseClient:
    """PostgRESTクエリ記法(eq./in.())の最小限だけを解釈するフェイクSupabaseClient"""

    def __init__(self, tables: dict = None, max_rows: int = None):
        self.tables = {k: [dict(row) for row in v] for k, v in (tables or {}).items()}
        # PostgRESTのdb-max-rows（Supabase既定1000件）の再現用。指定すると、limitをそれより大きく
        # 指定しても1回のselectで返る件数がこの値で暗黙に頭打ちになる（2026-10-03、record map
        # 1000行切れの再発防止テスト用）。既定None=無制限で、既存テストの挙動は変わらない。
        self.max_rows = max_rows
        self.inserted = {}
        self.updated = []
        self._id_counter = 0
        # geo_report_prompt1/2_orchestration_serviceがバッチ内アイテムをThreadPoolExecutorで
        # 並列処理するようになった(2026-09-11)ため、テストでもinsert/updateへ複数スレッドから
        # 同時アクセスが起きる。素のdict/listは複合操作(カウンタ+=1等)がアトミックでないため保護する。
        self._lock = threading.Lock()

    def _next_id(self, prefix: str) -> str:
        self._id_counter += 1
        return f"{prefix}-{self._id_counter}"

    @staticmethod
    def _stringify(value) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _eval_or_condition(row: dict, condition: str) -> bool:
        """PostgRESTの'or=(cond1,cond2,...)'記法内の単一条件'column.op.value'を評価する
        最小限の実装（eq/neqのみ対応、geo_report_service.delete_reportのon delete
        restrictチェック等、単純な等価比較のor条件でしか今のところ使わないため）。"""
        column, op, val = condition.split(".", 2)
        actual = FakeSupabaseClient._stringify(row.get(column))
        if op == "eq":
            return actual == val
        if op == "neq":
            return actual != val
        if op == "ilike":
            # geo_external_signal_retrieval_service.search_external_signalsのkeyword
            # fallback（'or=(title.ilike.*x*,summary.ilike.*x*)'）が使う最小限の対応
            pattern = val.strip("*").lower()
            return pattern in actual.lower()
        return False

    def _matches(self, row: dict, params: dict) -> bool:
        for key, value in params.items():
            if key in ("select", "order", "limit", "offset") or not isinstance(value, str):
                continue
            if key == "or":
                conditions = [c for c in value.strip("()").split(",") if c]
                if not any(self._eval_or_condition(row, c) for c in conditions):
                    return False
                continue
            if key == "and":
                # 'and=(id.gte.X,id.lt.Y)'形式（範囲指定）。gte/gt/lte/lt/eqのみ対応。
                for condition in [c for c in value.strip("()").split(",") if c]:
                    column, op, val = condition.split(".", 2)
                    actual = self._stringify(row.get(column))
                    ok = {"gte": actual >= val, "gt": actual > val, "lte": actual <= val, "lt": actual < val,
                          "eq": actual == val}.get(op, True)
                    if not ok:
                        return False
                continue
            if value.startswith("eq."):
                if self._stringify(row.get(key)) != value[3:]:
                    return False
            elif value.startswith("neq."):
                if self._stringify(row.get(key)) == value[4:]:
                    return False
            elif value.startswith("not.in.("):
                items = value[len("not.in.("):-1].split(",")
                if self._stringify(row.get(key)) in items:
                    return False
            elif value.startswith("in.("):
                items = value[len("in.("):-1].split(",")
                if self._stringify(row.get(key)) not in items:
                    return False
            elif value.startswith("gte."):
                if self._stringify(row.get(key)) < value[4:]:
                    return False
            elif value.startswith("gt."):
                if self._stringify(row.get(key)) <= value[3:]:
                    return False
            elif value.startswith("lte."):
                if self._stringify(row.get(key)) > value[4:]:
                    return False
            elif value.startswith("ilike."):
                # PostgRESTのilike記法（*をワイルドカードとして使う、大文字小文字を区別しない）。
                # geo_search_service._partial_match_candidatesが使う"ilike.*text*"形式のみ対応する
                # （前後の*を外して部分一致とみなす簡易実装で十分、複雑なワイルドカードは扱わない）。
                pattern = value[len("ilike."):].strip("*").lower()
                if pattern not in self._stringify(row.get(key)).lower():
                    return False
            elif value == "not.is.null":
                if row.get(key) is None:
                    return False
            elif value == "is.null":
                if row.get(key) is not None:
                    return False
        return True

    def select(self, table: str, params: dict) -> list:
        with self._lock:
            rows = [r for r in self.tables.get(table, []) if self._matches(r, params)]
        order = params.get("order", "")
        if order:
            # "created_at.desc"のような1軸のorderのみ対応（既存テストが依存していたcreated_at
            # 専用の分岐を、任意フィールドのasc/descへ一般化したもの。フィールド欠損行が混在
            # すると型比較でエラーになりうるため、fakeへ渡すseedデータは対象フィールドを揃えること）
            field = order.split(".")[0]
            rows = sorted(rows, key=lambda r: r.get(field, ""), reverse="desc" in order)
        offset = int(params.get("offset") or 0)
        if offset:
            rows = rows[offset:]
        limit = params.get("limit")
        if limit:
            rows = rows[: int(limit)]
        if self.max_rows:
            rows = rows[: self.max_rows]
        return rows

    def insert(self, table: str, rows: list, prefer: str = "return=representation") -> list:
        id_field = {"expert_runs": "run_id", "expert_contents": "content_id",
                    "weekly_email_reports": "report_id",
                    "competitor_target_records": "record_id",
                    "competitor_change_events": "change_event_id",
                    "competitor_initiatives": "initiative_id",
                    "competitor_alerts": "alert_id",
                    "competitor_audit_log": "audit_id",
                    "competitor_daily_alert_digests": "digest_id",
                    "monthly_reports": "report_id",
                    "monthly_report_companies": "monthly_report_company_id",
                    "monthly_report_items": "monthly_report_item_id",
                    "geo_themes": "theme_id",
                    "geo_source_registry": "source_id",
                    "geo_documents": "document_id",
                    "geo_events": "event_id",
                    "geo_evidence": "evidence_id",
                    "geo_indicator_definitions": "indicator_id",
                    "geo_indicator_rule_versions": "rule_version_id",
                    "geo_observations": "observation_id",
                    "geo_audit_logs": "audit_id",
                    "geo_expert_requests": "request_id",
                    "geo_source_packs": "source_pack_id",
                    "geo_expert_responses": "response_id",
                    "geo_quality_evaluations": "evaluation_id",
                    "geo_expert_feedback": "feedback_id",
                    # 地政学ナレッジ＆インテリジェンスDB Phase1（geo_knowledge_*系）
                    "geo_entities": "entity_id",
                    "geo_entity_aliases": "alias_id",
                    "geo_issues": "issue_id",
                    "geo_relationships": "relationship_id",
                    "geo_assets": "asset_id",
                    "geo_dependencies": "dependency_id",
                    "geo_knowledge_items": "knowledge_item_id",
                    "geo_knowledge_reviews": "review_id",
                    "geo_cases": "case_id",
                    "geo_case_hypotheses": "hypothesis_id",
                    "geo_claims": "claim_id",
                    "geo_document_chunks": "chunk_id",
                    "geo_claim_evidence": "claim_evidence_id",
                    "geo_knowledge_gaps": "gap_id",
                    "geo_entity_issues": "entity_issue_id",
                    "geo_relationship_issues": "relationship_issue_id",
                    "geo_knowledge_item_issues": "knowledge_item_issue_id",
                    "geo_case_issues": "case_issue_id",
                    # 地政学ナレッジ＆インテリジェンスDB Phase2A（geo_phase2a_*系）
                    "geo_raw_snapshots": "raw_snapshot_id",
                    "geo_ingestion_runs": "ingestion_run_id",
                    "geo_ingestion_run_snapshots": "ingestion_run_snapshot_id",
                    "geo_entity_match_candidates": "match_candidate_id",
                    "geo_sanctions_entries": "sanctions_entry_id",
                    "geo_sanctions_entry_versions": "version_id",
                    "geo_metric_definitions": "metric_definition_id",
                    "geo_metric_observations": "metric_observation_id",
                    "geo_knowledge_item_claims": "knowledge_item_claim_id",
                    "geo_retrieval_runs": "retrieval_run_id",
                    "geo_retrieval_results": "retrieval_result_id",
                    # 地政学ナレッジ＆インテリジェンスDB Phase2B（geo_phase2b_*系）
                    "geo_indicator_alerts": "alert_id",
                    # 地政学ナレッジ＆インテリジェンスDB Phase2B-2（External Reference/Expert Review）
                    "geo_claim_issues": "claim_issue_id",
                    "geo_expert_review_sessions": "review_session_id",
                    "geo_expert_review_notes": "note_id",
                    "geo_expert_review_changes": "change_id",
                    "geo_expert_review_change_entities": "change_entity_id",
                    "geo_expert_review_change_issues": "change_issue_id",
                    "geo_expert_review_change_notes": "change_note_id",
                    # 地政学インテリジェンス基盤 Phase1: Geo Intelligence Agent
                    "geo_intelligence_requests": "request_id",
                    "geo_intelligence_responses": "response_id",
                    # 地政学インテリジェンス基盤 Phase3: Intelligence Need管理
                    "geo_knowledge_gap_requests": "gap_request_id",
                    # 地政学インテリジェンス基盤 Phase4: Report Upload
                    "geo_reports": "report_id",
                    "geo_report_gaps": "report_gap_id",
                    # Geopolitical News DB / External Intelligence Layer Phase1
                    "geo_external_signal_queries": "query_id",
                    "geo_external_signals": "external_signal_id",
                    "geo_external_signal_query_matches": "match_id",
                    "geo_external_signal_metrics": "metric_id",
                    # Geopolitical External Intelligence Layer Phase2A
                    "geo_external_events": "external_event_id",
                    "geo_external_event_signal_links": "link_id",
                    "geo_external_signal_annotations": "annotation_id",
                    "geo_actor_episode_candidates": "actor_episode_candidate_id",
                    "geo_media_episode_candidates": "media_episode_candidate_id",
                    # Geopolitical External Intelligence Layer Phase2B-1
                    "geo_episode_clusters": "episode_cluster_id",
                    "geo_episode_cluster_members": "cluster_member_id",
                    # Geopolitical External Intelligence Layer Phase2B-2A
                    "geo_episode_cluster_links": "cluster_link_id",
                    # Geopolitical External Intelligence Layer Phase2B-2B
                    "geo_episode_split_candidates": "split_candidate_id",
                    # Geopolitical External Intelligence Layer Phase2B-3A-1
                    "geo_statement_candidates": "statement_candidate_id",
                    # Geopolitical Intelligence OS Phase 2G-1 (Theme Discovery)
                    "geo_theme_candidates": "candidate_id",
                    # Geopolitical Intelligence OS Phase B（Analysis Session/Review/Approval/Learning）
                    "geo_ios_analysis_sessions": "analysis_session_id",
                    "geo_ios_scenario_candidates": "scenario_candidate_id",
                    "geo_ios_analysis_designs": "analysis_design_id",
                    "geo_ios_analysis_contexts": "analysis_context_id",
                    "geo_ios_intake_turns": "intake_turn_id",
                    "geo_ios_session_attachments": "attachment_id",
                    "geo_ios_review_cycles": "review_cycle_id",
                    "geo_ios_review_comments": "review_comment_id",
                    "geo_ios_approval_events": "approval_event_id",
                    "geo_ios_learning_candidates": "candidate_id",
                    "geo_ios_consultation_turns": "consultation_turn_id",
                    "geo_ios_report_drafts": "report_draft_id",
                    "geo_ios_draft_ai_reviews": "draft_ai_review_id",
                    "geo_ios_learning_candidate_revisions": "revision_id",
                    "geo_ios_learning_workbench_turns": "workbench_turn_id",
                    "geo_expert_session_case_runs": "run_id",
                    "geo_expert_session_evaluations": "evaluation_id",
                    "geo_report_review_v2_runs": "run_id",
                    # Source Library / Monitoring（2026-10-05）
                    "geo_source_tags": "tag_id", "geo_source_theme_links": "link_id",
                    "geo_source_domains": "domain_id", "geo_source_sets": "source_set_id",
                    "geo_monitoring_themes": "monitoring_theme_id", "geo_monitoring_alerts": "alert_id",
                    "geo_report_projects": "project_id"}.get(table)
        out = []
        with self._lock:
            for row in rows:
                r = dict(row)
                if id_field and id_field not in r:
                    r[id_field] = self._next_id(id_field)
                # 実DBのcreated_at既定値(now())を模す。固定日付だと、時間経過とともに
                # 「直近N日」ウィンドウの判定テストが実時刻とズレて壊れる
                # （2026-07-17固定時代に実際に発生した不具合。詳細はPhase 3B Step 2の調査記録）
                r.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                if table == "geo_ios_approval_events":
                    # 実DBのapproval_date既定値(not null default now())を模す（Phase E:
                    # publish_approved_reportのversion supersession比較がapproval_dateに依存する）。
                    r.setdefault("approval_date", datetime.now(timezone.utc).isoformat())
                if table == "geo_raw_snapshots":
                    r.setdefault("fetched_at", "2026-07-17T00:00:00+00:00")
                if table == "geo_ingestion_runs":
                    # 実DBのstarted_at既定値(not null default now())を模す。GDELT Operationalizationの
                    # completed_through watermark系ヘルパー（_find_last_catchup_watermark/
                    # _find_last_doc_watermark）がstarted_atで最新行を比較するため必須
                    r.setdefault("started_at", datetime.now(timezone.utc).isoformat())
                self.tables.setdefault(table, []).append(r)
                self.inserted.setdefault(table, []).append(r)
                out.append(r)
        return out

    def update(self, table: str, params: dict, patch: dict) -> None:
        with self._lock:
            for row in self.tables.get(table, []):
                if self._matches(row, params):
                    row.update(patch)
            self.updated.append((table, dict(params), dict(patch)))

    def delete(self, table: str, params: dict) -> None:
        with self._lock:
            remaining = [r for r in self.tables.get(table, []) if not self._matches(r, params)]
            self.tables[table] = remaining
            if not hasattr(self, "deleted"):
                self.deleted = []
            self.deleted.append((table, dict(params)))

    def rpc(self, fn_name: str, params: dict = None) -> list:
        """geo_search_service等のFTS用RPC呼び出しをテストで差し替えられるようにする最小限のスタブ。
        既定では空リストを返す。テスト側でrpc_responses[fn_name] = [...] を設定して固定応答を返せる。"""
        if fn_name == "geo_handoff_create_session_if_absent":
            return self._rpc_handoff_create_session_if_absent(params or {})
        if not hasattr(self, "rpc_responses"):
            self.rpc_responses = {}
        return self.rpc_responses.get(fn_name, [])

    def _rpc_handoff_create_session_if_absent(self, params: dict) -> dict:
        """Phase 2G-5 fix: sql/2026-10-01_geo_handoff_create_session_if_absent.sqlの
        check-and-create（advisory lock）をin-memoryで再現する。self._lockで一括りに
        することで、複数スレッドから同時に呼ばれてもSessionが1件しか作られないことを
        テストで確認できるようにする（insert()は内部で同じlockを取るため、ここでは
        insert()を呼ばず最小限のrow組み立てを直接行う——再入不可なLockでのdeadlock回避）。"""
        candidate_id = params.get("p_candidate_id")
        with self._lock:
            existing = [r for r in self.tables.get("geo_ios_analysis_sessions", [])
                        if r.get("source_theme_candidate_id") == candidate_id]
            if existing:
                existing.sort(key=lambda r: r.get("created_at", ""))
                return {"session": existing[0], "created": False}

            row = {
                "analysis_session_id": self._next_id("analysis_session_id"),
                "title": params.get("p_title"), "theme": params.get("p_theme"),
                "user_id": params.get("p_user_id"), "status": "active",
                "mode": params.get("p_mode") or "create",
                "source_theme_candidate_id": candidate_id,
                "selected_scenario_id": None, "linked_report_project_id": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.tables.setdefault("geo_ios_analysis_sessions", []).append(row)
            self.inserted.setdefault("geo_ios_analysis_sessions", []).append(row)
            return {"session": row, "created": True}

    # -----------------------------------------------------------------------
    # Storage（Report Studio v2 Phase 3: geo_db_client.SupabaseClient.storage_*と
    # 同じインターフェースのin-memoryフェイク）
    # -----------------------------------------------------------------------

    def storage_upload(self, bucket: str, path: str, data: bytes, *, content_type: str = "application/octet-stream",
                        upsert: bool = True) -> None:
        with self._lock:
            if not hasattr(self, "storage_objects"):
                self.storage_objects = {}
            self.storage_objects[(bucket, path)] = bytes(data)

    def storage_download(self, bucket: str, path: str) -> bytes:
        if not hasattr(self, "storage_objects") or (bucket, path) not in self.storage_objects:
            raise FileNotFoundError(f"storage object not found: {bucket}/{path}")
        return self.storage_objects[(bucket, path)]

    def storage_delete(self, bucket: str, path: str) -> None:
        if hasattr(self, "storage_objects"):
            self.storage_objects.pop((bucket, path), None)


class FakeKnowledgeStore:
    """knowledge_store.search() と同じインターフェースの固定応答フェイク"""

    def __init__(self, docs: list = None):
        self.docs = docs or [
            {"document_id": "KB-TEST-001", "title": "テスト用公式コンテキスト",
             "content": "テスト用の当社公式方針の説明。", "source_url": "https://example.com/policy",
             "theme_ids": ["water"], "document_type": "policy", "section_type": "policy",
             "authority_level": 1, "retrieved_at": "2026-07-17", "content_hash": "dummy"},
        ]
        self.calls = []

    def search(self, query, themes=None, document_type=None, authority_level=None,
               source_url=None, top_k=8):
        self.calls.append(query)
        return self.docs[:top_k]
