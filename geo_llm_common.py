"""地政学インテリジェンス基盤: LLM構造化出力の共通ヘルパー。

sustainability_expert_common.call_llm_structured と同じ考え方（Azure OpenAIの
json_schema構造化出力を優先し、失敗時は手動JSON抽出+Schema検証+1回だけ修正再実行に
フォールバックする）を、地政学ドメイン用に自己完結で実装する。
サステナビリティ側の expert_runs ロギング等ドメイン固有部分は含まない
（既存の sustainability_expert_common.py は一切importも改変もしない）。
"""

import json
import random
import re
import time

import jsonschema
import openai

# Report Studio Current Pipelineでバッチ内LLM呼び出しを並列化した際（2026-09-11）、
# Azure OpenAIデプロイのレート制限(429 rate_limit_exceeded)に実データで即座に到達する
# ことを実機確認した。従来は呼び出し元(例: geo_report_prompt1_classification_service)
# ごとに個別リトライを持たない設計だったため、429は即座に呼び出し元へ例外伝播していた。
# ここで一括してリトライ+指数バックオフを扱うことで、並列度を問わず地政学基盤全体の
# LLM呼び出しがレート制限を自動的に乗り越えられるようにする（既存の呼び出し元コードは
# 無改変で恩恵を受ける）。
_RATE_LIMIT_MAX_RETRIES = 5
_RATE_LIMIT_BASE_DELAY_SECONDS = 5.0

# v1.2でSchemaが大きく複雑になったこと（Key Actor Profile等の深い入れ子構造）に伴い、
# 実データ検証で出力が最大トークン上限に達して途中で切れ、JSON構文エラー
# （"Expecting ',' delimiter"等）になるケースが確認された（実測: 正常完了時で
# 約13,800 completion tokens）。明示的な上限を指定しない場合のデプロイ既定値では
# 不足することがあるため、十分な余裕を持たせた値を明示する。
_MAX_COMPLETION_TOKENS = 20000


class GeoLLMError(Exception):
    """Schema検証が最終的に失敗した場合、またはLLM呼び出し自体（timeout/connection error等含む）
    が失敗した場合。2026-09-18発見: 以前はmanual JSON fallback/訂正再試行のAzure呼び出しが
    try/exceptで保護されておらず、httpx.ReadTimeout/openai.APITimeoutErrorが未処理のまま
    Production pathへ漏れてrun_expert_request_stagedをクラッシュさせていた（Actor Intelligence
    実データ投入でCall1の生成量が増え、大型SchemaのAzure呼び出しが180秒のclient timeoutへ
    近づいたことで初めて露出した）。本Exceptionは「GeoLLMError化＝自動リトライされる」ことを
    意味しない——call_llm_structuredの外側ループは429(RateLimitError)のみをバックオフ
    リトライする。timeoutをリトライするかどうかは呼び出し元（例: run_expert_request_stagedの
    stage1_recovery）が個別に判断する。

    attempts: この呼び出し内で実際に行われたAzure呼び出しごとの記録（telemetry用）。
    exception_type: 最終的に失敗の原因となった例外のクラス名（Schema検証失敗のみの場合も
    "JSONDecodeError"/"ValidationError"等を入れる、原因不明な場合のみNone）。"""

    def __init__(self, message, *, attempts: list | None = None, exception_type: str | None = None):
        super().__init__(message)
        self.attempts = attempts if attempts is not None else []
        self.exception_type = exception_type


def _resolve_schema_ref(node, root_schema: dict) -> dict:
    if isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref.startswith("#/definitions/"):
            return root_schema.get("definitions", {}).get(ref[len("#/definitions/"):], {})
    return node if isinstance(node, dict) else {}


def _flatten_string_dict_list(value: list, join_with: str) -> str | None:
    """[{"name": "A", "description": "B"}, ...] のように、値がすべて文字列のdictの配列を
    「A：B」を1行ずつ並べた1本の文字列へ変換する。文字列以外の値を含む場合はNone（変換しない）。
    複数のシナリオを項目ごとに返された場合など、type=stringの項目へ入ってしまった配列用。"""
    lines = []
    for item in value:
        if not isinstance(item, dict) or not item or not all(isinstance(v, str) for v in item.values()):
            return None
        lines.append("：".join(v.strip() for v in item.values() if v.strip()))
    return join_with.join(lines)


def _coerce_string_arrays_to_strings(data, schema_node, root_schema: dict, *, join_with: str = "\n") -> None:
    """既知の頑健性ギャップへの対応: type=stringのフィールドへLLMが文字列配列を返す
    ことがある（例: weakening_information）。Schema検証前に、string配列を1本の文字列へ
    結合するだけの決定的な正規化を行う（意味内容は変更しない、他の型不一致は対象外）。
    dataをin-placeで書き換える。"""
    node = _resolve_schema_ref(schema_node, root_schema)
    node_type = node.get("type")
    if isinstance(data, dict) and node_type == "object":
        props = node.get("properties", {})
        for key, value in list(data.items()):
            prop_schema = props.get(key)
            if prop_schema is None:
                continue
            resolved = _resolve_schema_ref(prop_schema, root_schema)
            allowed = resolved.get("type")
            allowed_types = allowed if isinstance(allowed, list) else [allowed]
            if "string" in allowed_types and isinstance(value, list) and value \
                    and all(isinstance(v, str) for v in value):
                data[key] = join_with.join(value)
            elif "string" in allowed_types and isinstance(value, list) and value \
                    and _flatten_string_dict_list(value, join_with) is not None:
                data[key] = _flatten_string_dict_list(value, join_with)
            else:
                _coerce_string_arrays_to_strings(value, prop_schema, root_schema, join_with=join_with)
    elif isinstance(data, list) and node_type == "array":
        items_schema = node.get("items")
        if items_schema:
            for item in data:
                _coerce_string_arrays_to_strings(item, items_schema, root_schema, join_with=join_with)


def _extract_json_object(text: str) -> dict:
    """出力からJSONオブジェクト1個を取り出す。単純なfind/rfindだと、モデルがJSONの後に
    説明文（末尾が偶然"}"を含む等）を続けた場合に「Extra data」で失敗するため、
    最初の"{"から中括弧の対応を数えて閉じるところまでを1個のオブジェクトとして取り出す"""
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)

    start = text.find("{")
    if start == -1:
        return json.loads(text)

    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    return json.loads(text[start:])


def call_llm_structured(client, model: str, system_prompt: str, user_prompt: str,
                         schema: dict, schema_name: str, *, max_attempts: int = 3,
                         strict: bool = False, timeout: float | None = None) -> dict:
    """_call_llm_structured_onceを、レート制限(429)発生時のみ指数バックオフで
    リトライするラッパー。それ以外の例外（GeoLLMError含む）はそのまま呼び出し元へ伝播する
    ——「GeoLLMErrorが呼び出し元に届く＝ここで自動リトライされた」ではない点に注意
    （既存の戻り値の形は変えない。ただし実際に何回リトライしたかを`rate_limit_retries`
    として追加する——2026-09-11、Prompt1バッチ実行の実測分析のため。既存の呼び出し元は
    このキーを無視するだけで動作に影響しない）。

    timeout: 2026-09-18追加（ユーザー指示、call-level override）。未指定時はai_client.py側の
    client既定timeout（_HTTP_CLIENT_TIMEOUT）をそのまま使う。Stage1/Stage2のような大型Schema
    呼び出しにだけ長めの値を渡せるようにする——全システム共通のclient既定値は変更しない。"""
    for retry in range(_RATE_LIMIT_MAX_RETRIES + 1):
        try:
            result = _call_llm_structured_once(
                client, model, system_prompt, user_prompt, schema, schema_name,
                max_attempts=max_attempts, strict=strict, timeout=timeout)
            result["rate_limit_retries"] = retry
            return result
        except openai.RateLimitError:
            if retry >= _RATE_LIMIT_MAX_RETRIES:
                raise
            delay = _RATE_LIMIT_BASE_DELAY_SECONDS * (2 ** retry) + random.uniform(0, 2)
            time.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _call_llm_structured_once(client, model: str, system_prompt: str, user_prompt: str,
                                schema: dict, schema_name: str, *, max_attempts: int = 3,
                                strict: bool = False, timeout: float | None = None) -> dict:
    """schemaに準拠したJSONを1個取得する。戻り値: {"data": dict, "mode": str, "usage": dict|None,
    "attempts": list} — attemptsは実際に行われたAzure呼び出しごとの記録（telemetry用、
    call_llm_structuredの docstring参照）。

    max_attempts: 実際のAzure OpenAI API呼び出し上限（既定3=既存の全呼び出し元と同じ動作:
    ①構造化出力試行 ②手動JSON抽出フォールバック ③Schema訂正メッセージ付き再試行）。
    地政学専門家AI v1.2のQuality Guard修復呼び出し等、1リクエストあたりの総呼び出し回数を
    明示的に抑えたい場合のみ2以下を渡す（例: 通常生成はmax_attempts=2、修復生成は
    max_attempts=1）。1に満たない値は指定しないこと（少なくとも1回は試行する）。

    strict: Trueの場合、①構造化出力試行でAzure/OpenAIのStrict Structured Output
    （schemaを厳密に強制するモード）を使う。段階実行(Staged Execution) Phase Aの
    Stage1/Stage2 Schema（geo_expert_schema.build_stage1_schema等、全properties required
    ＋additionalProperties=falseで構築済み）向け。既定Falseは既存の全呼び出し元と同じ動作
    （strictはAzureにとって単なるヒントで、構造を強制しない）を維持する。

    timeout: call-level override（未指定時はclient既定のtimeoutを使う）。

    2026-09-18修正: 以前はここの②③（手動JSON抽出・訂正再試行）のAzure呼び出しがtry/except
    で保護されておらず、httpx.ReadTimeout/openai.APITimeoutError等が未処理のまま呼び出し元へ
    伝播し、Production pathをクラッシュさせていた。①と同じ例外正規化を②③にも適用し、
    どの段でも最終的な失敗は必ずGeoLLMError（+ attempts + exception_type）として返す
    ——生のhttpx/openai例外がここから漏れないことを保証する。ただし②③はcorrectionの
    ために「前回の出力」を必要とするため、Azure呼び出し自体が失敗（transport-level、
    レスポンスが無い）した場合は、それ以上のフォールバック段を試さずその場でGeoLLMErrorに
    する（意味のない追加コールを増やさない——リトライ回数を明示的に抑える）。
    """
    started = time.monotonic()
    attempts: list = []

    def _usage_from(resp) -> dict | None:
        u = getattr(resp, "usage", None)
        if u is None:
            return None
        # prompt_tokens_details.cached_tokens: Azure/OpenAI Prompt Cachingが実際に
        # ヒットしたかを確認するため2026-09-12追加（同一system promptを繰り返し送信する
        # バッチ呼び出しで、共通部分がキャッシュされコスト・latencyが軽減されているか
        # 実測するのが目的）。
        details = getattr(u, "prompt_tokens_details", None)
        cached_tokens = getattr(details, "cached_tokens", None) if details is not None else None
        return {"prompt_tokens": getattr(u, "prompt_tokens", None),
                "completion_tokens": getattr(u, "completion_tokens", None),
                "total_tokens": getattr(u, "total_tokens", None),
                "cached_tokens": cached_tokens}

    def _call_azure(messages: list, *, response_format: dict | None, attempt_label: str):
        """1回のAzure呼び出しを行い、成否をattemptsへ記録する。戻り値: (resp, exc)——
        成功時はexc=None、失敗時はresp=None。openai.RateLimitErrorは呼び出し元
        call_llm_structuredのバックオフリトライに委ねるため、ここでは記録せずそのまま
        再raiseする（既存動作を維持——429はfallbackへ進んでも同じ理由で即座にまた429に
        なるだけと実機確認済み）。"""
        attempt_started = time.monotonic()
        kwargs = dict(model=model, messages=messages, temperature=0.2,
                      max_completion_tokens=_MAX_COMPLETION_TOKENS)
        if response_format is not None:
            kwargs["response_format"] = response_format
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            resp = client.chat.completions.create(**kwargs)
        except openai.RateLimitError:
            raise
        except Exception as exc:  # noqa: BLE001 — timeout/connection error等を分類・記録する
            attempts.append({
                "attempt_label": attempt_label, "attempt_number": len(attempts) + 1,
                "outcome": "error", "exception_type": type(exc).__name__,
                "elapsed_ms": int((time.monotonic() - attempt_started) * 1000),
                "timeout_value": timeout,
            })
            return None, exc
        attempts.append({
            "attempt_label": attempt_label, "attempt_number": len(attempts) + 1,
            "outcome": "success", "exception_type": None,
            "elapsed_ms": int((time.monotonic() - attempt_started) * 1000),
            "timeout_value": timeout,
        })
        return resp, None

    def _mark_last_attempt_validation_failed(exc: Exception, resp=None) -> None:
        """直前のAzure呼び出し自体は成功したが、JSON parse/Schema検証で失敗した場合に
        attempts末尾のoutcomeを訂正する（Azure呼び出しは成功、後続の検証が失敗という
        ことをtelemetryへ正しく残す）。

        Phase 7-1C-R2(Stage2 Recovery、ユーザー指示「Raw Failure Capture」): 失敗時のみ、
        原因分析に必要な生レスポンス診断情報（finish_reason/raw content length・head・tail/
        usage/parse error位置）を追加する。成功時のattempts（このブロックは通らない）には
        このフィールド群を一切増やさない——通常成功runのログ量を増やさないため
        （ユーザー指示「通常成功runで不要な大量ログを増やさない」）。"""
        if not attempts:
            return
        entry = {**attempts[-1], "outcome": "validation_failed", "exception_type": type(exc).__name__}
        if resp is not None:
            try:
                choice = resp.choices[0]
                raw_content = choice.message.content or ""
                entry["finish_reason"] = choice.finish_reason
                entry["raw_content_length"] = len(raw_content)
                entry["raw_content_head"] = raw_content[:300]
                entry["raw_content_tail"] = raw_content[-800:]
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    entry["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
                    entry["completion_tokens"] = getattr(usage, "completion_tokens", None)
                    entry["total_tokens"] = getattr(usage, "total_tokens", None)
                entry["max_output_tokens"] = _MAX_COMPLETION_TOKENS
            except Exception:  # noqa: BLE001 — 診断情報の収集自体で本処理を止めない(fail open)
                pass
        if isinstance(exc, json.JSONDecodeError):
            entry["parse_error"] = {"msg": exc.msg, "pos": exc.pos, "lineno": exc.lineno, "colno": exc.colno}
        elif isinstance(exc, jsonschema.ValidationError):
            entry["schema_validation_error"] = str(exc)[:500]
        attempts[-1] = entry

    # ---- ①構造化出力試行 ----
    resp, exc = _call_azure(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        response_format={"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": strict}},
        attempt_label="structured")
    if resp is not None:
        try:
            data = json.loads(resp.choices[0].message.content)
            try:
                _coerce_string_arrays_to_strings(data, schema, schema)
            except Exception:  # noqa: BLE001 — 正規化自体の失敗で検証を妨げない（fail open）
                pass
            jsonschema.validate(data, schema)
            return {"data": data, "mode": "structured_output", "usage": _usage_from(resp),
                    "latency_ms": int((time.monotonic() - started) * 1000), "attempts": attempts}
        except Exception as validation_exc:  # noqa: BLE001 — 構造化出力が未対応・Schema検証エラー含む
            _mark_last_attempt_validation_failed(validation_exc, resp=resp)
            exc = validation_exc
    last_exception_type = type(exc).__name__ if exc is not None else None

    if max_attempts < 2:
        raise GeoLLMError(
            "構造化出力に失敗し、max_attempts上限のため手動JSON抽出フォールバックを行いません。"
            f"（原因: {last_exception_type}）", attempts=attempts, exception_type=last_exception_type)

    # v1.2でSchemaが大きく複雑になったことにより、structured output(strict=False)が
    # トップレベル必須項目を入れ子構造の中へ誤って埋め込むケースが実データ検証で確認された
    # （例: main_scenario内にclaims/stakeholders/strongest_counterargument等を誤って
    # ネストする）。手動JSON fallbackでは元々response_formatによるSchema強制が効かないため、
    # 最低限トップレベル必須キー名を明示することで改善する（Schema全文は埋め込まない簡易対策）。
    required_fields = schema.get("required") or []
    required_hint = (
        f"\n\n出力するJSON1個は、次のキーを最上位（トップレベル、入れ子にしない）に"
        f"直接含めること: {', '.join(required_fields)}。"
    ) if required_fields else ""

    messages = [
        {"role": "system", "content": system_prompt + "\n\n必ずJSON1個のみを出力すること。説明文・Markdown装飾は不要。" + required_hint},
        {"role": "user", "content": user_prompt},
    ]

    # ---- ②手動JSON抽出フォールバック ----
    resp, exc = _call_azure(messages, response_format=None, attempt_label="manual_json")
    if resp is None:
        # Azure呼び出し自体が失敗（transport-level）——訂正に使う「前回の出力」が無いため
        # ③（Schema訂正再試行）は意味を持たない。ここでGeoLLMErrorとして確定させる
        # （生のhttpx/openai例外をここから漏らさない、これが今回の修正の核心）。
        raise GeoLLMError(
            f"手動JSON抽出のAzure呼び出し自体が失敗しました: {type(exc).__name__}: {exc}",
            attempts=attempts, exception_type=type(exc).__name__)

    raw = resp.choices[0].message.content
    try:
        data = _extract_json_object(raw)
        try:
            _coerce_string_arrays_to_strings(data, schema, schema)
        except Exception:  # noqa: BLE001 — 正規化自体の失敗で検証を妨げない（fail open）
            pass
        jsonschema.validate(data, schema)
        return {"data": data, "mode": "manual_json", "usage": _usage_from(resp),
                "latency_ms": int((time.monotonic() - started) * 1000), "attempts": attempts}
    except (json.JSONDecodeError, jsonschema.ValidationError) as e:
        # Python 3はexcept ... as eのeをブロック終了時に自動delするため、ブロックを
        # 出た後で使う値（str(e)/型名）はここで変数へ保存しておく必要がある。
        _mark_last_attempt_validation_failed(e, resp=resp)
        first_error = str(e)
        first_error_type = type(e).__name__

    if max_attempts < 3:
        raise GeoLLMError(
            f"手動JSON抽出がSchema検証に失敗し、max_attempts上限のため訂正再試行を行いません: {first_error}",
            attempts=attempts, exception_type=first_error_type)

    messages.append({"role": "assistant", "content": raw})
    messages.append({
        "role": "user",
        "content": f"前回の出力はJSON Schema検証に失敗しました: {first_error}\n"
                    f"Schemaに厳密に従うJSON1個のみを再出力してください。",
    })

    # ---- ③Schema訂正メッセージ付き再試行 ----
    resp2, exc2 = _call_azure(messages, response_format=None, attempt_label="manual_json_retry")
    if resp2 is None:
        raise GeoLLMError(
            f"Schema訂正再試行のAzure呼び出し自体が失敗しました: {type(exc2).__name__}: {exc2}",
            attempts=attempts, exception_type=type(exc2).__name__)

    raw2 = resp2.choices[0].message.content
    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        data2 = _extract_json_object(raw2)
        try:
            _coerce_string_arrays_to_strings(data2, schema, schema)
        except Exception:  # noqa: BLE001 — 正規化自体の失敗で検証を妨げない（fail open）
            pass
        jsonschema.validate(data2, schema)
        return {"data": data2, "mode": "manual_json_retry", "usage": _usage_from(resp2),
                "latency_ms": latency_ms, "attempts": attempts}
    except (json.JSONDecodeError, jsonschema.ValidationError) as e2:
        _mark_last_attempt_validation_failed(e2, resp=resp2)
        raise GeoLLMError(f"Schema検証が2回とも失敗しました: {e2}",
                           attempts=attempts, exception_type=type(e2).__name__) from e2


def reconcile_batch_llm_results(input_ids: list, raw_results: list, item_schema: dict, *,
                                  id_field: str = "cluster_id") -> dict:
    """複数アイテムを1回のLLM呼び出しでまとめて処理する「バッチ呼び出し」の結果を検証する。

    バッチ呼び出しの戻り値自体のschema（call_llm_structuredへ渡すschema）はあえて緩く
    しておき（id_fieldのみ必須、他は自由形式）、Azure/OpenAI側の構造化出力検証で
    バッチ全体が失敗しないようにする。本関数がPython側で「各アイテムを個別に」
    item_schemaへ照合し、1〜2件だけ壊れている場合でも他の正常なアイテムを失わずに
    済むようにする（Batch全体を無駄に再送しないため）。

    戻り値: {
        "valid": {id: item(id_field除去済み)},  # item_schemaに合格したアイテムのみ
        "missing": [...],     # input_idsにあったが結果に無かったid
        "duplicate": [...],   # 結果内で同じidが複数回出現した（2回目以降）
        "unknown": [...],     # 結果にあったがinput_idsに無い（LLMが捏造したid）
        "invalid": [...],     # idはinput_idsに存在するが、item_schema検証に失敗した
    }
    missing/duplicate/unknown/invalidのいずれかに該当したidは呼び出し元が
    アイテム単位で再試行の対象にする（バッチ全体はリトライしない）。
    """
    input_id_set = set(input_ids)
    seen: set = set()
    duplicate: list = []
    unknown: list = []
    invalid: list = []
    valid: dict = {}

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        cid = item.get(id_field)
        if cid is None:
            continue
        if cid not in input_id_set:
            unknown.append(cid)
            continue
        if cid in seen:
            duplicate.append(cid)
            continue
        seen.add(cid)
        item_body = {k: v for k, v in item.items() if k != id_field}
        try:
            jsonschema.validate(item_body, item_schema)
        except jsonschema.ValidationError:
            invalid.append(cid)
            continue
        valid[cid] = item_body

    missing = [cid for cid in input_ids if cid not in seen]
    return {"valid": valid, "missing": missing, "duplicate": duplicate, "unknown": unknown, "invalid": invalid}
