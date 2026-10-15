import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_run_provisioning_service as provisioning  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


class PinningFakeClient(FakeSupabaseClient):
    """`geo_report_pin_run_snapshot` RPC（sql/2026-09-11b_geo_report_pin_run_snapshot.sql）と
    同じ副作用をPythonで再現するFake。実SQL関数の正当性自体は本番Supabaseへ適用・検証済み
    （読み取り専用スキーマ検証、2026-09-11実施）——このFakeは、Pythonラッパー側の呼び出し
    ロジック（何を・いつ・どのRunへ固定するか）を高速にユニットテストするためのもの。"""

    def rpc(self, fn_name: str, params: dict = None):
        if fn_name != "geo_report_pin_run_snapshot":
            return super().rpc(fn_name, params)
        p = params
        design_versions = [
            r for r in self.tables.get("geo_report_design_versions", [])
            if r["id"] == p["p_design_version_id"] and r["project_id"] == p["p_project_id"]
        ]
        if not design_versions:
            raise RuntimeError(
                f"design_version_id {p['p_design_version_id']} がproject_id {p['p_project_id']} と一致しません")
        dv = design_versions[0]
        runs = [
            r for r in self.tables.get("geo_report_collection_runs", [])
            if r["run_id"] == p["p_run_id"] and r["project_id"] == p["p_project_id"]
        ]
        if not runs:
            raise RuntimeError(f"run_id {p['p_run_id']} がproject_id {p['p_project_id']} と一致しません")
        run = runs[0]
        run.update({
            "design_version_id": p["p_design_version_id"],
            "narrative_master_asset_id": dv["narrative_master_asset_id"],
            "report_template_asset_id": dv["report_template_asset_id"],
            "prompt1_contract_version": p["p_prompt1_contract_version"],
            "prompt1_instruction_fingerprint": p["p_prompt1_instruction_fingerprint"],
            "prompt2_contract_version": p["p_prompt2_contract_version"],
            "prompt2_instruction_fingerprint": p["p_prompt2_instruction_fingerprint"],
            "preprocess_contract_version": p["p_preprocess_contract_version"],
            "aggregation_contract_version": p["p_aggregation_contract_version"],
            "renderer_version": p["p_renderer_version"],
            "application_build_revision": p["p_application_build_revision"],
            "baseline_prompt2_execution_id": p["p_baseline_prompt2_execution_id"],
            "previous_continuity_artifact_id": p["p_previous_continuity_artifact_id"],
        })
        self.insert("geo_report_run_config", [{
            "run_id": p["p_run_id"], "config_json": p["p_report_config_json"],
            "primary_graph_signals": p["p_primary_graph_signals"],
            "common_primary_graph_start": p["p_common_primary_graph_start"],
        }])
        self.insert("geo_report_run_artifacts", [
            {"id": self._next_id("artifact"), "run_id": p["p_run_id"], "execution_id": None,
             "execution_method": "system_snapshot", "artifact_type": "prompt_snapshot",
             "file_name": "prompt1_instruction.md", "content_text": p["p_prompt1_instruction_text"],
             "sha256": p["p_prompt1_instruction_fingerprint"],
             "size_bytes": len(p["p_prompt1_instruction_text"]), "media_type": "text/markdown"},
            {"id": self._next_id("artifact"), "run_id": p["p_run_id"], "execution_id": None,
             "execution_method": "system_snapshot", "artifact_type": "prompt_snapshot",
             "file_name": "prompt2_instruction.md", "content_text": p["p_prompt2_instruction_text"],
             "sha256": p["p_prompt2_instruction_fingerprint"],
             "size_bytes": len(p["p_prompt2_instruction_text"]), "media_type": "text/markdown"},
        ])
        return dict(run)


def _seed_design_version(client, project_id, version, *, nm_asset_id, rt_asset_id, report_design=None,
                           signals=None):
    dv_id = f"dv-{project_id}-{version}"
    design = dict(report_design or {})
    if design.get("analysis_mode", "public_voice_monitor") == "public_voice_monitor":
        design.setdefault("report_kind", "consumption_tax_food_price")      # Report Type未指定のDesignは止まるため既定を補う
    client.insert("geo_report_design_versions", [{
        "id": dv_id, "project_id": project_id, "version": version,
        "report_design": design, "signals": signals or [], "queries": [],
        "narrative_master_asset_id": nm_asset_id, "report_template_asset_id": rt_asset_id,
    }])
    return dv_id


def test_load_prompt_instruction_reads_versioned_resource_with_fingerprint():
    prompt1 = provisioning.load_prompt_instruction("prompt1")
    assert prompt1["contract_version"] == "v4.3"
    assert len(prompt1["text"]) > 1000
    assert len(prompt1["fingerprint"]) == 64  # sha256 hex

    prompt2 = provisioning.load_prompt_instruction("prompt2")
    assert prompt2["contract_version"] == "v4.3"
    assert prompt2["fingerprint"] != prompt1["fingerprint"]


def test_load_prompt_instruction_rejects_unknown_stage():
    try:
        provisioning.load_prompt_instruction("prompt3")
        assert False, "should have raised"
    except ValueError as exc:
        assert "prompt3" in str(exc)


def test_get_latest_published_design_version_requires_at_least_one():
    client = FakeSupabaseClient()
    try:
        provisioning.get_latest_published_design_version(client, "proj-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "Published Design Version" in str(exc)


def test_resolve_baseline_continuity_returns_none_when_no_baseline():
    client = FakeSupabaseClient()
    assert provisioning.resolve_baseline_continuity(client, None) == (None, None)


def test_resolve_baseline_continuity_returns_none_when_baseline_has_no_canonical_prompt2():
    client = FakeSupabaseClient()
    client.insert("geo_report_collection_runs", [{"run_id": "run-baseline", "project_id": "proj-1"}])
    assert provisioning.resolve_baseline_continuity(client, "run-baseline") == (None, None)


# ---------------------------------------------------------------------------
# シナリオ1: Design v1でRun A作成後、Design v2をPublishしてもRun Aのasset IDが変わらない
# ---------------------------------------------------------------------------

def test_scenario1_run_keeps_its_pinned_assets_after_later_design_publish():
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1")

    run_a = provisioning.create_run_with_pinned_snapshot(client, project_id)
    assert run_a["design_version_id"] == f"dv-{project_id}-1"
    assert run_a["narrative_master_asset_id"] == "nm-v1"
    assert run_a["report_template_asset_id"] == "rt-v1"

    # Design v2をPublish（新しいAsset）
    _seed_design_version(client, project_id, 2, nm_asset_id="nm-v2", rt_asset_id="rt-v2")

    # Run Aの行を再取得しても、v1のまま変わらない
    run_a_reloaded = client.select("geo_report_collection_runs", {
        "select": "*", "run_id": f"eq.{run_a['run_id']}",
    })[0]
    assert run_a_reloaded["design_version_id"] == f"dv-{project_id}-1"
    assert run_a_reloaded["narrative_master_asset_id"] == "nm-v1"
    assert run_a_reloaded["report_template_asset_id"] == "rt-v1"

    # 新規Run Bを作れば、今度はv2に固定される
    run_b = provisioning.create_run_with_pinned_snapshot(client, project_id)
    assert run_b["design_version_id"] == f"dv-{project_id}-2"
    assert run_b["narrative_master_asset_id"] == "nm-v2"
    assert run_b["report_template_asset_id"] == "rt-v2"


# ---------------------------------------------------------------------------
# シナリオ2: Baseline RunのCanonical Prompt2を後から切り替えても、
#            Run BのPrevious Continuity Snapshotが変わらない
# ---------------------------------------------------------------------------

def test_scenario2_previous_continuity_snapshot_is_immune_to_later_baseline_canonical_switch():
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1")

    # Baseline Run（既にPrompt2まで実行済み、という体でcanonical_prompt2_execution_idを持つ）
    client.insert("geo_report_collection_runs", [{
        "run_id": "run-baseline", "project_id": project_id,
        "canonical_prompt2_execution_id": "exec-prompt2-A",
    }])
    client.insert("geo_report_run_artifacts", [{
        "id": "artifact-continuity-A", "run_id": "run-baseline", "execution_id": "exec-prompt2-A",
        "execution_method": "application_ai", "artifact_type": "prompt2_output",
        "file_name": "continuity_context_next.json", "content_text": '{"schema_version":"1.2"}',
        "sha256": "dummy-a", "size_bytes": 10, "media_type": "application/json",
    }])

    run_b = provisioning.create_run_with_pinned_snapshot(client, project_id, baseline_run_id="run-baseline")
    assert run_b["baseline_prompt2_execution_id"] == "exec-prompt2-A"
    assert run_b["previous_continuity_artifact_id"] == "artifact-continuity-A"

    # Baseline側で後からPrompt2を再実行し、Canonicalを別のExecutionへ切り替えたとする
    client.update("geo_report_collection_runs", {"run_id": "eq.run-baseline"}, {
        "canonical_prompt2_execution_id": "exec-prompt2-B",
    })
    client.insert("geo_report_run_artifacts", [{
        "id": "artifact-continuity-B", "run_id": "run-baseline", "execution_id": "exec-prompt2-B",
        "execution_method": "chatgpt_import", "artifact_type": "prompt2_output",
        "file_name": "continuity_context_next.json", "content_text": '{"schema_version":"1.2","changed":true}',
        "sha256": "dummy-b", "size_bytes": 10, "media_type": "application/json",
    }])

    # Run Bの固定済みポインタは変わらない
    run_b_reloaded = client.select("geo_report_collection_runs", {
        "select": "*", "run_id": f"eq.{run_b['run_id']}",
    })[0]
    assert run_b_reloaded["baseline_prompt2_execution_id"] == "exec-prompt2-A"
    assert run_b_reloaded["previous_continuity_artifact_id"] == "artifact-continuity-A"


# ---------------------------------------------------------------------------
# シナリオ3: Prompt Resourceを更新しても既存Runのfingerprint/本文が変わらない
# ---------------------------------------------------------------------------

def test_scenario3_existing_run_keeps_its_prompt_snapshot_after_resource_update(monkeypatch):
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1")

    original_loader = provisioning.load_prompt_instruction
    v43 = original_loader("prompt1")

    run_a = provisioning.create_run_with_pinned_snapshot(client, project_id)
    assert run_a["prompt1_contract_version"] == "v4.3"
    assert run_a["prompt1_instruction_fingerprint"] == v43["fingerprint"]

    # Prompt1がv4.4へ更新された、という状況をモックで模す（実ファイルは書き換えない）
    def _fake_loader(stage, *, version=None):
        if stage == "prompt1":
            return {"contract_version": "v4.4", "text": "updated prompt1 text targeted_stance_classified",
                    "fingerprint": "fingerprint-v4.4"}
        return original_loader(stage, version=version)

    monkeypatch.setattr(provisioning, "load_prompt_instruction", _fake_loader)

    # 既存Run Aのfingerprint/本文はSnapshotされたまま変わらない
    run_a_reloaded = client.select("geo_report_collection_runs", {
        "select": "*", "run_id": f"eq.{run_a['run_id']}",
    })[0]
    assert run_a_reloaded["prompt1_contract_version"] == "v4.3"
    assert run_a_reloaded["prompt1_instruction_fingerprint"] == v43["fingerprint"]

    # 新規Run Bは更新後のv4.4で固定される
    run_b = provisioning.create_run_with_pinned_snapshot(client, project_id)
    assert run_b["prompt1_contract_version"] == "v4.4"
    assert run_b["prompt1_instruction_fingerprint"] == "fingerprint-v4.4"


# ---------------------------------------------------------------------------
# シナリオ4: create_run途中失敗時にRunだけ残る/Snapshotだけ残るというpartial stateが発生しない
# ---------------------------------------------------------------------------

def test_scenario4_failed_pin_leaves_no_orphaned_run_when_no_design_published():
    client = PinningFakeClient()
    project_id = "proj-1"  # Design Versionを1件もPublishしていない

    try:
        provisioning.create_run_with_pinned_snapshot(client, project_id)
        assert False, "should have raised"
    except ValueError as exc:
        assert "Published Design Version" in str(exc)

    # 失敗したRun行がテーブルに残っていないこと
    assert client.tables.get("geo_report_collection_runs", []) == []
    assert client.tables.get("geo_report_run_config", []) == []
    assert client.tables.get("geo_report_run_artifacts", []) == []


def test_scenario4_failed_pin_leaves_no_orphaned_run_when_rpc_rejects(monkeypatch):
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1")

    # RPC呼び出し自体が失敗するケース（design_version_idの不整合等）をシミュレート
    def _broken_rpc(fn_name, params=None):
        if fn_name == "geo_report_pin_run_snapshot":
            raise RuntimeError("simulated atomic transaction failure")
        return []

    monkeypatch.setattr(client, "rpc", _broken_rpc)

    try:
        provisioning.create_run_with_pinned_snapshot(client, project_id)
        assert False, "should have raised"
    except RuntimeError as exc:
        assert "simulated" in str(exc)

    # Run行自体（前処理より前に作られた空行）もクリーンアップされ、
    # Config/Artifact行は最初から作られない（RPCが失敗してPython側まで戻ってきたため）
    assert client.tables.get("geo_report_collection_runs", []) == []
    assert client.tables.get("geo_report_run_config", []) == []
    assert client.tables.get("geo_report_run_artifacts", []) == []


def test_pin_run_snapshot_converts_rpc_http_error_into_value_error_with_postgres_message(monkeypatch):
    """2026-09-14発覚: RPCがDB制約違反（例: date型カラムへ"Pre-summit"のような不正な
    文字列）で400を返すと、requests.exceptions.HTTPErrorがそのままAPI層まで伝播し
    500 Internal Server Errorになっていた（ブラウザには「Failed to fetch」としか
    見えず原因不明だった）。Postgresのエラーメッセージを含むValueErrorへ変換され、
    既存のValueError→HTTPException 400のパターンへ合流することを確認する。"""
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1")

    class _FakeErrorResponse:
        def json(self):
            return {"code": "22007", "message": 'invalid input syntax for type date: "Pre-summit"'}

    def _rpc_raises_http_error(fn_name, params=None):
        if fn_name == "geo_report_pin_run_snapshot":
            raise requests.exceptions.HTTPError("400 Client Error", response=_FakeErrorResponse())
        return []

    monkeypatch.setattr(client, "rpc", _rpc_raises_http_error)

    try:
        provisioning.create_run_with_pinned_snapshot(client, project_id)
        assert False, "should have raised"
    except ValueError as exc:
        assert 'invalid input syntax for type date: "Pre-summit"' in str(exc)

    assert client.tables.get("geo_report_collection_runs", []) == []


def test_pin_run_snapshot_atomicity_when_run_row_missing_before_rpc():
    """pin_run_snapshot単体（create_run_with_pinned_snapshot経由でない）を、
    存在しないrun_idへ直接呼んだ場合、Fake RPCがrun未検出で例外を送出し、
    Config/Artifact行が一切作られないこと（実SQL関数の「先にUPDATEで行を探し、
    見つからなければraise exception」という順序と同じ振る舞い）。"""
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1")

    try:
        provisioning.pin_run_snapshot(client, "run-does-not-exist", project_id)
        assert False, "should have raised"
    except RuntimeError as exc:
        assert "run-does-not-exist" in str(exc)

    assert client.tables.get("geo_report_run_config", []) == []
    assert client.tables.get("geo_report_run_artifacts", []) == []


# ---------------------------------------------------------------------------
# autofill_prompt2_report_config（2026-09-15追加）: primary_graph_signals/
# final_graph_ruleが未設定のままPublish/Runが進み、Aggregation実行で初めて
# 「Calculating trendsが失敗します」に気づくという同種の報告が繰り返し発生した
# ため、警告だけでなく実際に安全な既定値で自動補完する。
# ---------------------------------------------------------------------------

def test_autofill_fills_primary_graph_signals_from_core_signals_when_unset():
    signals = [
        {"signal_id": "S1", "continuity_role": "Core"},
        {"signal_id": "S2", "continuity_role": "Core"},
        {"signal_id": "S3", "continuity_role": "Watch"},
    ]
    result = provisioning.autofill_prompt2_report_config({}, signals)
    assert result["primary_graph_signals"] == ["S1", "S2"]
    assert result["final_graph_rule"]


def test_autofill_leaves_explicit_primary_graph_signals_untouched():
    """ユーザー/Design ChatのAIが明示的に設定した値は、Core Signalの構成に
    関わらず一切変更しない。"""
    signals = [{"signal_id": "S1", "continuity_role": "Core"}]
    result = provisioning.autofill_prompt2_report_config(
        {"primary_graph_signals": ["S7"]}, signals)
    assert result["primary_graph_signals"] == ["S7"]


def test_autofill_respects_explicit_empty_primary_graph_signals():
    """2026-09-15実機報告で発覚した重大なバグの再現テスト: Design側が意図的に
    `primary_graph_signals: []`（＝このプロジェクトはグラフ機能を使わないという
    明示的な設計判断）を設定していても、以前は「未設定」と誤判定してCore Signalを
    勝手に上書きしていた。空リストは明示的な判断として尊重し、上書きしないこと。"""
    signals = [
        {"signal_id": "S1", "continuity_role": "Core"},
        {"signal_id": "S2", "continuity_role": "Core"},
    ]
    result = provisioning.autofill_prompt2_report_config(
        {"primary_graph_signals": []}, signals)
    assert result["primary_graph_signals"] == []


def test_autofill_respects_explicit_none_final_graph_rule():
    """final_graph_rule側も同様に、キーが存在し値がNoneでない限り上書きしない
    （ここではNoneを明示した場合は既定値で補完されることを確認する——
    Noneはキー自体が無いのと同義に扱う、という既定の挙動）。"""
    result = provisioning.autofill_prompt2_report_config(
        {"final_graph_rule": None}, [])
    assert result["final_graph_rule"]


def test_autofill_leaves_primary_graph_signals_empty_when_no_core_signal():
    """自動補完できない場合（Core Signalが1つも無い）は空のまま返し、
    validate_prompt2_report_configの警告で表面化させる。"""
    signals = [{"signal_id": "S3", "continuity_role": "Watch"}]
    result = provisioning.autofill_prompt2_report_config({}, signals)
    assert "primary_graph_signals" not in result
    assert result["final_graph_rule"]


def test_autofill_does_not_override_explicit_final_graph_rule():
    result = provisioning.autofill_prompt2_report_config(
        {"final_graph_rule": {"custom": True}}, [])
    assert result["final_graph_rule"] == {"custom": True}


def test_autofill_handles_none_report_design_and_signals():
    result = provisioning.autofill_prompt2_report_config(None, None)
    assert result["final_graph_rule"]
    assert "primary_graph_signals" not in result


def test_build_report_config_autofills_primary_graph_signals_from_core_signals():
    """build_report_config（Run作成時のSnapshot固定で使われる）自体も、Publishより
    前に作られた古いDesign Versionに対するフォールバックとして自動補完する。"""
    design_version = {
        "project_id": "proj-1", "version": 1,
        "report_design": {"common_primary_graph_start": "2026-09-01"},
        "signals": [
            {"signal_id": "S1", "continuity_role": "Core"},
            {"signal_id": "S2", "continuity_role": "Watch"},
        ],
    }
    config_json, primary_graph_signals, common_start = provisioning.build_report_config(design_version)
    assert primary_graph_signals == ["S1"]
    assert config_json["primary_graph_signals"] == ["S1"]
    assert common_start == "2026-09-01"
    assert config_json["final_graph_rule"]


def test_build_report_config_keeps_explicit_empty_primary_graph_signals_as_empty_not_none():
    """2026-09-15実機報告で発覚した再発バグの再現テスト: Design側が意図的に
    `primary_graph_signals: []`を設定している場合、build_report_configの戻り値
    （validate_prompt2_report_config用の2番目の値）はNoneにならず空リストのまま
    保持されること。config_json側（DB保存用）も[]のままであること。"""
    design_version = {
        "project_id": "proj-1", "version": 1,
        "report_design": {"primary_graph_signals": [], "common_primary_graph_start": "2026-09-01"},
        "signals": [{"signal_id": "S1", "continuity_role": "Core"}],
    }
    config_json, primary_graph_signals, common_start = provisioning.build_report_config(design_version)
    assert primary_graph_signals == []
    assert config_json["primary_graph_signals"] == []


# ---------------------------------------------------------------------------
# validate_prompt2_report_config（2026-09-15修正）: Noneと空リストの意味論
# ---------------------------------------------------------------------------

def test_validate_prompt2_report_config_warns_when_primary_graph_signals_is_none():
    problems = provisioning.validate_prompt2_report_config(
        {"final_graph_rule": "default"}, None, "2026-09-01", set())
    assert "primary_graph_signals" in problems


def test_validate_prompt2_report_config_does_not_warn_on_explicit_empty_primary_graph_signals():
    """2026-09-15実機報告の再現テスト: 「グラフを使わない」という明示的な設計判断
    （primary_graph_signals=[]）に対して、Run作成のたびに毎回同じ警告が繰り返し
    表示されてしまっていた。空リストは警告対象にしないこと。"""
    problems = provisioning.validate_prompt2_report_config(
        {"final_graph_rule": "default"}, [], "2026-09-01", set())
    assert "primary_graph_signals" not in problems


def test_pin_run_snapshot_does_not_warn_when_design_explicitly_disables_primary_graph():
    """pin_run_snapshot統合テスト（2026-09-15実機報告の再現）: Publishされた
    report_designがprimary_graph_signals=[]を明示している場合、Run作成結果の
    prompt2_config_warningsにprimary_graph_signalsが含まれないこと（Noneに
    正規化されて誤警告が繰り返し出てしまっていた不具合の再発防止）。"""
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(
        client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1",
        report_design={"primary_graph_signals": [], "common_primary_graph_start": "2026-09-01"},
    )

    run = provisioning.create_run_with_pinned_snapshot(client, project_id)
    assert "prompt2_config_warnings" not in run or \
        "primary_graph_signals" not in run.get("prompt2_config_warnings", [])


# ---------------------------------------------------------------------------
# analysis_mode分離（2026-09-15追加、外部監査指摘: Report DesignからRunへ
# analysis_modeをsnapshotし、public_voice_monitorとevidence_intelligenceで
# Prompt1契約・final_graph_ruleの扱いを分離する）
# ---------------------------------------------------------------------------

def test_autofill_does_not_default_final_graph_rule_for_evidence_intelligence():
    """evidence_intelligenceモードではPublic Voice 3-way分母ルールを自動補完しない
    （Official/Government/Industry/Expert/Media/Social等をすべてFirst-class
    Evidenceとして扱い、Public Voice sentimentを分母にしないため）。"""
    result = provisioning.autofill_prompt2_report_config(
        {"analysis_mode": "evidence_intelligence"}, [])
    assert "final_graph_rule" not in result


def test_autofill_still_defaults_final_graph_rule_for_public_voice_monitor():
    result = provisioning.autofill_prompt2_report_config(
        {"analysis_mode": "public_voice_monitor"}, [])
    assert result["final_graph_rule"]


def test_autofill_defaults_to_public_voice_monitor_when_analysis_mode_unset():
    """既存プロジェクトとの後方互換: analysis_modeが一度も設定されていなければ
    従来通りpublic_voice_monitorとして扱う。"""
    result = provisioning.autofill_prompt2_report_config({}, [])
    assert result["final_graph_rule"]


def test_validate_prompt2_report_config_does_not_warn_on_missing_final_graph_rule_for_evidence_intelligence():
    problems = provisioning.validate_prompt2_report_config(
        {}, [], "2026-09-01", set(), analysis_mode="evidence_intelligence")
    assert "final_graph_rule" not in problems


def test_validate_prompt2_report_config_still_warns_on_missing_final_graph_rule_for_public_voice_monitor():
    problems = provisioning.validate_prompt2_report_config(
        {}, [], "2026-09-01", set(), analysis_mode="public_voice_monitor")
    assert "final_graph_rule" in problems


def test_build_report_config_includes_analysis_mode():
    design_version = {
        "project_id": "proj-1", "version": 1,
        "report_design": {"analysis_mode": "evidence_intelligence", "primary_graph_signals": []},
        "signals": [],
    }
    config_json, _, _ = provisioning.build_report_config(design_version)
    assert config_json["analysis_mode"] == "evidence_intelligence"


def test_build_report_config_defaults_analysis_mode_to_public_voice_monitor():
    design_version = {"project_id": "proj-1", "version": 1, "report_design": {}, "signals": []}
    config_json, _, _ = provisioning.build_report_config(design_version)
    assert config_json["analysis_mode"] == "public_voice_monitor"


def test_pin_run_snapshot_selects_evidence_intelligence_prompt1_instruction():
    """pin_run_snapshot統合テスト: report_design.analysis_mode='evidence_intelligence'
    のDesignからRunを作ると、Runへsnapshotされるprompt1指示文が
    evidence_intelligence_v1版になること（public_voice_monitor版=v4.3とは
    別バージョンとしてfingerprintも異なること）。"""
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(
        client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1",
        report_design={"analysis_mode": "evidence_intelligence", "primary_graph_signals": []},
    )

    run = provisioning.create_run_with_pinned_snapshot(client, project_id)
    assert run["prompt1_contract_version"] == "evidence_intelligence_v1"

    artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "run_id": f"eq.{run['run_id']}", "file_name": "eq.prompt1_instruction.md",
    })
    assert len(artifacts) == 1
    assert "Evidence Intelligence" in artifacts[0]["content_text"]


def test_pin_run_snapshot_selects_public_voice_monitor_prompt1_instruction_by_default():
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1")

    run = provisioning.create_run_with_pinned_snapshot(client, project_id)
    assert run["prompt1_contract_version"] == "v4.3"


def test_pin_run_snapshot_selects_evidence_intelligence_prompt2_instruction():
    """Phase H: analysis_mode='evidence_intelligence'のRunはprompt2_contract_versionも
    evidence_intelligence_v1になり、実際のinstruction本文もEvidence Intelligence版が
    snapshotされること（Prompt1と同じdispatchパターン）。"""
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(
        client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1",
        report_design={"analysis_mode": "evidence_intelligence", "primary_graph_signals": []},
    )

    run = provisioning.create_run_with_pinned_snapshot(client, project_id)
    assert run["prompt2_contract_version"] == "evidence_intelligence_v1"

    artifacts = client.select("geo_report_run_artifacts", {
        "select": "*", "run_id": f"eq.{run['run_id']}", "file_name": "eq.prompt2_instruction.md",
    })
    assert len(artifacts) == 1
    assert "Evidence Intelligence" in artifacts[0]["content_text"]


def test_pin_run_snapshot_selects_public_voice_monitor_prompt2_instruction_by_default():
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1")

    run = provisioning.create_run_with_pinned_snapshot(client, project_id)
    assert run["prompt2_contract_version"] == "v4.3"


# ---------------------------------------------------------------------------
# discovery_signal_ids（2026-09-16追加、#4）: Signal定義のdiscovery_modeを
# Run Configへsnapshotする。
# ---------------------------------------------------------------------------

def test_build_report_config_collects_discovery_signal_ids():
    design_version = {
        "project_id": "proj-1", "version": 1, "report_design": {"primary_graph_signals": []},
        "signals": [
            {"signal_id": "S1", "continuity_role": "Core", "discovery_mode": False},
            {"signal_id": "S6", "continuity_role": "Event", "discovery_mode": True},
        ],
    }
    config_json, _, _ = provisioning.build_report_config(design_version)
    assert config_json["discovery_signal_ids"] == ["S6"]


def test_build_report_config_discovery_signal_ids_empty_when_none_marked():
    design_version = {
        "project_id": "proj-1", "version": 1, "report_design": {"primary_graph_signals": []},
        "signals": [{"signal_id": "S1", "continuity_role": "Core", "discovery_mode": False}],
    }
    config_json, _, _ = provisioning.build_report_config(design_version)
    assert config_json["discovery_signal_ids"] == []


def test_pin_run_snapshot_propagates_discovery_signal_ids_to_run_config():
    client = PinningFakeClient()
    project_id = "proj-1"
    _seed_design_version(
        client, project_id, 1, nm_asset_id="nm-v1", rt_asset_id="rt-v1",
        report_design={"primary_graph_signals": []},
        signals=[
            {"signal_id": "S1", "continuity_role": "Core", "discovery_mode": False},
            {"signal_id": "S6", "continuity_role": "Event", "discovery_mode": True},
        ],
    )

    run = provisioning.create_run_with_pinned_snapshot(client, project_id)
    run_config = client.select("geo_report_run_config", {"select": "config_json", "run_id": f"eq.{run['run_id']}"})
    assert run_config[0]["config_json"]["discovery_signal_ids"] == ["S6"]
