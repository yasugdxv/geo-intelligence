"""地政学ナレッジ＆インテリジェンスDB Phase2A: GDELT 2.0 raw feed（Event/Mentions/GKG）Connector。

Phase1の`geo_connector_gdelt.py`（DOC API、記事検索）とは別のデータストリーム。
GDELT Event/GKGはValidated KnowledgeではなくExternal Observationであり、既存Knowledge/
Evidence関連テーブルには一切書き込まない（永続化はgeo_external_event_service.py経由）。

全世界のEvent/GKGを無条件保存しない。Phase1 News DB（geo_external_signals）に既に存在する
URLとMentions/GKG経由で一致したものだけをEnrichする（Batch Filtering）。

使い方: python geo_connector_cli.py gdelt-stream --since 20260907060000 --max-batches 4
"""
import io
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone

import requests

import geo_connector_base as base
import geo_external_signal_service as signal_svc

RAW_BASE_URL = "http://data.gdeltproject.org/gdeltv2"
LASTUPDATE_URL = f"{RAW_BASE_URL}/lastupdate.txt"
BATCH_INTERVAL_MINUTES = 15
CONNECTOR_VERSION = "geo-gdelt-stream-1.0"
REQUEST_TIMEOUT = 60
MAX_RETRIES = 5
DEFAULT_REQUEST_INTERVAL_SECONDS = 5.0
DEFAULT_LOOKBACK_HOURS = 24
DEFAULT_BATCH_MATURITY_HOURS = 6
# GDELTがこの時間分は確実にbatchを公開し終えているとみなす安全マージン。この時間より新しい
# batchで404が出ても「まだ公開されていないだけ」の可能性があるため恒久欠番とは判定しない。
# config.jsonのgeo_external_signals.gdelt_stream.batch_maturity_hoursで上書きできる。
CATCHUP_SUMMARY_CONNECTOR_TYPE = "gdelt_stream_catchup"
CHECKPOINT_SCOPE_INCREMENTAL = "incremental"
CHECKPOINT_SCOPE_ADHOC = "adhoc"
DEFAULT_CATCHUP_BATCHES_PER_CALL = 100
DEFAULT_CATCHUP_MAX_CALLS = 50

_last_request_at = 0.0

# --- Event(export.CSV) 61列、0-indexed。実データ2026-09-07で検証済み（Codebook準拠） ---
EVENT_COL_GLOBALEVENTID = 0
EVENT_COL_DAY = 1               # イベントが実際に起きた日(SQLDATE)。列59 DATEADDEDとは別物、混同禁止
EVENT_COL_ACTOR1NAME = 6
EVENT_COL_ACTOR1COUNTRYCODE = 7
EVENT_COL_ACTOR2NAME = 16
EVENT_COL_ACTOR2COUNTRYCODE = 17
EVENT_COL_EVENTCODE = 26
EVENT_COL_EVENTBASECODE = 27
EVENT_COL_EVENTROOTCODE = 28
EVENT_COL_QUADCLASS = 29
EVENT_COL_GOLDSTEINSCALE = 30
EVENT_COL_NUMMENTIONS = 31
EVENT_COL_NUMSOURCES = 32
EVENT_COL_NUMARTICLES = 33
EVENT_COL_AVGTONE = 34
EVENT_COL_ACTIONGEO_FULLNAME = 52
EVENT_COL_ACTIONGEO_COUNTRYCODE = 53
EVENT_COL_ACTIONGEO_LAT = 56
EVENT_COL_ACTIONGEO_LONG = 57
EVENT_COL_DATEADDED = 59
EVENT_COL_SOURCEURL = 60
EVENT_MIN_COLUMNS = 61

# --- Mentions 16列 ---
MENTION_COL_GLOBALEVENTID = 0
MENTION_COL_EVENTTIMEDATE = 1   # このGlobalEventIDの生成元となったGDELT Event batchのtimestamp
                                 # （GDELTがこのEventを最初にEvent streamへ記録したbatch）。
                                 # 実世界でEventが発生した時刻ではない（実世界の発生日はEvent列1
                                 # Day/SQLDATE由来のevent_dateを使う）。MentionTimeDate(列2)とも別物
MENTION_COL_MENTIONTIMEDATE = 2
MENTION_COL_MENTIONTYPE = 3     # 1=WEB。この値の時だけMentionIdentifierがURL
MENTION_COL_MENTIONIDENTIFIER = 5
MENTION_COL_CONFIDENCE = 11
MENTION_COL_MENTIONDOCTONE = 13
MENTION_TYPE_WEB = "1"
MENTION_MIN_COLUMNS = 16

# --- GKG 27列 ---
GKG_COL_GKGRECORDID = 0
GKG_COL_V21DATE = 1
GKG_COL_V2SOURCECOLLECTIONIDENTIFIER = 2  # 1=WEB。この値の時だけV2DocumentIdentifierがURL
GKG_COL_V2SOURCECOMMONNAME = 3
GKG_COL_V2DOCUMENTIDENTIFIER = 4
GKG_SOURCE_COLLECTION_WEB = "1"
GKG_COL_V1THEMES = 7
GKG_COL_V2ENHANCEDLOCATIONS = 10
GKG_COL_V2ENHANCEDPERSONS = 12
GKG_COL_V2ENHANCEDORGANIZATIONS = 14
GKG_COL_V15TONE = 15
GKG_MIN_COLUMNS = 27


class GdeltStreamDisabledError(RuntimeError):
    pass


def _sleep_for_interval(interval_seconds: float) -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < interval_seconds:
        time.sleep(interval_seconds - elapsed)


def _download_with_retry(file_url: str, *, proxies=None, verify=True,
                          request_interval_seconds=DEFAULT_REQUEST_INTERVAL_SECONDS) -> bytes:
    """Phase1の`geo_connector_gdelt._request`と同型のexponential backoff（429/5xx、
    MAX_RETRIES=5）。同じロジックの複製であり第三のHTTPクライアントは作らない。
    404は「このbatchにファイルが存在しない」ことを意味し、リトライしても無意味なため
    即座にFileNotFoundErrorを送出する（呼び出し元がterminal扱いできるように区別する）。"""
    global _last_request_at
    last_error = None
    for attempt in range(MAX_RETRIES):
        _sleep_for_interval(request_interval_seconds)
        try:
            resp = requests.get(file_url, proxies=proxies or {}, verify=verify, timeout=REQUEST_TIMEOUT)
            _last_request_at = time.monotonic()
        except requests.RequestException as e:
            last_error = e
            time.sleep(request_interval_seconds * (2 ** attempt))
            continue
        if resp.status_code == 200:
            return resp.content
        if resp.status_code == 404:
            raise FileNotFoundError(f"GDELTファイルが存在しません: {file_url}")
        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = RuntimeError(f"GDELT stream status={resp.status_code}: {resp.text[:200]!r}")
            time.sleep(request_interval_seconds * (2 ** attempt))
            continue
        resp.raise_for_status()
    raise RuntimeError(f"GDELT stream file取得に失敗（{MAX_RETRIES}回リトライ後）: {file_url}: {last_error}")


def _unzip_single_member(zip_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return zf.read(zf.namelist()[0])


def build_batch_urls(batch_timestamp: str) -> dict:
    return {
        "event": f"{RAW_BASE_URL}/{batch_timestamp}.export.CSV.zip",
        "mentions": f"{RAW_BASE_URL}/{batch_timestamp}.mentions.CSV.zip",
        "gkg": f"{RAW_BASE_URL}/{batch_timestamp}.gkg.csv.zip",
    }


def download_batch_file(kind: str, batch_timestamp: str, *, proxies=None, verify=True,
                         request_interval_seconds=DEFAULT_REQUEST_INTERVAL_SECONDS) -> bytes:
    """kindは'event'/'mentions'/'gkg'。zip解凍済みの生CSVバイト列を返す。404はFileNotFoundError。"""
    urls = build_batch_urls(batch_timestamp)
    zip_bytes = _download_with_retry(urls[kind], proxies=proxies, verify=verify,
                                      request_interval_seconds=request_interval_seconds)
    return _unzip_single_member(zip_bytes)


def get_latest_batch_manifest(*, proxies=None, verify=True,
                               request_interval_seconds=DEFAULT_REQUEST_INTERVAL_SECONDS) -> dict:
    """lastupdate.txtを取得し、最新のevent/mentions/gkg URLとbatch_timestampを返す。"""
    content = _download_with_retry(LASTUPDATE_URL, proxies=proxies, verify=verify,
                                    request_interval_seconds=request_interval_seconds)
    text = content.decode("utf-8", errors="replace")
    urls = {}
    for line in text.strip().splitlines():
        parts = line.strip().split(" ")
        if len(parts) < 3:
            continue
        url = parts[-1]
        if url.endswith(".export.CSV.zip"):
            urls["event"] = url
        elif url.endswith(".mentions.CSV.zip"):
            urls["mentions"] = url
        elif url.endswith(".gkg.csv.zip"):
            urls["gkg"] = url
    match = re.search(r"/(\d{14})\.", urls.get("mentions", "") or urls.get("event", ""))
    return {"batch_timestamp": match.group(1) if match else None, **urls}


# ---------------------------------------------------------------------------
# Parsers（1行ずつtry/exceptで囲み失敗行はスキップ）
# ---------------------------------------------------------------------------

def _decode_lines(raw_bytes: bytes) -> list:
    text = raw_bytes.decode("utf-8", errors="replace")
    return [line for line in text.split("\n") if line.strip()]


def _parse_event_day(value: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _to_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _to_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def parse_event_rows(raw_bytes: bytes) -> tuple:
    parsed = []
    skipped = 0
    for line in _decode_lines(raw_bytes):
        cols = line.split("\t")
        if len(cols) < EVENT_MIN_COLUMNS:
            skipped += 1
            continue
        try:
            parsed.append({
                "provider_event_id": cols[EVENT_COL_GLOBALEVENTID],
                "event_date": _parse_event_day(cols[EVENT_COL_DAY]),
                "actor1_name": cols[EVENT_COL_ACTOR1NAME] or None,
                "actor1_country_code": cols[EVENT_COL_ACTOR1COUNTRYCODE] or None,
                "actor2_name": cols[EVENT_COL_ACTOR2NAME] or None,
                "actor2_country_code": cols[EVENT_COL_ACTOR2COUNTRYCODE] or None,
                "event_code": cols[EVENT_COL_EVENTCODE] or None,
                "event_base_code": cols[EVENT_COL_EVENTBASECODE] or None,
                "event_root_code": cols[EVENT_COL_EVENTROOTCODE] or None,
                "quad_class": _to_int(cols[EVENT_COL_QUADCLASS]),
                "goldstein_scale": _to_float(cols[EVENT_COL_GOLDSTEINSCALE]),
                "num_mentions": _to_int(cols[EVENT_COL_NUMMENTIONS]),
                "num_sources": _to_int(cols[EVENT_COL_NUMSOURCES]),
                "num_articles": _to_int(cols[EVENT_COL_NUMARTICLES]),
                "avg_tone": _to_float(cols[EVENT_COL_AVGTONE]),
                "action_geo_full_name": cols[EVENT_COL_ACTIONGEO_FULLNAME] or None,
                "action_geo_country_code": cols[EVENT_COL_ACTIONGEO_COUNTRYCODE] or None,
                "action_geo_lat": _to_float(cols[EVENT_COL_ACTIONGEO_LAT]),
                "action_geo_long": _to_float(cols[EVENT_COL_ACTIONGEO_LONG]),
                "event_date_added": signal_svc.parse_gdelt_timeline_date(cols[EVENT_COL_DATEADDED]),
                "source_url": cols[EVENT_COL_SOURCEURL] or None,
                "raw_metadata": {"columns": cols},
            })
        except Exception:
            skipped += 1
    return parsed, skipped


def parse_mention_rows(raw_bytes: bytes) -> tuple:
    parsed = []
    skipped = 0
    for line in _decode_lines(raw_bytes):
        cols = line.split("\t")
        if len(cols) < MENTION_MIN_COLUMNS:
            skipped += 1
            continue
        try:
            mention_type = cols[MENTION_COL_MENTIONTYPE]
            parsed.append({
                "provider_event_id": cols[MENTION_COL_GLOBALEVENTID],
                "event_time_date": cols[MENTION_COL_EVENTTIMEDATE],
                "mention_time_date": signal_svc.parse_gdelt_timeline_date(cols[MENTION_COL_MENTIONTIMEDATE]),
                "is_web": mention_type == MENTION_TYPE_WEB,
                "mention_identifier": cols[MENTION_COL_MENTIONIDENTIFIER] or None,
                "confidence": _to_int(cols[MENTION_COL_CONFIDENCE]),
                "mention_doc_tone": _to_float(cols[MENTION_COL_MENTIONDOCTONE]),
            })
        except Exception:
            skipped += 1
    return parsed, skipped


def _parse_gkg_persons_or_orgs(raw: str) -> list:
    if not raw:
        return []
    items = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if "," in part:
            name, _, offset = part.rpartition(",")
            items.append({"name": name.strip(), "char_offset": offset.strip()})
        else:
            items.append({"name": part, "char_offset": None})
    return items


def _parse_gkg_locations(raw: str) -> list:
    if not raw:
        return []
    items = []
    for part in raw.split(";"):
        fields = part.split("#")
        if len(fields) < 3:
            continue
        items.append({
            "full_name": fields[1] if len(fields) > 1 else None,
            "country_code": fields[2] if len(fields) > 2 else None,
            "lat": _to_float(fields[5]) if len(fields) > 5 else None,
            "long": _to_float(fields[6]) if len(fields) > 6 else None,
        })
    return items


_GKG_TONE_KEYS = ("tone", "tone_positive", "tone_negative", "tone_polarity",
                  "tone_activity_density", "tone_self_group_density", "tone_word_count")


def _parse_gkg_tone(raw: str) -> dict:
    if not raw:
        return {}
    parts = raw.split(",")
    result = {}
    for key, value in zip(_GKG_TONE_KEYS, parts):
        result[key] = _to_int(value) if key == "tone_word_count" else _to_float(value)
    return result


def parse_gkg_rows(raw_bytes: bytes) -> tuple:
    parsed = []
    skipped = 0
    for line in _decode_lines(raw_bytes):
        cols = line.split("\t")
        if len(cols) < GKG_MIN_COLUMNS:
            skipped += 1
            continue
        try:
            row = {
                "provider_record_id": cols[GKG_COL_GKGRECORDID],
                "gkg_date": signal_svc.parse_gdelt_timeline_date(cols[GKG_COL_V21DATE]),
                "is_web": cols[GKG_COL_V2SOURCECOLLECTIONIDENTIFIER] == GKG_SOURCE_COLLECTION_WEB,
                "source_common_name": cols[GKG_COL_V2SOURCECOMMONNAME] or None,
                "document_identifier": cols[GKG_COL_V2DOCUMENTIDENTIFIER] or None,
                "themes": [t for t in cols[GKG_COL_V1THEMES].split(";") if t],
                "locations": _parse_gkg_locations(cols[GKG_COL_V2ENHANCEDLOCATIONS]),
                "persons": _parse_gkg_persons_or_orgs(cols[GKG_COL_V2ENHANCEDPERSONS]),
                "organizations": _parse_gkg_persons_or_orgs(cols[GKG_COL_V2ENHANCEDORGANIZATIONS]),
                "raw_metadata": {"columns": cols},
            }
            row.update(_parse_gkg_tone(cols[GKG_COL_V15TONE]))
            parsed.append(row)
        except Exception:
            skipped += 1
    return parsed, skipped


# ---------------------------------------------------------------------------
# Checkpoint（差分方式）
# ---------------------------------------------------------------------------

def list_expected_batch_timestamps(since: str, until: str) -> list:
    """since〜untilの範囲で15分刻みのbatch_timestamp一覧を生成する（古い順）。"""
    start = datetime.strptime(since, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    out = []
    t = start
    while t <= end:
        out.append(t.strftime("%Y%m%d%H%M%S"))
        t += timedelta(minutes=BATCH_INTERVAL_MINUTES)
    return out


def list_completed_batch_timestamps(client, *, since: str, until: str, is_demo: bool = False) -> set:
    """connector_type='gdelt_stream'かつ指定is_demoスコープの全run（_select_all_paginated）を
    batch_timestamp単位でgroup化し、1件でも status='completed' または
    (status='completed_with_warnings' かつ retryable_pending_count==0) を満たす行があれば
    そのbatch_timestampを完了集合へ入れる。同一batch_timestampに古いfailed/retryable行が
    残っていても、後続の成功runがあれば再実行対象へ戻さない。retryable_pending_countが
    読み取れない場合は安全側で未完了扱い。is_demoでフィルタしないと、production向けの
    missing batch計算がdemo実行で完了したbatchを「完了済み」として誤って除外してしまう
    （逆方向も同様）。"""
    rows = signal_svc._select_all_paginated(client, "geo_ingestion_runs", {
        "select": "status,request_parameters", "connector_type": "eq.gdelt_stream",
        "is_demo": "eq.true" if is_demo else "eq.false",
    })
    by_batch = {}
    for r in rows:
        rp = r.get("request_parameters") or {}
        bt = rp.get("batch_timestamp")
        if not bt or bt < since or bt > until:
            continue
        by_batch.setdefault(bt, []).append(r)

    completed = set()
    for bt, group in by_batch.items():
        for r in group:
            if r.get("status") == "completed":
                completed.add(bt)
                break
            if r.get("status") == "completed_with_warnings":
                rp = r.get("request_parameters") or {}
                if rp.get("retryable_pending_count") == 0:
                    completed.add(bt)
                    break
    return completed


def _is_terminal_run(run_row: dict) -> bool:
    rp = run_row.get("request_parameters") or {}
    return (run_row.get("status") == "completed"
            or (run_row.get("status") == "completed_with_warnings" and rp.get("retryable_pending_count") == 0)
            or rp.get("batch_gap") is True)


def _align_to_batch_grid(ts: str) -> str:
    """任意の時刻文字列を15分batch gridへ切り下げる。"""
    dt = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    minute = (dt.minute // BATCH_INTERVAL_MINUTES) * BATCH_INTERVAL_MINUTES
    return dt.replace(minute=minute, second=0, microsecond=0).strftime("%Y%m%d%H%M%S")


def _fetch_terminal_status_map(client, *, since_ts: str, until_ts: str, is_demo: bool) -> dict:
    """指定範囲・指定is_demoスコープのgdelt_stream個別batch runを取得し
    {batch_timestamp: is_terminal}を返す。is_demoでフィルタしないとdemo実行の完了状態が
    productionのwatermark計算に混入する（逆方向も同様）。"""
    rows = signal_svc._select_all_paginated(client, "geo_ingestion_runs", {
        "select": "status,request_parameters", "connector_type": "eq.gdelt_stream",
        "is_demo": "eq.true" if is_demo else "eq.false",
    })
    by_batch = {}
    for r in rows:
        bt = (r.get("request_parameters") or {}).get("batch_timestamp")
        if not bt or bt < since_ts or bt > until_ts:
            continue
        by_batch.setdefault(bt, []).append(r)
    return {bt: any(_is_terminal_run(r) for r in group) for bt, group in by_batch.items()}


def _find_last_catchup_watermark(client, *, is_demo: bool) -> str | None:
    """is_demoスコープかつcheckpoint_scope='incremental'のsummary runのみを対象にする。
    checkpoint_scope='adhoc'（明示since指定のHistorical/Ad-hoc catch-up）のsummaryは
    定常checkpointに影響してはならない。"""
    rows = signal_svc._select_all_paginated(client, "geo_ingestion_runs", {
        "select": "started_at,request_parameters", "connector_type": f"eq.{CATCHUP_SUMMARY_CONNECTOR_TYPE}",
        "is_demo": "eq.true" if is_demo else "eq.false",
    })
    latest = None
    for r in rows:
        rp = r.get("request_parameters") or {}
        if rp.get("checkpoint_scope") != CHECKPOINT_SCOPE_INCREMENTAL:
            continue
        wm = rp.get("completed_through")
        if wm and (latest is None or r["started_at"] > latest[0]):
            latest = (r["started_at"], wm)
    return latest[1] if latest else None


def _bootstrap_baseline(fallback_hours: int, *, now: datetime) -> str:
    """prior watermarkが無い場合のbaseline（=first_expected_batchの1つ前）を返す。
    `now - fallback_hours`を15分gridへ正規化した`first_expected_batch`の1 batch前を
    baselineとすることで、その最初のbatchがまだterminalでない場合にwatermarkがそれを
    飛び越えることがない。"""
    fallback_ts = (now - timedelta(hours=fallback_hours)).strftime("%Y%m%d%H%M%S")
    first_expected_batch = _align_to_batch_grid(fallback_ts)
    return (datetime.strptime(first_expected_batch, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            - timedelta(minutes=BATCH_INTERVAL_MINUTES)).strftime("%Y%m%d%H%M%S")


def _baseline_before(ts: str) -> str:
    """任意の時刻を15分gridへ正規化し、その1 batch前を返す。Ad-hoc/Historical catch-upの
    explicit sinceからbaselineを作るのに使う。"""
    aligned = _align_to_batch_grid(ts)
    return (datetime.strptime(aligned, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            - timedelta(minutes=BATCH_INTERVAL_MINUTES)).strftime("%Y%m%d%H%M%S")


def _resolve_configured_maturity_hours() -> int:
    try:
        from config_utils import load_config
        config = load_config()
        stream_cfg = (config.get("geo_external_signals", {}) or {}).get("gdelt_stream", {}) or {}
        return stream_cfg.get("batch_maturity_hours", DEFAULT_BATCH_MATURITY_HOURS)
    except Exception:
        return DEFAULT_BATCH_MATURITY_HOURS


def _resolve_effective_until(until: str, *, batch_maturity_hours: int, bypass_maturity_clamp: bool,
                              now: datetime) -> str:
    """list_missing_batch_timestampsとrun_gdelt_stream_catchupのcompleted_through計算が
    同じmaturity clampロジックを共有するための共通ヘルパー（重複実装によるズレを防ぐ）。"""
    if bypass_maturity_clamp:
        return until or now.strftime("%Y%m%d%H%M%S")
    maturity_cutoff = (now - timedelta(hours=batch_maturity_hours)).strftime("%Y%m%d%H%M%S")
    requested_until = until or now.strftime("%Y%m%d%H%M%S")
    return min(requested_until, maturity_cutoff)


def compute_completed_through(client, *, baseline: str, horizon: str, is_demo: bool) -> str:
    """baseline（=検証対象外の仮の起点）の1 batch後からhorizonまで、連続してterminalな
    batchが続く限りwatermarkを前進させる。途中に1件でも非terminal（missing/retryable）な
    batchがあれば、その手前で止める。baseline/horizonは呼び出し側（定常Incrementalなら
    resolve_incremental_since、Ad-hocならrun_gdelt_stream_catchup）が自分の文脈に応じて
    計算してから渡す——本関数自身はfallback_hoursやsinceの意味を知らない。"""
    terminal_map = _fetch_terminal_status_map(client, since_ts=baseline, until_ts=horizon, is_demo=is_demo)

    watermark = baseline
    cursor = datetime.strptime(baseline, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    end = datetime.strptime(horizon, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    cursor += timedelta(minutes=BATCH_INTERVAL_MINUTES)
    while cursor <= end:
        bt = cursor.strftime("%Y%m%d%H%M%S")
        if not terminal_map.get(bt):
            break
        watermark = bt
        cursor += timedelta(minutes=BATCH_INTERVAL_MINUTES)
    return watermark


def resolve_incremental_since(client, *, fallback_hours: int = DEFAULT_LOOKBACK_HOURS,
                               is_demo: bool = False) -> str:
    """定常Incremental運用で`since`が指定されなかった場合の開始点を決定する。is_demoスコープに
    閉じた直近のgdelt_stream_catchup incremental summary runのcompleted_throughウォーターマーク
    を起点に、連続terminal区間を再計算してから次の15分batchを返す。長時間（72時間等）停止して
    いても未取得期間を失わない。"""
    now = datetime.now(timezone.utc)
    prior_watermark = _find_last_catchup_watermark(client, is_demo=is_demo)
    baseline = (_align_to_batch_grid(prior_watermark) if prior_watermark
                else _bootstrap_baseline(fallback_hours, now=now))
    horizon = now.strftime("%Y%m%d%H%M%S")
    watermark = compute_completed_through(client, baseline=baseline, horizon=horizon, is_demo=is_demo)
    dt = datetime.strptime(watermark, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return (dt + timedelta(minutes=BATCH_INTERVAL_MINUTES)).strftime("%Y%m%d%H%M%S")


def list_missing_batch_timestamps(client, *, since=None, until=None, batch_maturity_hours: int = None,
                                   bypass_maturity_clamp: bool = False, is_demo: bool = False) -> list:
    now = datetime.now(timezone.utc)
    effective_maturity_hours = (batch_maturity_hours if batch_maturity_hours is not None
                                 else _resolve_configured_maturity_hours())
    # 明示的にuntilが指定されていても、通常運用ではmaturity cutoffでclampする。
    # YYYYMMDDHHMMSSは固定長ゼロ埋め数字文字列なので文字列比較がそのまま時系列比較になる。
    until_ts = _resolve_effective_until(until, batch_maturity_hours=effective_maturity_hours,
                                         bypass_maturity_clamp=bypass_maturity_clamp, now=now)
    since_ts = since or resolve_incremental_since(client, fallback_hours=DEFAULT_LOOKBACK_HOURS, is_demo=is_demo)
    expected = list_expected_batch_timestamps(since_ts, until_ts)
    completed = list_completed_batch_timestamps(client, since=since_ts, until=until_ts, is_demo=is_demo)
    return [bt for bt in expected if bt not in completed]


# ---------------------------------------------------------------------------
# Event Resolution（別batch解決、run全体で共有されるcache/budget）
# ---------------------------------------------------------------------------

def group_event_ids_by_event_time(matched_mentions: list) -> dict:
    """{event_time_date: {provider_event_id, ...}} を返す。"""
    grouped = {}
    for m in matched_mentions:
        etd = m.get("event_time_date")
        pid = m.get("provider_event_id")
        if not etd or not pid:
            continue
        grouped.setdefault(etd, set()).add(pid)
    return grouped


def resolve_events_by_event_time(client, *, event_time_date_to_ids: dict, mention_batch_timestamp: str,
                                  proxies, verify, request_interval_seconds,
                                  event_resolution_lookback_days: int,
                                  event_batch_cache: dict, budget_remaining: int) -> dict:
    """event_time_date_to_idsの各EventTimeDateについて、mention_batch_timestamp（wall-clock
    nowではない）基準のlookbackとrun全体で共有されるevent_batch_cache/budget_remainingを使い、
    該当GlobalEventIDのEvent行を解決する。戻り値のevent_row_by_idの各rowには"_event_batch_
    timestamp"キーでそのEventの実際の取得元batch(=EventTimeDate)を埋め込む。"""
    mention_dt = datetime.strptime(mention_batch_timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    lookback_cutoff = mention_dt - timedelta(days=event_resolution_lookback_days)

    event_row_by_id = {}
    unresolved_retryable = []
    unresolved_terminal = []
    budget_consumed = 0

    for event_time_date in sorted(event_time_date_to_ids.keys()):
        ids_needed = event_time_date_to_ids[event_time_date]
        try:
            etd_dt = datetime.strptime(event_time_date, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            unresolved_terminal.extend(ids_needed)
            continue

        if etd_dt < lookback_cutoff:
            unresolved_terminal.extend(ids_needed)
            continue

        if event_time_date not in event_batch_cache:
            if budget_remaining - budget_consumed <= 0:
                unresolved_retryable.extend(ids_needed)
                continue
            try:
                raw_bytes = download_batch_file(
                    "event", event_time_date, proxies=proxies, verify=verify,
                    request_interval_seconds=request_interval_seconds)
                rows, _skipped = parse_event_rows(raw_bytes)
                event_batch_cache[event_time_date] = {r["provider_event_id"]: r for r in rows}
                budget_consumed += 1
            except FileNotFoundError:
                event_batch_cache[event_time_date] = {}
                budget_consumed += 1
            except Exception:
                event_batch_cache[event_time_date] = None
                budget_consumed += 1
                unresolved_retryable.extend(ids_needed)
                continue

        cached = event_batch_cache[event_time_date]
        if cached is None:
            unresolved_retryable.extend(ids_needed)
            continue
        for eid in ids_needed:
            if eid in cached:
                row = dict(cached[eid])
                row["_event_batch_timestamp"] = event_time_date
                event_row_by_id[eid] = row
            else:
                unresolved_terminal.append(eid)

    return {
        "event_row_by_id": event_row_by_id,
        "resolved_count": len(event_row_by_id),
        "unresolved_event_ids_retryable": unresolved_retryable,
        "unresolved_event_ids_terminal": unresolved_terminal,
        "budget_consumed": budget_consumed,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _load_runtime_config():
    try:
        from config_utils import load_config, make_proxies
        config = load_config()
        proxies = make_proxies(config)
        verify = config.get("ssl", {}).get("verify", True)
    except Exception:
        config, proxies, verify = {}, {}, True
    stream_cfg = {}
    try:
        stream_cfg = (config.get("geo_external_signals", {}) or {}).get("gdelt_stream", {}) or {}
    except NameError:
        pass
    enabled = stream_cfg.get("enabled", True)
    request_interval_seconds = stream_cfg.get("request_interval_seconds", DEFAULT_REQUEST_INTERVAL_SECONDS)
    event_resolution_lookback_days = stream_cfg.get("event_resolution_lookback_days", 7)
    max_event_resolution_batches_per_run = stream_cfg.get("max_event_resolution_batches_per_run", 20)
    return proxies, verify, enabled, request_interval_seconds, event_resolution_lookback_days, max_event_resolution_batches_per_run


def _is_batch_mature(batch_timestamp: str, *, batch_maturity_hours: int = DEFAULT_BATCH_MATURITY_HOURS,
                      now: datetime = None) -> bool:
    now = now or datetime.now(timezone.utc)
    bt = datetime.strptime(batch_timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return (now - bt) >= timedelta(hours=batch_maturity_hours)


def run_gdelt_structured_ingestion(client, *, since: str = None, until: str = None, max_batches: int = 1,
                                    event_resolution_lookback_days: int = None,
                                    max_event_resolution_batches_per_run: int = None,
                                    batch_maturity_hours: int = None, bypass_maturity_clamp: bool = False,
                                    requested_by: str = "system", is_demo: bool = False) -> dict:
    """`list_missing_batch_timestamps`が返すbatch_timestampを古い順に処理する。Event Resolution
    のcache/budgetはこの関数の1呼び出し全体（複数Mention batchをまたぐ）で共有する。"""
    import geo_external_event_service as event_svc
    import geo_actor_episode_service as actor_svc
    import geo_media_episode_service as media_svc
    import geo_episode_cluster_service as cluster_svc
    import geo_situation_link_service as link_svc

    (proxies, verify, enabled, request_interval_seconds,
     default_lookback_days, default_max_event_batches) = _load_runtime_config()
    if not enabled:
        raise GdeltStreamDisabledError(
            "config.jsonのgeo_external_signals.gdelt_stream.enabled=false のため無効です")
    if event_resolution_lookback_days is None:
        event_resolution_lookback_days = default_lookback_days
    if max_event_resolution_batches_per_run is None:
        max_event_resolution_batches_per_run = default_max_event_batches
    if batch_maturity_hours is None:
        batch_maturity_hours = _resolve_configured_maturity_hours()

    batch_timestamps = list_missing_batch_timestamps(
        client, since=since, until=until, batch_maturity_hours=batch_maturity_hours,
        bypass_maturity_clamp=bypass_maturity_clamp, is_demo=is_demo)[:max_batches]

    event_batch_cache = {}
    budget_remaining = max_event_resolution_batches_per_run
    run_type = "cli" if requested_by == "cli" else ("scheduled_external" if requested_by == "batch" else "manual")

    batch_results = []
    for batch_timestamp in batch_timestamps:
        run = base.create_ingestion_run(
            client, source_id=None, connector_type="gdelt_stream", run_type=run_type,
            requested_by=requested_by, request_parameters={"batch_timestamp": batch_timestamp},
            is_demo=is_demo,
        )
        counts = {"fetched_count": 0, "inserted_count": 0, "updated_count": 0, "error_count": 0}
        errors = []

        mention_rows, gkg_rows = [], []
        mentions_not_found = gkg_not_found = False
        try:
            mentions_raw = download_batch_file("mentions", batch_timestamp, proxies=proxies, verify=verify,
                                                request_interval_seconds=request_interval_seconds)
            mention_rows, _ = parse_mention_rows(mentions_raw)
        except FileNotFoundError:
            mentions_not_found = True
        except Exception as e:
            counts["error_count"] += 1
            errors.append(f"mentions download: {type(e).__name__}: {e}")
        try:
            gkg_raw = download_batch_file("gkg", batch_timestamp, proxies=proxies, verify=verify,
                                           request_interval_seconds=request_interval_seconds)
            gkg_rows, _ = parse_gkg_rows(gkg_raw)
        except FileNotFoundError:
            gkg_not_found = True
        except Exception as e:
            counts["error_count"] += 1
            errors.append(f"gkg download: {type(e).__name__}: {e}")

        both_404 = mentions_not_found and gkg_not_found
        any_404 = mentions_not_found or gkg_not_found
        is_permanent_gap = both_404 and _is_batch_mature(batch_timestamp, batch_maturity_hours=batch_maturity_hours)
        # 十分古い(maturity cutoff超)batchで両方404の場合のみ恒久欠番と判定する。まだmatureで
        # ないbatchの両方404（GDELTがまだ公開していないだけの可能性）、および片方だけの404
        # （一過性の不整合の可能性）は、共にretryableのままにする——ここでcompletedへ倒すと
        # 本当は取得できるはずのデータを取りこぼしたまま「完了」扱いにしてしまう。
        fetch_incomplete = any_404 and not is_permanent_gap
        if fetch_incomplete:
            errors.append(
                f"fetch incomplete (retryable): mentions_404={mentions_not_found} gkg_404={gkg_not_found}")

        web_mentions = [m for m in mention_rows if m["is_web"]]
        web_gkg = [g for g in gkg_rows if g["is_web"]]
        skipped_non_web = (len(mention_rows) - len(web_mentions)) + (len(gkg_rows) - len(web_gkg))

        counts["fetched_count"] = len(mention_rows) + len(gkg_rows)

        mention_urls = [m["mention_identifier"] for m in web_mentions if m.get("mention_identifier")]
        gkg_urls = [g["document_identifier"] for g in web_gkg if g.get("document_identifier")]
        all_urls = list({u for u in (mention_urls + gkg_urls) if u})
        url_matches = event_svc.match_signals_for_urls(client, all_urls) if all_urls else {}

        matched_mentions = [m for m in web_mentions if url_matches.get(m.get("mention_identifier"))]
        matched_gkg = [g for g in web_gkg if url_matches.get(g.get("document_identifier"))]

        event_time_date_to_ids = group_event_ids_by_event_time(matched_mentions)
        resolution = resolve_events_by_event_time(
            client, event_time_date_to_ids=event_time_date_to_ids, mention_batch_timestamp=batch_timestamp,
            proxies=proxies, verify=verify, request_interval_seconds=request_interval_seconds,
            event_resolution_lookback_days=event_resolution_lookback_days,
            event_batch_cache=event_batch_cache, budget_remaining=budget_remaining,
        )
        budget_remaining -= resolution["budget_consumed"]

        events_to_insert = [
            event_svc.normalize_event_row(row, event_batch_timestamp=row["_event_batch_timestamp"],
                                           ingestion_run_id=run["ingestion_run_id"])
            for row in resolution["event_row_by_id"].values()
        ]
        event_result = event_svc.bulk_upsert_events(client, events_to_insert)
        counts["inserted_count"] += event_result["inserted"]
        counts["updated_count"] += event_result["updated"]

        signal_ids_by_event_id = {}
        links_to_insert = []
        for m in matched_mentions:
            eid = m["provider_event_id"]
            internal_event_id = event_result["event_id_by_provider_event_id"].get(eid)
            if not internal_event_id:
                continue
            signal_match = url_matches.get(m["mention_identifier"])
            if not signal_match:
                continue
            signal_ids_by_event_id.setdefault(internal_event_id, set()).add(signal_match["external_signal_id"])
            links_to_insert.append(event_svc.normalize_link(
                m, external_event_id=internal_event_id,
                external_signal_id=signal_match["external_signal_id"],
                link_method=signal_match["link_method"],
            ))
        link_result = event_svc.bulk_upsert_event_signal_links(client, links_to_insert)
        counts["inserted_count"] += link_result.get("inserted", 0)

        annotations_to_insert = []
        signal_ids_for_annotations = set()
        for g in matched_gkg:
            signal_match = url_matches.get(g["document_identifier"])
            if not signal_match:
                continue
            signal_ids_for_annotations.add(signal_match["external_signal_id"])
            annotations_to_insert.append(event_svc.normalize_annotation(
                g, external_signal_id=signal_match["external_signal_id"],
                batch_timestamp=batch_timestamp, ingestion_run_id=run["ingestion_run_id"],
            ))
        annotation_result = event_svc.bulk_upsert_annotations(client, annotations_to_insert)
        counts["inserted_count"] += annotation_result.get("inserted", 0)

        resolved_events_full = [
            {**row, "external_event_id": event_result["event_id_by_provider_event_id"][eid]}
            for eid, row in resolution["event_row_by_id"].items()
            if eid in event_result["event_id_by_provider_event_id"]
        ]
        actor_result = actor_svc.generate_candidates_for_batch(
            client, events=resolved_events_full,
            external_signal_ids_by_event_id={k: list(v) for k, v in signal_ids_by_event_id.items()},
            ingestion_run_id=run["ingestion_run_id"], is_demo=is_demo,
        )
        # Phase2B-1: このbatchで新規created/evidence_addedされたActor Episode Candidateだけを
        # affected keyとしてrecluster_keyへ渡す（cluster_svc側がkeyを解決し、対象keyの
        # eligible Candidate全件から毎回ゼロから再構築する。投入順に依存しない）。
        affected_ids = actor_result.get("affected_actor_episode_candidate_ids") or []
        if affected_ids:
            cluster_svc.recluster_affected_keys(client, actor_episode_candidate_ids=affected_ids)
        # Situation Linkの再計算はbatchループの外（この関数の呼び出し1回につき1回）で行う
        # （catch-upで多数batchを処理する場合の冗長な再構築を防ぐ、下記参照）。

        signals_by_id = {}
        if signal_ids_for_annotations:
            rows = signal_svc._select_in_chunked(
                client, "geo_external_signals", select="external_signal_id,source_domain,title,published_at",
                in_column="external_signal_id", in_values=list(signal_ids_for_annotations))
            signals_by_id = {r["external_signal_id"]: r for r in rows}

        # Phase A（Collection Reliability、2026-10-05）: bulk_upsert_annotationsが返す実際の
        # annotation_idを引き継がず常にNoneのままgenerate_candidates_for_batchへ渡していたため、
        # geo_media_episode_candidates.annotation_id（not null外部キー）へのinsertが必ず
        # 制約違反（400）で失敗していた（実データでraw stream側の一致が初めて発生するまで
        # この経路が一度も実行されず気づかれていなかった）。
        annotation_id_by_provider_record_id = annotation_result.get("annotation_id_by_provider_record_id", {})
        annotation_rows_for_media = []
        for g, ann_normalized in zip(matched_gkg, annotations_to_insert):
            signal_id = ann_normalized["external_signal_id"]
            annotation_id = annotation_id_by_provider_record_id.get(ann_normalized["provider_record_id"])
            annotation_rows_for_media.append({
                **g, "external_signal_id": signal_id, "annotation_id": annotation_id,
            })

        related_event_ids_by_signal_id = {}
        for signal_id in signal_ids_for_annotations:
            related_event_ids_by_signal_id[signal_id] = event_svc.get_related_event_ids_for_signal(client, signal_id)

        media_result = media_svc.generate_candidates_for_batch(
            client, annotations=annotation_rows_for_media, signals_by_id=signals_by_id,
            related_event_ids_by_signal_id=related_event_ids_by_signal_id,
            ingestion_run_id=run["ingestion_run_id"], is_demo=is_demo,
        )

        retryable_pending_count = len(resolution["unresolved_event_ids_retryable"])
        terminal_count = len(resolution["unresolved_event_ids_terminal"])
        if fetch_incomplete:
            retryable_pending_count += 1
            # 片方/双方404（未成熟）をretryable_pending_countへ明示的に加算する。これにより
            # list_completed_batch_timestampsの「completed_with_warningsかつretryable_pending_
            # count==0」判定が誤ってこのbatchを完了扱いしなくなる（片方404でもcompletedに
            # なりうる旧バグの修正）。

        # 未解決Eventはerror_countではなくstatus=completed_with_warningsで表現する（warningとerrorを区別）
        if is_permanent_gap:
            status = "completed"
        elif counts["error_count"] > 0 and counts["fetched_count"] == 0 and not fetch_incomplete:
            status = "failed"
        elif retryable_pending_count == 0 and terminal_count == 0 and counts["error_count"] == 0:
            status = "completed"
        else:
            status = "completed_with_warnings"

        next_scheduled_at = base.calculate_next_run("daily")
        base.complete_ingestion_run(
            client, ingestion_run_id=run["ingestion_run_id"], status=status, counts=counts,
            error_summary="; ".join(errors) if errors else None, next_scheduled_at=next_scheduled_at,
            connector_version=CONNECTOR_VERSION,
        )
        # retryable_pending_count/batch_gapはgeo_ingestion_runsに専用result列が無いため
        # request_parametersへ明示的にUPDATEする。
        client.update("geo_ingestion_runs", {"ingestion_run_id": f"eq.{run['ingestion_run_id']}"}, {
            "request_parameters": {"batch_timestamp": batch_timestamp,
                                    "retryable_pending_count": retryable_pending_count,
                                    "batch_gap": is_permanent_gap},
        })

        batch_results.append({
            "batch_timestamp": batch_timestamp, "ingestion_run_id": run["ingestion_run_id"],
            "status": status, "counts": counts,
            "skipped_non_web_source": skipped_non_web,
            "resolved_event_count": resolution["resolved_count"],
            "unresolved_event_ids_retryable": len(resolution["unresolved_event_ids_retryable"]),
            "unresolved_event_ids_terminal": len(resolution["unresolved_event_ids_terminal"]),
            "actor_candidates": actor_result, "media_candidates": media_result,
            "errors": errors,
        })

    if batch_results:
        # Phase2B-2A: Episode Cluster再構築後にそのis_demoスコープのSituation Linkを
        # ゼロから再計算する。Situation Linkは常に「そのis_demoスコープの全Cluster」から
        # 再構築されるため、毎batch後に呼んでも全batch後に1回呼んでも最終結果は同じ——
        # catch-upで多数batchを処理する場合の冗長な計算を避けるためループの外で1回だけ呼ぶ。
        link_svc.recompute_situation_links(client, is_demo=is_demo)

    return {"batches": batch_results}


def run_enabled_gdelt_stream(client, *, requested_by: str = "batch") -> dict:
    """config.jsonのgeo_external_signals.gdelt_stream.enabledを見て呼ぶ薄いラッパー。"""
    return run_gdelt_structured_ingestion(client, requested_by=requested_by)


def run_gdelt_stream_catchup(client, *, since: str = None, until: str = None,
                              batches_per_call: int = DEFAULT_CATCHUP_BATCHES_PER_CALL,
                              max_calls: int = DEFAULT_CATCHUP_MAX_CALLS,
                              batch_maturity_hours: int = None, bypass_maturity_clamp: bool = False,
                              promote_to_checkpoint: bool = False,
                              requested_by: str = "cli", is_demo: bool = False) -> dict:
    """list_missing_batch_timestampsが空になるまで、またはmax_calls回に達するまで
    run_gdelt_structured_ingestionを繰り返し呼ぶ。Event resolution予算
    （max_event_resolution_batches_per_run、既定20）は1回の呼び出しごとにリセットされるため、
    広範囲のInitial Loadは自然に複数回へ分割される——この関数がその再試行を自動化する
    （運用者が手動で何度も再実行する必要をなくす）。同一since/untilで再実行しても安全
    （list_missing_batch_timestampsが既完了分を除外するため）。`until`は明示指定されても
    maturity cutoffでclampされる（bypass_maturity_clamp=Trueの場合のみ例外）。全ての
    is_demo=trueの呼び出しはis_demo=falseのcheckpoint/watermarkに一切影響しない
    （逆方向も同様）。

    各呼び出しの前後でmissing batch集合を比較し、前回で実際に解決できたbatchが1件も
    無ければ（同一batchが恒常的に失敗している可能性が高い）、max_calls回連打せずそこで
    停止し、stopped_reason="no_progress"として明示的に報告する。単純な件数比較ではなく
    missing_before - missing_afterの集合差分で判定する——maturity cutoffが時間経過とともに
    動くため、件数だけの比較だと「新しくmatureになったbatchが増えた」ことを誤って
    「進捗ゼロ」と判定しかねないため。

    ループ終了後、compute_completed_throughで（is_demoスコープに閉じた）連続terminal区間の
    watermarkを再計算し、connector_type='gdelt_stream_catchup'のsummary run（実データ取得を
    伴わないbookkeeping専用の1行）としてrequest_parameters.completed_throughに保存する。

    baseline/horizonの計算はsinceの有無で分岐する（fallback 24hをAd-hoc rangeの計算に
    紛れ込ませない）:
    - since省略（定常Incremental運用）→resolve_incremental_sinceと同じ規則（prior
      watermark、無ければfallback_hours起点のbootstrap baseline）でbaseline/horizonを決める。
    - since明示指定（Historical/Ad-hoc catch-up、promote_to_checkpointの値に関わらず）→
      baselineはaligned sinceの1 batch前、horizonはmaturity clamp適用後のeffective
      until。指定range自身のcontiguous terminal frontierだけを計算し、fallback_hoursは
      一切参照しない。

    checkpoint_scopeは以下のルールで決まる:
    - since省略（定常Incremental運用）→常に"incremental"（定常watermarkを更新する）
    - since明示指定かつpromote_to_checkpoint=False（既定、Historical/Ad-hoc catch-up）→
      "adhoc"（記録は残すがresolve_incremental_sinceからは無視される——定常watermarkに
      一切影響しない）
    - since明示指定かつpromote_to_checkpoint=Trueは、以下を満たす場合のみ"incremental"
      （定常watermarkを前進させる）。満たさない場合は"adhoc"のまま据え置き、
      promotion_rejected_reasonにその理由を明示する:
      - 完全解決していること: stopped_reason=="caught_up"かつremaining_batch_count==0
        （不完全なcatch-upをpromoteしない）。満たさなければ"incomplete_catchup"。
      - is_demoスコープに閉じた既存incremental watermarkが無い場合（Initial Load初回の
        bootstrap）: このrange自身のwatermarkをそのままbootstrap checkpointとして許可する
        （橋渡し対象が無いため）。
      - 既存incremental watermarkがある場合: 既存watermarkをbaseline、今回計算した
        candidate watermarkをhorizonとしてcompute_completed_throughを再計算し、その結果
        （bridge watermark）がcandidate watermarkに一致する（＝既存watermarkからcandidate
        watermarkまで完全に連続terminalである）場合のみpromotionを許可する。途中に1件でも
        missing/retryableなbatchがあればbridge watermark < candidate watermarkとなり、
        promotion_rejected_reason="checkpoint_gap"として拒否する（既存checkpointと今回の
        Ad-hoc rangeの間に穴が残ったまま定常watermarkを飛び越えて前進させない。純粋な
        後退——candidate watermarkが既存watermark以下——もこの橋渡し計算で自然に失敗し、
        同じ"checkpoint_gap"で拒否される）。"""
    all_batches = []
    calls_made = 0
    stopped_reason = "caught_up"
    last_error = None
    for _ in range(max_calls):
        missing_before = set(list_missing_batch_timestamps(
            client, since=since, until=until, batch_maturity_hours=batch_maturity_hours,
            bypass_maturity_clamp=bypass_maturity_clamp, is_demo=is_demo))
        if not missing_before:
            break
        try:
            result = run_gdelt_structured_ingestion(
                client, since=since, until=until, max_batches=batches_per_call,
                batch_maturity_hours=batch_maturity_hours, bypass_maturity_clamp=bypass_maturity_clamp,
                requested_by=requested_by, is_demo=is_demo)
        except Exception as e:
            # Phase A（Collection Reliability、2026-10-05）: 社内プロキシ/Supabase側の一時的な
            # ネットワーク障害でこの1callが未捕捉例外を投げると、catchup全体がクラッシュし、
            # 既に確定しているwatermark再計算・summary run記録すら行われないまま異常終了する
            # 事象を実際に確認した（geo_ingestion_runsがstatus=runningのまま残留する温床になる）。
            # この1callのみ失敗として打ち切り、以降は既存のwatermark再計算ロジックへ合流させる
            # （次回呼び出しで同じbatchから再試行できる、リトライ安全な設計のまま）。
            last_error = f"{type(e).__name__}: {e}"
            stopped_reason = "error"
            break
        calls_made += 1
        all_batches.extend(result["batches"])

        missing_after = set(list_missing_batch_timestamps(
            client, since=since, until=until, batch_maturity_hours=batch_maturity_hours,
            bypass_maturity_clamp=bypass_maturity_clamp, is_demo=is_demo))
        resolved_count = len(missing_before - missing_after)
        if resolved_count == 0:
            stopped_reason = "no_progress"
            break
    else:
        stopped_reason = "max_calls_reached"

    remaining = list_missing_batch_timestamps(
        client, since=since, until=until, batch_maturity_hours=batch_maturity_hours,
        bypass_maturity_clamp=bypass_maturity_clamp, is_demo=is_demo)

    now = datetime.now(timezone.utc)
    # batch_maturity_hoursがNoneの場合の解決は、ループ内のlist_missing_batch_timestamps/
    # run_gdelt_structured_ingestionが使うのと同じ_resolve_configured_maturity_hours()経由の
    # 値を使う（設定を2箇所で別々に解決してズレさせない）。
    effective_maturity_hours = (batch_maturity_hours if batch_maturity_hours is not None
                                 else _resolve_configured_maturity_hours())
    if since is None:
        prior_watermark = _find_last_catchup_watermark(client, is_demo=is_demo)
        baseline = (_align_to_batch_grid(prior_watermark) if prior_watermark
                    else _bootstrap_baseline(DEFAULT_LOOKBACK_HOURS, now=now))
        horizon = now.strftime("%Y%m%d%H%M%S")
    else:
        # Ad-hoc/Historical: fallback_hoursを一切参照せず、指定range自身のcontiguous
        # frontierを計算する。
        baseline = _baseline_before(since)
        horizon = _resolve_effective_until(until, batch_maturity_hours=effective_maturity_hours,
                                            bypass_maturity_clamp=bypass_maturity_clamp, now=now)
    watermark = compute_completed_through(client, baseline=baseline, horizon=horizon, is_demo=is_demo)

    checkpoint_scope = CHECKPOINT_SCOPE_ADHOC
    promotion_rejected_reason = None
    if since is None:
        checkpoint_scope = CHECKPOINT_SCOPE_INCREMENTAL
    elif promote_to_checkpoint:
        if not (stopped_reason == "caught_up" and len(remaining) == 0):
            promotion_rejected_reason = "incomplete_catchup"
        else:
            existing_watermark = _find_last_catchup_watermark(client, is_demo=is_demo)
            if existing_watermark is None:
                # Initial Load初回のbootstrap: 橋渡し対象が無いため、このrange自身の
                # watermarkをそのままbootstrap checkpointとして許可する。
                checkpoint_scope = CHECKPOINT_SCOPE_INCREMENTAL
            else:
                # 既存watermark→candidate watermarkの間が完全に連続terminalか再計算する。
                # 単純な大小比較では、既存watermarkと今回のrangeの間に未取得gapが挟まって
                # いる場合を見逃す（純粋な後退もこの橋渡し計算で自然に失敗する）。
                bridge_watermark = compute_completed_through(
                    client, baseline=_align_to_batch_grid(existing_watermark), horizon=watermark,
                    is_demo=is_demo)
                if bridge_watermark == watermark:
                    checkpoint_scope = CHECKPOINT_SCOPE_INCREMENTAL
                else:
                    promotion_rejected_reason = "checkpoint_gap"

    request_parameters = {"completed_through": watermark, "checkpoint_scope": checkpoint_scope}
    if promotion_rejected_reason:
        request_parameters["promotion_rejected_reason"] = promotion_rejected_reason
    if last_error:
        request_parameters["last_error"] = last_error
    summary_run = base.create_ingestion_run(
        client, source_id=None, connector_type=CATCHUP_SUMMARY_CONNECTOR_TYPE, run_type="cli",
        requested_by=requested_by, request_parameters=request_parameters, is_demo=is_demo)
    base.complete_ingestion_run(
        client, ingestion_run_id=summary_run["ingestion_run_id"],
        status="completed_with_warnings" if last_error else "completed",
        error_summary=last_error)

    return {"calls_made": calls_made, "batches": all_batches, "remaining_batch_count": len(remaining),
            "stopped_reason": stopped_reason, "completed_through": watermark,
            "checkpoint_scope": checkpoint_scope, "promotion_rejected_reason": promotion_rejected_reason,
            "last_error": last_error}
