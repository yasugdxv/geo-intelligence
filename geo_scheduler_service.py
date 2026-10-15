"""Phase D: Production Scheduling / Daily Operations — Job Orchestration。

既存のConnector実行ロジック（GDELT News DOC/Stream・IMF・World Bank・OFAC）・
catch-up/重複防止ロジック・Theme Candidate生成・Episode Cluster判定・Alert判定は
一切変更しない。本モジュールは「いつ・どのJobを・依存関係とlockを見ながら・
失敗したら安全に再試行する」というオーケストレーション層のみを追加する。

本番Linuxサーバは未構築（Phase A確定事項）のため、ここでは「Scheduler Ready」
（Job定義・状態追跡・lock・stale検知・catch-upギャップ計算・retry方針・dry run・
手動trigger）までを完成させる。実OSレベルのcron/systemd timer登録は行わない
（docs/production-scheduling-runbook.md参照）。

状態はgeo_ingestion_runsのみを使う（新規table無し）。Job Lockも、既存の
status='running'行をそのままlockとして扱う（DB based lockの新設不要）。

Human Gate（Episode Cluster Review→Accept）とTheme Candidate Syncは、ここでは
一切自動実行しない——THEME_CANDIDATE_SYNCはjobs（Scheduler対象）ではなく
manual_only_jobsとして registry に分離されており、run_job()の対象外。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

import geo_connector_base as base
import geo_connector_gdelt
import geo_connector_gdelt_stream
import geo_connector_imf
import geo_connector_federal_register
import geo_connector_html_list
import geo_connector_ukmto
import geo_connector_ofac
import geo_connector_rss
import geo_connector_sanctions_xml
import geo_direct_collection_common
import geo_connector_world_bank
import geo_gdelt_rate_limit

JOB_REGISTRY_PATH = Path(__file__).parent / "config" / "scheduler_jobs.yaml"

# item22/23: retryable/non_retryableの分類。既存コードを変更せず、Phase A/Dで実機確認した
# 実際の例外パターン（文字列）に基づく（新しい判定ロジックではなく、観測済み事実の分類）。
_RETRYABLE_MARKERS = (
    "ProxyError", "ConnectionError", "ReadTimeout", "ConnectTimeout", "Timeout",
    "NameResolutionError", "RemoteDisconnected", "GDELT status=429", "GDELT status=5",
)
_NON_RETRYABLE_MARKERS = (
    "ConnectorDisabledError", "credential", "schema", "ValidationError",
)


class SchedulerError(Exception):
    """Job定義不備・実行前提条件違反等、Scheduler自体の失敗。"""


def load_job_registry() -> dict:
    with open(JOB_REGISTRY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _all_job_defs(registry: dict) -> dict:
    """Scheduler対象（jobs）とHuman Gate後の参考情報（manual_only_jobs）を合わせて返す
    （状態照会には両方必要だが、run_job()はjobsのみを対象にする）。"""
    return {**registry.get("jobs", {}), **registry.get("manual_only_jobs", {})}


_FRACTION_RE = re.compile(r"\.(\d+)")


def _parse(value) -> datetime | None:
    """DB(PostgREST)のタイムスタンプをdatetimeにする。小数秒の桁数は一定ではない
    （例: '...48.67346+00:00' は5桁）。Python 3.10のfromisoformatは6桁(または3桁)しか受け付けないため、
    小数部を6桁へそろえてから解釈する（実データで発見した不具合）。"""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    text = _FRACTION_RE.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def classify_error(error_summary: str | None) -> str:
    """retryable / non_retryable / unknown。item22。"""
    if not error_summary:
        return "unknown"
    for marker in _NON_RETRYABLE_MARKERS:
        if marker.lower() in error_summary.lower():
            return "non_retryable"
    for marker in _RETRYABLE_MARKERS:
        if marker.lower() in error_summary.lower():
            return "retryable"
    return "unknown"


def _connector_types_for_job(job: dict) -> list:
    out = [job["connector_type"]]
    if job.get("catchup_connector_type"):
        out.append(job["catchup_connector_type"])
    return out


def _latest_run(client, connector_type: str, *, is_demo: bool = False) -> dict | None:
    rows = client.select("geo_ingestion_runs", {
        "select": "*", "connector_type": f"eq.{connector_type}",
        "is_demo": f"eq.{str(is_demo).lower()}", "order": "started_at.desc", "limit": "1",
    })
    return rows[0] if rows else None


def is_stale(run: dict | None, *, stale_after_hours: float, now: datetime | None = None) -> bool:
    """item9: running状態のまま一定時間が経過した場合はstale（interrupted）とみなす。
    自動的に成功扱いにはしない——呼び出し側がdetect_and_mark_stale_jobsでfailedへ補正する。"""
    if not run or run.get("status") != "running":
        return False
    now = now or datetime.now(timezone.utc)
    started = _parse(run.get("started_at"))
    if not started:
        return False
    return (now - started) > timedelta(hours=stale_after_hours)


def is_job_locked(client, job: dict, *, is_demo: bool = False, now: datetime | None = None) -> bool:
    """item10: staleでない'running'行が既にあればlock中とみなす（重複起動防止）。
    新しいlock tableは作らず、既存geo_ingestion_runs.statusをそのまま使う。"""
    stale_hours = job.get("stale_after_hours", 6)
    for ct in _connector_types_for_job(job):
        run = _latest_run(client, ct, is_demo=is_demo)
        if run and run.get("status") == "running" and not is_stale(run, stale_after_hours=stale_hours, now=now):
            return True
    return False


def detect_and_mark_stale_jobs(client, *, registry: dict = None, is_demo: bool = False,
                                now: datetime | None = None) -> list:
    """item9: running状態のまま一定時間heartbeatが無いJobを検出し、安全にfailedへ補正する。
    成功扱いには絶対にしない。マークしたJobの一覧を返す（手動retry対象の可視化に使う）。"""
    registry = registry or load_job_registry()
    now = now or datetime.now(timezone.utc)
    marked = []
    for job_id, job in registry.get("jobs", {}).items():
        if job.get("enabled") is False:
            continue
        stale_hours = job.get("stale_after_hours", 6)
        for ct in _connector_types_for_job(job):
            run = _latest_run(client, ct, is_demo=is_demo)
            if run and is_stale(run, stale_after_hours=stale_hours, now=now):
                base.complete_ingestion_run(
                    client, ingestion_run_id=run["ingestion_run_id"], status="failed",
                    error_summary=(
                        f"stale: status=runningのまま{stale_hours}時間以上経過したため、"
                        "Scheduler/Orchestrationが自動的にfailedへ補正しました"
                        "（自動的に成功扱いにはしていません。安全に再試行可能です）。"),
                )
                marked.append({"job_id": job_id, "connector_type": ct,
                               "ingestion_run_id": run["ingestion_run_id"],
                               "started_at": run.get("started_at")})
    return marked


def check_dependencies_satisfied(client, job: dict, *, registry: dict, is_demo: bool = False,
                                 now: datetime | None = None) -> tuple[bool, str]:
    """item5/7: 依存Jobは「順序」だけを保証する——依存Jobが**今まさに実行中**の間は待つ。
    依存Jobが失敗している/未実行でも後続は止めない（1 Sourceの失敗で全Pipelineを止めない）。
    GDELT rawのURL一致は「既にDBにあるSignal」に対して行うため、News DOCの直近実行が失敗
    していても、取得済みのSignalに対して安全に下流処理できる。"""
    for dep_id in job.get("depends_on", []):
        dep = registry["jobs"].get(dep_id)
        if dep is None:
            return False, f"依存Job定義が見つかりません: {dep_id}"
        if is_job_locked(client, dep, is_demo=is_demo, now=now):
            return False, f"依存Job {dep_id} が実行中のため、完了を待ちます（次のtickで再判定）"
    return True, ""


def compute_catchup_gap(client, job_id: str, job: dict, *, is_demo: bool = False,
                         now: datetime | None = None) -> dict | None:
    """item8: last_successからの実際のgapを計算する。GDELT News DOCについては、
    Connector自身のresolve_doc_incremental_sinceが持つ「7日で暗黙打ち切り」というバグ
    （Phase A発見、コードは変更していない）を、呼び出し側のこの計算で回避する——
    geo_external_signal_queries.last_run_atという「真の」最終成功時刻を直接見る。"""
    now = now or datetime.now(timezone.utc)
    catchup_cfg = job.get("catchup") or {}
    if not catchup_cfg.get("enabled"):
        return None

    if job_id == "COLLECTION_GDELT_DOC":
        rows = client.select("geo_external_signal_queries", {
            "select": "name,last_run_at", "enabled": "eq.true"})
        timestamps = [(r["name"], _parse(r["last_run_at"])) for r in rows if r.get("last_run_at")]
        if not timestamps:
            return {"gap_days": None, "note": "last_run_at未設定（初回実行）"}
        oldest_name, oldest = min(timestamps, key=lambda t: t[1])
        gap_days = (now - oldest).days
        max_window = catchup_cfg.get("max_single_window_days", 7)
        return {
            "gap_days": gap_days, "oldest_query_name": oldest_name, "oldest_last_run_at": _iso(oldest),
            "exceeds_implicit_floor": gap_days > max_window,
            "recommended_since": oldest.strftime("%Y%m%d%H%M%S") if gap_days > max_window else None,
            "staged_window_days": max_window,
        }

    run = _latest_run(client, job["connector_type"], is_demo=is_demo)
    last_success = None
    if run and run.get("status") in ("completed", "completed_with_warnings"):
        last_success = _parse(run.get("completed_at") or run.get("started_at"))
    if last_success is None:
        return {"gap_days": None, "note": "成功履歴なし"}
    return {"gap_days": (now - last_success).days}


def dry_run_job(client, job_id: str, *, registry: dict = None, is_demo: bool = False,
                 now: datetime | None = None) -> dict:
    """item21: DB変更しない。対象Source・実行予定・catch-up期間・downstream処理予定だけを返す。"""
    registry = registry or load_job_registry()
    job = _all_job_defs(registry).get(job_id)
    if job is None:
        raise SchedulerError(f"未定義のJobです: {job_id}")
    now = now or datetime.now(timezone.utc)

    is_manual_only = job_id in registry.get("manual_only_jobs", {})
    enabled = job.get("enabled", True) is not False
    locked = False if is_manual_only else is_job_locked(client, job, is_demo=is_demo, now=now)
    deps_ok, deps_reason = (True, "") if is_manual_only else check_dependencies_satisfied(
        client, job, registry=registry, is_demo=is_demo)
    gap = compute_catchup_gap(client, job_id, job, is_demo=is_demo, now=now)

    would_run = enabled and not locked and deps_ok and not is_manual_only

    return {
        "job_id": job_id, "job_type": job.get("job_type"), "cli_command": job.get("cli_command"),
        "enabled": enabled, "disabled_reason": job.get("disabled_reason"),
        "would_run": would_run, "locked": locked,
        "dependencies_satisfied": deps_ok, "dependencies_reason": deps_reason,
        "catchup_gap": gap, "produces_phases": job.get("produces_phases", []),
        "dedicated": bool(job.get("dedicated")), "dry_run": True, "generated_at": _iso(now),
    }


def _resolve_theme_id(client, source_registry_name: str) -> str | None:
    rows = client.select("geo_source_registry", {
        "select": "theme_id", "name": f"eq.{source_registry_name}", "limit": "1"})
    return rows[0]["theme_id"] if rows else None


def _exec_gdelt_doc(client, job: dict, *, is_demo: bool, requested_by: str) -> dict:
    return {"query_results": geo_connector_gdelt.run_enabled_external_signal_queries(
        client, provider="gdelt", requested_by=requested_by, is_demo=is_demo)}


def _exec_gdelt_stream(client, job: dict, *, is_demo: bool, requested_by: str) -> dict:
    catchup_cfg = job.get("catchup") or {}
    return geo_connector_gdelt_stream.run_gdelt_stream_catchup(
        client, max_calls=catchup_cfg.get("max_calls_per_tick", 10),
        batches_per_call=catchup_cfg.get("batches_per_call", 100),
        requested_by=requested_by, is_demo=is_demo)


def _exec_ofac(client, job: dict, *, is_demo: bool, requested_by: str) -> dict:
    theme_id = _resolve_theme_id(client, job["source_registry_name"])
    if not theme_id:
        raise SchedulerError(
            "not_configured: OFAC Source RegistryにTheme IDがありません（初回は手動実行で登録してください）")
    return geo_connector_ofac.run_ofac_ingestion(client, theme_id=theme_id, requested_by=requested_by, is_demo=is_demo)


def missing_params(job: dict) -> list:
    """job.params（config/scheduler_jobs.yaml）のうち未設定（null）のキー。IMF/World Bankは
    対象国（theme_id/entity_id/country_code）を人間が決めるまでnot_configured。"""
    params = job.get("params") or {}
    return [k for k, v in params.items() if v in (None, "")]


def _exec_imf(client, job: dict, *, is_demo: bool, requested_by: str) -> dict:
    missing = missing_params(job)
    if missing:
        raise SchedulerError(
            f"not_configured: IMFの対象国が未設定です（{', '.join(missing)}）。"
            "docs/production-operations-runbook.mdの「IMF/World Bank対象国設定」を参照してください。")
    p = job["params"]
    return geo_connector_imf.run_imf_ingestion(
        client, theme_id=p["theme_id"], entity_id=p["entity_id"], country_code=p["country_code"],
        requested_by=requested_by, is_demo=is_demo)


def _exec_world_bank(client, job: dict, *, is_demo: bool, requested_by: str) -> dict:
    missing = missing_params(job)
    if missing:
        raise SchedulerError(
            f"not_configured: World Bankの対象国が未設定です（{', '.join(missing)}）。"
            "docs/production-operations-runbook.mdの「IMF/World Bank対象国設定」を参照してください。")
    p = job["params"]
    return geo_connector_world_bank.run_world_bank_ingestion(
        client, theme_id=p["theme_id"], entity_id=p["entity_id"], country_code=p["country_code"],
        requested_by=requested_by, is_demo=is_demo)


def _raise_if_direct_not_ready(results: dict) -> None:
    """Direct Collection（GEO_DIRECT_COLLECTION_V1）の結果を、Schedulerの結果分類へ寄せる。
    承認ゲート未通過（automation_allowed=false等）はnot_configured、取得の失敗は例外（再試行の判定対象）。"""
    states = {k: v.get("status") for k, v in results.items()}
    failed = {k: v.get("error") for k, v in results.items() if v.get("status") == "failed"}
    if failed:
        raise RuntimeError(f"Direct Collection失敗: {failed}")
    if states and all(v == "not_ready" for v in states.values()):
        raise SchedulerError("not_configured: 承認ゲート未通過（automation_allowed=false等）: "
                             + "; ".join(f"{k}: {v.get('reason')}" for k, v in results.items()))


def _exec_direct_rss(client, job: dict, *, is_demo: bool, requested_by: str) -> dict:
    results = geo_connector_rss.run_all_rss_sources(client, requested_by=requested_by, is_demo=is_demo)
    # 同じJobで、Source（Registry）のもう1つの取得口（公開HTML一覧。例: 統合幕僚監部）も取得する
    results.update({f"html:{k}": v for k, v in geo_connector_html_list.run_all_html_list_sources(
        client, requested_by=requested_by, is_demo=is_demo).items()})
    _raise_if_direct_not_ready(results)
    return {"sources": results}


def _exec_ukmto(client, job: dict, *, is_demo: bool, requested_by: str) -> dict:
    try:
        return geo_connector_ukmto.run_ukmto_collection(client, requested_by=requested_by, is_demo=is_demo, run_type="scheduled_external")
    except (base.ConnectorDisabledError, geo_direct_collection_common.SourceNotReadyError) as e:
        raise SchedulerError(f"not_configured: {e}") from e


def _exec_federal_register(client, job: dict, *, is_demo: bool, requested_by: str) -> dict:
    try:
        return geo_connector_federal_register.run_federal_register_collection(
            client, requested_by=requested_by, is_demo=is_demo, run_type="scheduled_external")
    except (base.ConnectorDisabledError, geo_direct_collection_common.SourceNotReadyError) as e:
        raise SchedulerError(f"not_configured: {e}") from e


def _exec_sanctions_xml(client, job: dict, *, is_demo: bool, requested_by: str) -> dict:
    out = {}
    for key in (geo_direct_collection_common.load_direct_config().get("sanctions_lists") or {}):
        try:
            out[key] = geo_connector_sanctions_xml.run_sanctions_collection(
                client, list_key=key, requested_by=requested_by, is_demo=is_demo, run_type="scheduled_external")
        except (base.ConnectorDisabledError, geo_direct_collection_common.SourceNotReadyError) as e:
            out[key] = {"status": "not_ready", "reason": str(e)}
        except Exception as e:  # noqa: BLE001 — 1リストの失敗で他を止めない
            out[key] = {"status": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
    _raise_if_direct_not_ready(out)
    return {"lists": out}


_EXECUTORS = {
    "COLLECTION_DIRECT_RSS": _exec_direct_rss,
    "COLLECTION_FEDERAL_REGISTER": _exec_federal_register,
    "COLLECTION_UKMTO": _exec_ukmto,
    "COLLECTION_SANCTIONS_XML": _exec_sanctions_xml,
    "COLLECTION_GDELT_DOC": _exec_gdelt_doc,
    "COLLECTION_GDELT_STREAM": _exec_gdelt_stream,
    "COLLECTION_OFAC": _exec_ofac,
    "COLLECTION_IMF": _exec_imf,
    "COLLECTION_WORLD_BANK": _exec_world_bank,
}


def run_job(client, job_id: str, *, registry: dict = None, is_demo: bool = False,
            requested_by: str = "batch", force: bool = False) -> dict:
    """item4/5/10/12: 1 Jobを実行する。Human Gate対象（manual_only_jobs）はここでは
    一切実行しない——job_idがjobs側に無ければ拒否する。

    force=Trueは手動Manual Trigger専用（item20）で、lock/依存関係チェックを明示的に
    バイパスできる——ただし危険な操作のため、呼び出し側API（geo_scheduler_api.py）で
    確認Dialogを必須にする。"""
    registry = registry or load_job_registry()
    job = registry.get("jobs", {}).get(job_id)
    if job is None:
        if job_id in registry.get("manual_only_jobs", {}):
            raise SchedulerError(
                f"{job_id}はHuman Gate後の半自動Jobです。Schedulerからは実行できません"
                "（Phase CのUIから人間が明示的に実行してください）。")
        raise SchedulerError(f"未定義のJobです: {job_id}")

    if job.get("enabled") is False:
        return {"job_id": job_id, "outcome": "skipped",
                "reason": job.get("disabled_reason", "disabled"), "retryable": False}

    if not force and is_job_locked(client, job, is_demo=is_demo):
        return {"job_id": job_id, "outcome": "skipped", "reason": "locked（既に実行中）", "retryable": True}

    if not force:
        deps_ok, deps_reason = check_dependencies_satisfied(client, job, registry=registry, is_demo=is_demo)
        if not deps_ok:
            return {"job_id": job_id, "outcome": "skipped", "reason": deps_reason, "retryable": True}

    executor = _EXECUTORS.get(job_id)
    if executor is None:
        return {"job_id": job_id, "outcome": "skipped", "reason": "実行方法未定義", "retryable": False}

    try:
        result = executor(client, job, is_demo=is_demo, requested_by=requested_by)
        return {"job_id": job_id, "outcome": "executed", "result": result}
    except geo_gdelt_rate_limit.GdeltBusyError as e:
        # 別のGDELT DOC収集（定期・catch-up・手動）が実行中。失敗ではなく「今は実行しない」
        return {"job_id": job_id, "outcome": "skipped", "reason": f"GDELT DOC収集が実行中のため見送り（{e}）",
                "retryable": True}
    except SchedulerError as e:
        return {"job_id": job_id, "outcome": "not_configured", "reason": str(e), "retryable": False}
    except Exception as e:  # noqa: BLE001 — Job実行失敗を安全に分類して返す（例外を外へ漏らさない）
        message = f"{type(e).__name__}: {e}"
        return {"job_id": job_id, "outcome": "error", "error": message,
                "retry_classification": classify_error(message)}


def get_job_status(client, job_id: str, *, registry: dict = None, is_demo: bool = False,
                    now: datetime | None = None) -> dict:
    """item4: job_id/job_type/status/started_at/finished_at/last_success_at/processed_count/
    error/retry_countを一通り追えるようにする。既存run logの集計のみ、新規stateは持たない。"""
    registry = registry or load_job_registry()
    job = _all_job_defs(registry).get(job_id)
    if job is None:
        raise SchedulerError(f"未定義のJobです: {job_id}")
    now = now or datetime.now(timezone.utc)
    stale_hours = job.get("stale_after_hours", 6)

    latest = None
    last_success = None
    recent_failures = 0
    for ct in _connector_types_for_job(job):
        run = _latest_run(client, ct, is_demo=is_demo)
        if run and (latest is None or (run.get("started_at") or "") > (latest.get("started_at") or "")):
            latest = run
        succ_rows = client.select("geo_ingestion_runs", {
            "select": "started_at,completed_at", "connector_type": f"eq.{ct}",
            "is_demo": f"eq.{str(is_demo).lower()}", "status": "in.(completed,completed_with_warnings)",
            "order": "started_at.desc", "limit": "1"})
        if succ_rows:
            ts = _parse(succ_rows[0].get("completed_at") or succ_rows[0].get("started_at"))
            if ts and (last_success is None or ts > last_success):
                last_success = ts
        fail_rows = client.select("geo_ingestion_runs", {
            "select": "ingestion_run_id", "connector_type": f"eq.{ct}",
            "is_demo": f"eq.{str(is_demo).lower()}", "status": "eq.failed",
            "started_at": f"gte.{(now - timedelta(days=1)).isoformat()}", "limit": "50"})
        recent_failures += len(fail_rows)

    status = "never_run"
    if latest:
        status = latest.get("status", "unknown")
        if status == "running" and is_stale(latest, stale_after_hours=stale_hours, now=now):
            status = "stale"

    return {
        "job_id": job_id, "job_type": job.get("job_type"),
        "status": status, "enabled": job.get("enabled", True) is not False,
        "disabled_reason": job.get("disabled_reason"),
        "started_at": latest.get("started_at") if latest else None,
        "finished_at": latest.get("completed_at") if latest else None,
        "last_success_at": _iso(last_success),
        "processed_count": (latest or {}).get("fetched_count"),
        "error": (latest or {}).get("error_summary") if status in ("failed", "stale") else None,
        "retry_classification": classify_error((latest or {}).get("error_summary")) if status == "failed" else None,
        "recent_failure_count_24h": recent_failures,
        "locked": is_job_locked(client, job, is_demo=is_demo, now=now) if job.get("job_type") == "collection" else False,
        "depends_on": job.get("depends_on", []),
        "cadence_hint": job.get("cadence_hint"),
    }


def get_all_job_statuses(client, *, registry: dict = None, is_demo: bool = False,
                          now: datetime | None = None) -> list:
    registry = registry or load_job_registry()
    now = now or datetime.now(timezone.utc)
    out = []
    for job_id in registry.get("jobs", {}):
        out.append(get_job_status(client, job_id, registry=registry, is_demo=is_demo, now=now))
    for job_id in registry.get("manual_only_jobs", {}):
        out.append(get_job_status(client, job_id, registry=registry, is_demo=is_demo, now=now))
    return out


def get_scheduler_health(client, *, registry: dict = None, is_demo: bool = False,
                          now: datetime | None = None) -> dict:
    """item19: Production Scheduler自体の状態。実際にOSレベルのScheduler登録は行って
    いない（Phase A/D確定事項）ため、configuredは常にFalse、statusは"not_configured"と
    正直に表示する（"正常"と誤認させない）。実際に将来cron/systemd timer登録後は、
    geo_ingestion_runs.run_type='scheduled_external'の実行履歴から自動検出できるように
    判定ロジック自体は用意しておく。"""
    now = now or datetime.now(timezone.utc)
    registry = registry or load_job_registry()
    active_days = 3
    sched_runs = client.select("geo_ingestion_runs", {
        "select": "started_at", "run_type": "eq.scheduled_external",
        "is_demo": f"eq.{str(is_demo).lower()}", "order": "started_at.desc", "limit": "1"})
    last_sched = _parse(sched_runs[0]["started_at"]) if sched_runs else None
    detected_active = bool(last_sched and (now - last_sched) <= timedelta(days=active_days))

    return {
        "configured": False,  # 本番OS Scheduler未登録（Phase A/D確定: 本番Linuxサーバ未構築）
        "status": "not_configured",
        "detected_recent_scheduled_runs": detected_active,
        "last_scheduled_run_at": _iso(last_sched),
        "note": (
            "本番Scheduler（cron/systemd timer）は未登録です。Job定義・Runbook・手動実行・"
            "Dry Runは完成していますが、OSレベルの定期実行はPRODUCTION DEPLOYMENT PENDINGです。"
            "本番Linuxサーバ構築後、docs/production-scheduling-runbook.mdの手順で登録してください。"
        ),
        "job_count": len(registry.get("jobs", {})),
        "enabled_job_count": sum(1 for j in registry.get("jobs", {}).values() if j.get("enabled", True) is not False),
    }
