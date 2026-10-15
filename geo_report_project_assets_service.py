"""Report Studio「Design」領域: Project Asset層（Phase B）

Narrative Master / Report TemplateのDraft・Published・History管理、CSV round-trip
（Narrative Master、Current契約34列）、Diff、Design State（Report Design/Signals/
Queries）、Atomic Publish（`geo_report_publish_design` RPCの薄いラッパー）を扱う。

根拠: C:\\Users\\269811\\.claude\\plans\\kind-napping-hare.md B/D/I.1/J.1章
（Plan Mode承認済み、sql/2026-09-11_geo_report_studio_design_and_current_pipeline.sql
で導入したスキーマに対応）。

LLM呼び出し（Design Chat本体、Template Extraction）はここでは行わない
（Phase C: geo_report_design_chat_service.py / geo_report_design_expert_adapter.py の責務）。
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from typing import Optional

import geo_report_processing_service as processing
import geo_report_run_provisioning_service as provisioning

CHUNK_SIZE = 500

# Narrative Master Current契約（実データヘッダ実測、34列。Plan D章参照）。
# dimension_config_jsonはこの契約に含まれない（Babelstreet由来の別レイヤー、
# geo_report_classification_service.get_dimension_configが別途フォールバックする）。
NARRATIVE_MASTER_COLUMNS = [
    "signal_id", "signal_name_internal", "report_topic_label", "narrative_id", "narrative_name",
    "required_context", "tone_hint", "public_voice_hint", "report_use_hint", "continuity_hint", "notes",
    "signal_source", "signal_name", "theme_id", "theme_name", "trend_role", "trend_start_date",
    "main_narrative", "sub_narrative", "sub_narrative_id", "stance_default", "include_keywords",
    "required_context_keywords", "exclude_keywords", "public_voice_cues", "cluster_split_terms",
    "definition_ja", "pa_implication_ja", "example_phrases", "priority", "max_cluster_records",
    "representative_count", "is_fallback", "active",
]

# Shellの前処理(run.ps1)が実際に検証・使用する列（Phase A監査結果1で確定した19列）。
# 残り15列（signal_id/narrative_id/narrative_name等）はPrompt1/2 AIやUI表示専用で
# Preprocessには使われない。
NARRATIVE_MASTER_SHELL_REQUIRED_COLUMNS = [
    "signal_source", "signal_name", "theme_id", "theme_name", "trend_role", "trend_start_date",
    "main_narrative", "sub_narrative", "sub_narrative_id", "include_keywords",
    "required_context_keywords", "exclude_keywords", "public_voice_cues", "cluster_split_terms",
    "priority", "max_cluster_records", "representative_count", "is_fallback", "active",
]

# Design Chat（Prompt0契約13列相当）が主に書く列 → run.ps1が読むShell専用列名への
# ミラー。Design側は signal_id/narrative_id/narrative_name を使い、Shell側は
# signal_source/sub_narrative_id/sub_narrative を使うため、保存時に同期する
# （どちらか一方しか埋まっていない行がPreprocessで無視される事故を防ぐ）。
_DESIGN_TO_SHELL_MIRROR = {
    "signal_id": "signal_source",
    "narrative_id": "sub_narrative_id",
    "narrative_name": "sub_narrative",
}


def _new_id() -> str:
    return str(uuid.uuid4())


def _chunked(items: list, size: int = CHUNK_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---------------------------------------------------------------------------
# Narrative Master: CSV round-trip
# ---------------------------------------------------------------------------

def sync_shell_mirror_columns(row: dict) -> dict:
    """Design側の列(signal_id/narrative_id/narrative_name)とShell側の列
    (signal_source/sub_narrative_id/sub_narrative)を相互補完する。片方だけが
    埋まっている場合、空の側へコピーする（両方埋まっている場合は既存値を優先し、
    上書きしない——手動編集でどちらかだけ意図的に変えているケースを壊さないため）。"""
    out = dict(row)
    for design_col, shell_col in _DESIGN_TO_SHELL_MIRROR.items():
        design_val = processing.clean_value(out.get(design_col, ""))
        shell_val = processing.clean_value(out.get(shell_col, ""))
        if design_val and not shell_val:
            out[shell_col] = design_val.upper() if design_col == "signal_id" else design_val
        elif shell_val and not design_val:
            out[design_col] = shell_val.upper() if shell_col == "signal_source" else shell_val
    return out


def narrative_master_rows_to_csv_text(rows: list) -> str:
    """Narrative Master行をCurrent契約34列のCSVテキストへ変換する
    （Download / ChatGPT Fallback Export共通で使う）。"""
    buf = io.StringIO()
    buf.write(",".join(NARRATIVE_MASTER_COLUMNS) + "\r\n")
    for row in rows:
        synced = sync_shell_mirror_columns(row)
        values = [processing.escape_csv(synced.get(col, "")) for col in NARRATIVE_MASTER_COLUMNS]
        buf.write(",".join(values) + "\r\n")
    return buf.getvalue()


def parse_narrative_master_csv(csv_text: str) -> list:
    """CSVテキストをNarrative Master行のlist[dict]へ変換する（Upload / ChatGPT
    Fallback Import共通）。列順・欠落列には依存しない（DictReaderのheaderから
    そのまま読む、余分な列も保持する——将来の列追加に強くするため）。"""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []
    for raw in reader:
        row = {k: processing.clean_value(v) for k, v in raw.items() if k is not None}
        rows.append(sync_shell_mirror_columns(row))
    return rows


# Narrative MasterのList系4列（2026-09-15追加）。split_phrasesはセミコロン(;)/
# パイプ(|)でのみ分割し、カンマは区切り文字として扱わない（run.ps1忠実移植の既存仕様、
# Iran/EUDR等の実運用に影響するため関数自体は変更しない）。AIがカンマ区切りで
# キーワードを生成すると、この列が「1つの巨大な文字列」としてしか扱われず絶対に
# マッチしなくなる。
_LIST_FIELDS_REQUIRING_SEMICOLON = (
    "include_keywords", "required_context_keywords", "exclude_keywords", "cluster_split_terms",
)


def _check_list_field_delimiters(row: dict, line_no: int, signal: str) -> list:
    """外部監査（2026-09-15実機報告）で発覚した重大バグの再発防止チェック。あるSignalの
    Narrative MasterがカンマでList列を書いてしまい、split_phrases（セミコロンのみで
    分割）が1要素のリストとして扱ったため、そのSignalの1,217/1,217レコードが
    100% fallbackになった（require_context_keywordsが絶対に一致しないため、全レコードが
    required-context不足でfallbackへ回された）。セミコロンが無くカンマがある値は、
    この間違いのほぼ確実なシグナルとしてPublishをブロックする。"""
    errors = []
    for field in _LIST_FIELDS_REQUIRING_SEMICOLON:
        value = processing.clean_value(row.get(field, ""))
        if value and ";" not in value and "," in value:
            errors.append(
                f"{line_no}行目 ({signal}): {field}がカンマ区切りになっています"
                f"（値: '{value[:80]}...'）。この列はセミコロン(;)区切りである必要があります"
                "——カンマ区切りのままだとマッチング処理が全く機能せず、全レコードがfallbackになります。"
            )
    return errors


def validate_narrative_master(rows: list) -> list:
    """Narrative Master Publish Gate（Plan B章「Publish Gate」・Phase A監査結果1）。
    エラーメッセージのlistを返す（空 = valid）。run.ps1実測の検証ルールを踏襲するが、
    「S1〜S7必須」のような1案件専用のハードコードは一般化する（実際に存在する
    signalのみを対象に検証する）。"""
    errors = []
    if not rows:
        errors.append("Narrative Masterに行がありません。")
        return errors

    narrative_ids_seen = {}
    fallback_count_by_signal = {}
    active_signals = set()
    primary_rows_by_signal = {}

    for i, row in enumerate(rows):
        line_no = i + 2  # ヘッダ行を1行目として数える（CSV上の実行番号に合わせる）
        signal = processing.clean_value(row.get("signal_source") or row.get("signal_id", "")).upper()
        narrative_id = processing.clean_value(row.get("sub_narrative_id") or row.get("narrative_id", ""))
        active = processing.parse_bool(row.get("active"), True)

        if not signal:
            errors.append(f"{line_no}行目: signal_id/signal_sourceが空です。")
            continue
        if not narrative_id:
            errors.append(f"{line_no}行目 ({signal}): narrative_id/sub_narrative_idが空です。")
            continue

        errors.extend(_check_list_field_delimiters(row, line_no, signal))

        if narrative_id in narrative_ids_seen:
            errors.append(
                f"{line_no}行目: narrative_id '{narrative_id}' が{narrative_ids_seen[narrative_id]}行目と重複しています"
                "（sub_narrative_idはMaster全体でユニークである必要があります）。"
            )
        else:
            narrative_ids_seen[narrative_id] = line_no

        if not active:
            continue
        active_signals.add(signal)
        if processing.parse_bool(row.get("is_fallback"), False):
            fallback_count_by_signal[signal] = fallback_count_by_signal.get(signal, 0) + 1

        trend_role = processing.clean_value(row.get("trend_role", "")).lower()
        if trend_role == "primary":
            primary_rows_by_signal.setdefault(signal, []).append((line_no, row.get("trend_start_date", "")))

    for signal in sorted(active_signals):
        count = fallback_count_by_signal.get(signal, 0)
        if count != 1:
            errors.append(
                f"Signal {signal}: active fallback行がちょうど1つ必要ですが{count}個あります。"
            )

    for signal, entries in primary_rows_by_signal.items():
        for line_no, trend_start_date in entries:
            if processing.parse_date_safe(trend_start_date) is None:
                errors.append(
                    f"{line_no}行目 ({signal}): trend_role=primaryの行はtrend_start_dateが必須・有効な日付である必要があります"
                    f"（値: '{trend_start_date}'）。"
                )

    return errors


def diff_narrative_master(old_rows: list, new_rows: list) -> dict:
    """Narrative Master 2バージョン間の構造化Diff（signal_id+narrative_idで行を対応付ける、
    difflibのunified_diffではなく行単位の意味的比較にする——Narrative Masterは
    テキストファイルではなく構造化データのため）。"""

    def _key(row):
        signal = processing.clean_value(row.get("signal_source") or row.get("signal_id", "")).upper()
        narrative_id = processing.clean_value(row.get("sub_narrative_id") or row.get("narrative_id", ""))
        return (signal, narrative_id)

    old_by_key = {_key(r): r for r in old_rows}
    new_by_key = {_key(r): r for r in new_rows}

    added = [new_by_key[k] for k in new_by_key if k not in old_by_key]
    removed = [old_by_key[k] for k in old_by_key if k not in new_by_key]
    changed = []
    for k in old_by_key:
        if k not in new_by_key:
            continue
        old_row, new_row = old_by_key[k], new_by_key[k]
        field_diffs = {}
        for col in NARRATIVE_MASTER_COLUMNS:
            old_val = processing.clean_value(old_row.get(col, ""))
            new_val = processing.clean_value(new_row.get(col, ""))
            if old_val != new_val:
                field_diffs[col] = {"old": old_val, "new": new_val}
        if field_diffs:
            changed.append({"signal_id": k[0], "narrative_id": k[1], "fields": field_diffs})

    return {
        "added": [{"signal_id": _key(r)[0], "narrative_id": _key(r)[1]} for r in added],
        "removed": [{"signal_id": _key(r)[0], "narrative_id": _key(r)[1]} for r in removed],
        "changed": changed,
        "unchanged_count": len(old_by_key) - len(removed) - len(changed),
    }


# ---------------------------------------------------------------------------
# Project Asset CRUD（Narrative Master / Report Template 共通）
# ---------------------------------------------------------------------------

def get_draft(client, project_id: str, asset_type: str) -> Optional[dict]:
    rows = client.select("geo_report_project_assets", {
        "select": "*", "project_id": f"eq.{project_id}", "asset_type": f"eq.{asset_type}",
        "status": "eq.draft",
    })
    return rows[0] if rows else None


def get_published(client, project_id: str, asset_type: str) -> Optional[dict]:
    rows = client.select("geo_report_project_assets", {
        "select": "*", "project_id": f"eq.{project_id}", "asset_type": f"eq.{asset_type}",
        "status": "eq.published",
    })
    return rows[0] if rows else None


def list_history(client, project_id: str, asset_type: str) -> list:
    """Published/Superseded版を新しい順で返す（Draftは含まない、Historyの対象は
    不変スナップショットのみ）。"""
    rows = client.select("geo_report_project_assets", {
        "select": "*", "project_id": f"eq.{project_id}", "asset_type": f"eq.{asset_type}",
        "status": "in.(published,superseded)", "order": "created_at.desc",
    })
    return rows


def get_asset_by_id(client, asset_id: str) -> Optional[dict]:
    rows = client.select("geo_report_project_assets", {"select": "*", "id": f"eq.{asset_id}"})
    return rows[0] if rows else None


def _save_draft(client, project_id: str, asset_type: str, content, *, source: str = "manual",
                 row_count: Optional[int] = None) -> dict:
    existing = get_draft(client, project_id, asset_type)
    if existing:
        client.update("geo_report_project_assets", {"id": f"eq.{existing['id']}"}, {
            "content": content, "source": source, "row_count": row_count,
        })
        existing.update({"content": content, "source": source, "row_count": row_count})
        return existing
    payload = {
        "id": _new_id(), "project_id": project_id, "asset_type": asset_type, "status": "draft",
        "content": content, "source": source, "row_count": row_count,
    }
    inserted = client.insert("geo_report_project_assets", [payload])
    return inserted[0] if inserted else payload


def normalize_list_field_delimiters(row: dict) -> dict:
    """Narrative MasterのList系4列（include_keywords/required_context_keywords/
    exclude_keywords/cluster_split_terms）を、カンマ/セミコロン/パイプいずれの
    区切り文字で書かれていてもセミコロン区切りへ正規化する（2026-09-16追加）。

    根本原因: Shellのマッチング処理（split_phrases）はセミコロン(;)/パイプ(|)
    でのみ分割し、カンマは区切り文字として扱わない（run.ps1忠実移植の既存仕様、
    Iran/EUDR等の実運用に影響するため関数自体は変更しない）。AIがカンマ区切りで
    生成した場合、その列は「1つの巨大な文字列」としてしか扱われず絶対にマッチ
    しなくなる——実データであるSignalが1,217/1,217件（100%）fallbackになる
    重大な実害が確認された。_check_list_field_delimitersによるPublish Gateでの
    検知（警告）に加え、保存時点でこの関数が自動的に正規化することで、
    そもそも壊れた状態がPublishまで残らないようにする。"""
    out = dict(row)
    for field in _LIST_FIELDS_REQUIRING_SEMICOLON:
        value = processing.clean_value(out.get(field, ""))
        if not value:
            continue
        parts = [p.strip() for p in re.split(r"[,;|]", value) if p.strip()]
        out[field] = "; ".join(parts)
    return out


def save_narrative_master_draft(client, project_id: str, rows: list, *, source: str = "manual") -> dict:
    """検証済みのNarrative Master行をDraftとして保存する。無効な場合はValueErrorを送出する
    （呼び出し元がPublish Gateのエラー一覧をそのままUIへ返せるよう、メッセージを改行結合する）。

    保存前に必ずnormalize_list_field_delimitersを通す（2026-09-16追加）——これにより
    delimiter不整合は通常の保存経路では原理的に発生しなくなる。validate_narrative_
    masterの_check_list_field_delimitersチェックは、将来この関数を経由しない
    書き込み経路が増えた場合の安全網として残す（defense in depth）。"""
    synced_rows = [normalize_list_field_delimiters(sync_shell_mirror_columns(r)) for r in rows]
    errors = validate_narrative_master(synced_rows)
    if errors:
        raise ValueError("Narrative Masterの検証エラー:\n" + "\n".join(errors))
    return _save_draft(client, project_id, "narrative_master", {"rows": synced_rows}, source=source,
                        row_count=len(synced_rows))


def download_narrative_master_csv(client, project_id: str, *, status: str = "draft") -> str:
    asset = get_draft(client, project_id, "narrative_master") if status == "draft" \
        else get_published(client, project_id, "narrative_master")
    if not asset:
        raise ValueError(f"Narrative Master（{status}）が見つかりません。")
    return narrative_master_rows_to_csv_text(asset["content"].get("rows", []))


def upload_narrative_master_csv(client, project_id: str, csv_text: str, *,
                                 source: str = "csv_upload") -> dict:
    rows = parse_narrative_master_csv(csv_text)
    return save_narrative_master_draft(client, project_id, rows, source=source)


# ---------------------------------------------------------------------------
# Report Template（I.1の構造化Section形式）
# ---------------------------------------------------------------------------

TEMPLATE_COMPONENT_TYPES = {
    "narrative_block", "metric_grid", "stacked_bar_chart", "scenario_cards",
    "comparison_table", "example_quotes", "data_limits_note",
}


def validate_report_template(content: dict) -> list:
    """Report Template Asset（I.1）の構造検証。style_css/shell_html/sectionsの
    最低限の型・必須sectionフィールド・component_typeの妥当性を確認する。"""
    errors = []
    if not isinstance(content, dict):
        return ["Report Templateはオブジェクト（style_css/shell_html/sections）である必要があります。"]
    sections = content.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("Report Templateにはsections（1件以上の配列）が必要です。")
        return errors
    seen_ids = set()
    for i, section in enumerate(sections):
        label = section.get("section_id", f"index={i}") if isinstance(section, dict) else f"index={i}"
        if not isinstance(section, dict):
            errors.append(f"Section {label}: オブジェクトである必要があります。")
            continue
        section_id = section.get("section_id")
        if not section_id:
            errors.append(f"Section {label}: section_idが必須です。")
        elif section_id in seen_ids:
            errors.append(f"Section {section_id}: section_idが重複しています。")
        else:
            seen_ids.add(section_id)
        component_type = section.get("component_type")
        if component_type not in TEMPLATE_COMPONENT_TYPES:
            errors.append(
                f"Section {label}: component_type '{component_type}' は未対応です"
                f"（対応: {sorted(TEMPLATE_COMPONENT_TYPES)}）。"
            )
        if not isinstance(section.get("required_fields"), list):
            errors.append(f"Section {label}: required_fieldsは配列である必要があります。")
    return errors


def save_report_template_draft(client, project_id: str, content: dict, *, source: str = "manual") -> dict:
    errors = validate_report_template(content)
    if errors:
        raise ValueError("Report Templateの検証エラー:\n" + "\n".join(errors))
    return _save_draft(client, project_id, "report_template", content, source=source,
                        row_count=len(content.get("sections", [])))


def get_template_sections(asset: dict) -> list:
    return (asset or {}).get("content", {}).get("sections", [])


# ---------------------------------------------------------------------------
# Design State（Report Design / Signals / Queries、常に可変）
# ---------------------------------------------------------------------------

# bs_query_plan.csv契約（Plan Section 2.1 / kind-napping-hare.md B章）。Design Chatの
# CHAT_TURN_SCHEMA["queries"]と完全一致する列名——変換ロジックを挟まずそのままserialize
# できるよう、意図的に同じフィールド名で設計されている。
BS_QUERY_PLAN_COLUMNS = [
    "signal_id", "report_topic_label", "fixed_query_group_a", "fixed_query_group_b",
    "event_terms", "exclude_terms", "continuity_role", "collection_notes",
]
_BS_QUERY_PLAN_ARRAY_COLUMNS = {"fixed_query_group_a", "fixed_query_group_b", "event_terms", "exclude_terms"}


def build_bs_query_plan_csv(queries: list) -> str:
    """Design State（`geo_report_design_state.queries`）をbs_query_plan.csvへ
    そのままserializeする（列名の変換無し）。配列列（fixed_query_group_a等）は
    "|"区切りのテキストへ変換する（既存のexception_pattern_to_text等と同じ規約）。"""
    buf = io.StringIO()
    buf.write(",".join(BS_QUERY_PLAN_COLUMNS) + "\r\n")
    for row in queries or []:
        values = []
        for col in BS_QUERY_PLAN_COLUMNS:
            value = row.get(col, "")
            if col in _BS_QUERY_PLAN_ARRAY_COLUMNS and isinstance(value, list):
                value = "|".join(str(v) for v in value)
            values.append(processing.escape_csv(value))
        buf.write(",".join(values) + "\r\n")
    return buf.getvalue()


def get_design_state(client, project_id: str) -> dict:
    rows = client.select("geo_report_design_state", {"select": "*", "project_id": f"eq.{project_id}"})
    if rows:
        return rows[0]
    return {
        "project_id": project_id, "report_design": {}, "signals": [], "queries": [],
        "pending_report_design": None, "pending_signals": None, "pending_queries": None,
    }


def save_design_state(client, project_id: str, *, report_design=None, signals=None, queries=None,
                       updated_by: Optional[str] = None) -> dict:
    existing_rows = client.select("geo_report_design_state", {
        "select": "project_id", "project_id": f"eq.{project_id}",
    })
    patch = {}
    if report_design is not None:
        patch["report_design"] = report_design
    if signals is not None:
        patch["signals"] = signals
    if queries is not None:
        patch["queries"] = queries
    if updated_by is not None:
        patch["updated_by"] = updated_by
    if existing_rows:
        client.update("geo_report_design_state", {"project_id": f"eq.{project_id}"}, patch)
    else:
        client.insert("geo_report_design_state", [{
            "project_id": project_id,
            "report_design": report_design if report_design is not None else {},
            "signals": signals if signals is not None else [],
            "queries": queries if queries is not None else [],
            "pending_report_design": None, "pending_signals": None, "pending_queries": None,
            **({"updated_by": updated_by} if updated_by is not None else {}),
        }])
    return get_design_state(client, project_id)


def save_pending_design_state(client, project_id: str, *, report_design=None, signals=None, queries=None) -> dict:
    """Design Chatの「提案→FIX」ワークフロー用（2026-09-15、ユーザー要望）。
    チャット中の提案はconfirmed列（report_design/signals/queries）を直接書き換えず、
    pending_*列へのみ保存する。ユーザーが画面の「FIX」ボタンを押しfix_design_stateを
    呼ぶまで、confirmed列・Publish・Narrative Master自動生成には一切影響しない。
    呼び出し元（process_chat_turn）は、直前のpending値（無ければconfirmed値）を
    ベースに今回のpatchをマージした「完全な値」を渡すこと（ここでは単純上書きする）。"""
    existing_rows = client.select("geo_report_design_state", {
        "select": "project_id", "project_id": f"eq.{project_id}",
    })
    patch = {}
    if report_design is not None:
        patch["pending_report_design"] = report_design
    if signals is not None:
        patch["pending_signals"] = signals
    if queries is not None:
        patch["pending_queries"] = queries
    if not patch:
        return get_design_state(client, project_id)
    if existing_rows:
        client.update("geo_report_design_state", {"project_id": f"eq.{project_id}"}, patch)
    else:
        client.insert("geo_report_design_state", [{
            "project_id": project_id, "report_design": {}, "signals": [], "queries": [],
            "pending_report_design": None, "pending_signals": None, "pending_queries": None,
            **patch,
        }])
    return get_design_state(client, project_id)


def fix_design_state(client, project_id: str) -> dict:
    """Design Chatで提案された内容（pending_*）を確定し、confirmed列
    （report_design/signals/queries）へコミットする。pendingがNoneの項目
    （一度もこのセッションで提案されていない項目）はconfirmed値を変更しない。
    コミット後、pending_*は全てNULLへ戻す（次の提案サイクルに備える）。

    2026-09-15追加: primary_graph_signals/final_graph_ruleが未設定のままFIXされ、
    Publish→Run作成→Aggregation実行で初めて気づくという同種のユーザー報告が
    繰り返し発生したため、確定させるreport_designへここで自動補完を適用する
    （明示的に設定された値はそのまま維持する。詳細はautofill_prompt2_report_config
    のdocstring参照）。"""
    state = get_design_state(client, project_id)
    patch = {"pending_report_design": None, "pending_signals": None, "pending_queries": None}
    if state.get("pending_report_design") is not None:
        patch["report_design"] = state["pending_report_design"]
    if state.get("pending_signals") is not None:
        patch["signals"] = state["pending_signals"]
    if state.get("pending_queries") is not None:
        patch["queries"] = state["pending_queries"]

    effective_report_design = patch.get("report_design", state.get("report_design"))
    effective_signals = patch.get("signals", state.get("signals"))
    patch["report_design"] = provisioning.autofill_prompt2_report_config(
        effective_report_design, effective_signals)

    client.update("geo_report_design_state", {"project_id": f"eq.{project_id}"}, patch)
    return get_design_state(client, project_id)


def get_pinned_run_names(client, project_id: str) -> list:
    """このprojectでDesignがPinされているRun（narrative_master_asset_idが設定済み、
    isPinnedRunとロジックを揃える）の一覧を返す。Design Resetの事前チェック用
    ——geo_report_design_versions/geo_report_project_assetsはこれらのRunから
    FK参照されているため、先にRunを消さない限りResetはDB制約で失敗する。"""
    rows = client.select("geo_report_collection_runs", {
        "select": "run_id,run_name", "project_id": f"eq.{project_id}",
        "narrative_master_asset_id": "not.is.null",
    })
    return [r.get("run_name") or r["run_id"] for r in rows]


def reset_design(client, project_id: str) -> None:
    """このprojectのDesign関連データ（Draft State・Narrative Master/Report Template
    Asset・Published Design Version履歴・Design Chat会話ログ）を全て削除し、
    「まだ一度も設計していない」状態へ戻す（2026-09-14、ユーザー要望:
    「デザインを消したり、新規始める方法がない」）。Runは一切削除しない——
    Pinned Runが1件でも存在する場合はValueErrorを送出して拒否する（Design Version/
    Project AssetはRunからFK参照されており、Runを残したままではDB制約で削除できない。
    かつ、過去の実行結果を意図せず巻き込んで壊さないための安全装置でもある）。
    呼び出し元は先にget_pinned_run_namesでユーザーへ案内し、Runを削除してもらってから
    再度この関数を呼ぶ想定。"""
    pinned = get_pinned_run_names(client, project_id)
    if pinned:
        raise ValueError(
            "このDesignを参照しているRunが残っているため、Designをリセットできません。"
            f"先に次のRunを削除してください: {', '.join(pinned)}"
        )
    # design_versions→project_assetsの参照方向のため、versionsを先に削除する
    client.delete("geo_report_design_versions", {"project_id": f"eq.{project_id}"})
    client.delete("geo_report_project_assets", {"project_id": f"eq.{project_id}"})
    client.delete("geo_report_design_state", {"project_id": f"eq.{project_id}"})
    client.delete("geo_report_design_chat_turns", {"project_id": f"eq.{project_id}"})


# ---------------------------------------------------------------------------
# Atomic Publish（geo_report_publish_design RPCの薄いラッパー）
# ---------------------------------------------------------------------------

def _content_equal(a, b) -> bool:
    return (a or {}) == (b or {})


def publish_design(client, project_id: str, actor_id: str, *,
                    expert_adapter_type: str = "geo",
                    expert_knowledge_version: str = "geo_expert_knowledge_v1") -> dict:
    """現在のDraft（Narrative Master/Report Template）とDesign State
    （Report Design/Signals/Queries）をAtomicにPublishする。

    無変更Publish拒否はここ（Python側）で行う——RPC呼び出し前に、Narrative
    Master/Report Template/Report Design/Signals/Queriesの全てが直前のPublished
    スナップショットと同一なら例外を送出し、RPCを呼ばない（無駄なversionを作らない）。
    """
    narrative_draft = get_draft(client, project_id, "narrative_master")
    template_draft = get_draft(client, project_id, "report_template")
    if not narrative_draft:
        raise ValueError(
            "Narrative MasterのDraftがありません。Narrative Masterカードの「全Signal一括生成」"
            "ボタン、またはDesign Chatで「ナラティブマスターを作って」のように依頼して保存してください。"
        )
    if not template_draft:
        raise ValueError(
            "Report TemplateのDraftがありません。Report Templateカードの「AIで生成」で内容を作成し、"
            "生成完了後に「保存」ボタンを押してください。"
        )

    design_state = get_design_state(client, project_id)
    # 2026-09-15: primary_graph_signals/final_graph_ruleが未設定のままPublishされ、
    # Run作成→Aggregation実行で初めて「Calculating trendsが失敗します」と気づく
    # というユーザー報告が繰り返し発生したため、警告を出すだけでなくPublish時点で
    # 実際に安全な既定値（Core Signal全件）で自動補完してからRPCへ渡す。
    # ユーザー/Design ChatのAIが明示的に設定した値があればそのまま使う。
    design_state["report_design"] = provisioning.autofill_prompt2_report_config(
        design_state.get("report_design"), design_state.get("signals"))

    latest_versions = client.select("geo_report_design_versions", {
        "select": "*", "project_id": f"eq.{project_id}", "order": "version.desc", "limit": "1",
    })
    if latest_versions:
        latest = latest_versions[0]
        narrative_published = get_asset_by_id(client, latest["narrative_master_asset_id"])
        template_published = get_asset_by_id(client, latest["report_template_asset_id"])
        unchanged = (
            _content_equal(narrative_draft["content"], (narrative_published or {}).get("content"))
            and _content_equal(template_draft["content"], (template_published or {}).get("content"))
            and _content_equal(design_state.get("report_design"), latest.get("report_design"))
            and _content_equal(design_state.get("signals"), latest.get("signals"))
            and _content_equal(design_state.get("queries"), latest.get("queries"))
        )
        if unchanged:
            raise ValueError("前回Publishから変更がありません（Narrative Master/Report Template/"
                              "Report Design/Signals/Queriesのいずれも同一）。Publishをスキップしました。")

    result = client.rpc("geo_report_publish_design", {
        "p_project_id": project_id,
        "p_actor_id": actor_id,
        "p_narrative_master_content": narrative_draft["content"],
        "p_report_template_content": template_draft["content"],
        "p_report_design": design_state.get("report_design", {}),
        "p_signals": design_state.get("signals", []),
        "p_queries": design_state.get("queries", []),
        "p_expert_adapter_type": expert_adapter_type,
        "p_expert_knowledge_version": expert_knowledge_version,
    })
    # PostgRESTは単一行を返す関数(SETOFでない)の場合、単一のJSONオブジェクトを返す。
    # 一部フェイク/クライアント実装がlistで返す場合に備えて両方を許容する。
    if isinstance(result, list):
        result = result[0] if result else {}

    # Publishが成功した後、自動補完済みのreport_designをconfirmed working state
    # （geo_report_design_state）へも書き戻す。RPCへはこの値をそのまま渡しPublished
    # Versionには反映済みだが、working state自体を更新しないとDesign Chatで続けて
    # 対話した際にAIが「primary_graph_signalsがまだNone」と誤認し続けてしまう。
    client.update("geo_report_design_state", {"project_id": f"eq.{project_id}"},
                  {"report_design": design_state["report_design"]})

    # 2026-09-16追加: primary_graph_signals/common_primary_graph_startが未設定の
    # まま Publish されると、Run作成後のAggregation実行で初めて気づくことになり
    # （「Calculating trendsが失敗します」）、同種のユーザー報告が繰り返し発生した。
    # Publish結果にも警告を含める（geo_report_design_chat_service.fix_designの
    # report_config_warningsと同じロジック、FIX時点に続く二重チェック）。
    report_design = design_state.get("report_design") or {}
    valid_signal_ids = {s.get("signal_id") for s in (design_state.get("signals") or []) if s.get("signal_id")}
    # primary_graph_signalsは`or []`で正規化せず、report_designの値をそのまま渡す
    # （2026-09-15修正）。理由はgeo_report_design_chat_service.fix_designの
    # 同種コメント参照——意図的な空リストと未設定を区別するため。
    result["report_config_warnings"] = provisioning.validate_prompt2_report_config(
        {"final_graph_rule": report_design.get("final_graph_rule") or "default"},
        report_design.get("primary_graph_signals"),
        # 履歴の基準開始日（historical_trend_start_date）があれば、グラフ開始日の設定として扱う（2026-10-05）。
        # 実際のグラフ開始日はRun作成時に観測期間と組み合わせて決まる。
        report_design.get("common_primary_graph_start") or report_design.get("historical_trend_start_date"),
        valid_signal_ids,
    )
    return result
