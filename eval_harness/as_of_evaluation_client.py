# -*- coding: utf-8 -*-
"""Phase I-B0.1: As-Of Evaluation Harness.

AsOfEvaluationClient is a READ-ONLY, evaluation-only proxy that wraps a real
geo_db_client.SupabaseClient and enforces "information available to the system
at T0" semantics on every .select() call, before delegating to the real client.

THIS IS NOT PRODUCTION CODE. It exists only so that a Retrospective Proxy
Evaluation (Phase I-B0) can re-run the REAL, unmodified production pipeline
(geo_expert_engine.py, geo_ios_expert_ai_adapter.py, etc.) against a
temporally-restricted view of the database, without touching a single line of
production retrieval/generation logic. The production code is handed this
proxy object in place of the real client; it never imports or references this
module.

Why "published_at <= T0" alone is not enough: a document could have been
published before T0 but not yet crawled/ingested into this system's DB until
much later -- if the system did not have the information by T0, Expert AI
could not have used it at T0 either, regardless of the source's own nominal
publish date. The policy below requires BOTH the subject-matter date AND the
system-ingestion date to be <= T0 wherever both are available.

Fail-closed default: any table with no explicit policy returns zero rows and
is recorded as UNDEFINED_POLICY_FAIL_CLOSED in the audit log, rather than
silently passing rows through unfiltered.
"""
from datetime import date, datetime


class WriteBlockedError(PermissionError):
    """Raised by any write-shaped operation on AsOfEvaluationClient."""


def _to_date(value) -> date | None:
    """Best-effort parse of a date/datetime-shaped value (str or date/datetime) to a date.
    Returns None if unparseable or falsy -- callers treat None as "unknown", never as
    "in the past" (unknown dates are handled per-policy, usually by exclusion)."""
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


class TableAudit:
    __slots__ = ("policy", "included", "excluded_future", "excluded_unverified")

    def __init__(self, policy: str):
        self.policy = policy
        self.included = 0
        self.excluded_future = 0
        self.excluded_unverified = 0

    def as_dict(self) -> dict:
        return {"policy": self.policy, "included_count": self.included,
                "excluded_future_count": self.excluded_future,
                "excluded_unverified_count": self.excluded_unverified}


def _policy_geo_documents(rows: list, as_of: date) -> tuple:
    """published_at <= T0 AND (fetched_at or created_at) <= T0.
    published_at missing -> excluded_unverified (safe-side: we cannot confirm the
    underlying source's real-world timing without it, and fetched_at alone only tells
    us when *this system* saw it, not whether the content itself predates T0)."""
    kept, fut, unverified = [], 0, 0
    for r in rows:
        pub = _to_date(r.get("published_at"))
        ingested = _to_date(r.get("fetched_at")) or _to_date(r.get("created_at"))
        if pub is None:
            unverified += 1
            continue
        if pub > as_of or (ingested is not None and ingested > as_of):
            fut += 1
            continue
        if ingested is None:
            unverified += 1
            continue
        kept.append(r)
    return kept, fut, unverified


def _policy_geo_events(rows: list, as_of: date) -> tuple:
    """start_at <= T0 (occurred_at/event_date equivalent in this schema) AND created_at <= T0
    (system ingestion). Missing start_at -> excluded_unverified."""
    kept, fut, unverified = [], 0, 0
    for r in rows:
        occurred = _to_date(r.get("start_at"))
        ingested = _to_date(r.get("created_at"))
        if occurred is None:
            unverified += 1
            continue
        if occurred > as_of or (ingested is not None and ingested > as_of):
            fut += 1
            continue
        if ingested is None:
            unverified += 1
            continue
        kept.append(r)
    return kept, fut, unverified


def _policy_geo_knowledge_items(rows: list, as_of: date) -> tuple:
    """created_at <= T0 is the primary gate (was this knowledge already in the system at
    all at T0? -- deliberately NOT as_of_date, which describes the subject matter's period,
    not when the system ingested it). Additionally valid_from/valid_to must bracket T0 when
    present. status/retrieval_status constraints from the caller's own query are preserved
    unchanged (this policy only ADDS the temporal constraint, never removes the caller's
    existing filters)."""
    kept, fut, unverified = [], 0, 0
    for r in rows:
        created = _to_date(r.get("created_at"))
        if created is None:
            unverified += 1
            continue
        if created > as_of:
            fut += 1
            continue
        valid_from = _to_date(r.get("valid_from"))
        valid_to = _to_date(r.get("valid_to"))
        if valid_from is not None and valid_from > as_of:
            fut += 1
            continue
        if valid_to is not None and valid_to <= as_of:
            fut += 1
            continue
        kept.append(r)
    return kept, fut, unverified


def _policy_geo_relationships(rows: list, as_of: date) -> tuple:
    """Same shape as geo_knowledge_items: created_at <= T0, valid_from/valid_to bracket T0."""
    return _policy_geo_knowledge_items(rows, as_of)


def _policy_geo_entities(rows: list, as_of: date) -> tuple:
    """Existence-window check only (active_from/active_to). Entity IDENTITY fields
    (canonical_name, entity_type, country_code, etc.) are treated as stable per the
    Phase I-B0.1 spec's priority-2 rule ("stable identity field only -> allow"). The
    mutable `status` field is NOT filtered here (this table has no historical
    versioning of status) -- callers of this harness must treat `status` on any
    geo_entities row as temporal_unverified, documented as a limitation, not silently
    trusted as "the T0 status". created_at is NOT used as the gate here (an entity's
    identity record existing today doesn't mean it didn't exist at T0 too -- countries/
    organizations are not created by this system, they are catalogued)."""
    kept, fut, unverified = [], 0, 0
    for r in rows:
        active_from = _to_date(r.get("active_from"))
        active_to = _to_date(r.get("active_to"))
        if active_from is not None and active_from > as_of:
            fut += 1
            continue
        if active_to is not None and active_to <= as_of:
            fut += 1
            continue
        kept.append(r)
    return kept, fut, unverified


def _policy_created_at_only(rows: list, as_of: date) -> tuple:
    """Generic fallback for linking/definitional tables (geo_evidence, geo_claim_evidence,
    geo_knowledge_item_claims, geo_source_registry, geo_indicator_definitions, geo_cases,
    geo_case_issues, geo_themes): created_at <= T0. Low individual leakage risk (mostly
    structural/definitional, not analytical content) but still gated for consistency
    with the fail-closed default posture."""
    kept, fut, unverified = [], 0, 0
    for r in rows:
        created = _to_date(r.get("created_at"))
        if created is None:
            unverified += 1
            continue
        if created > as_of:
            fut += 1
            continue
        kept.append(r)
    return kept, fut, unverified


def _policy_geo_observations(rows: list, as_of: date) -> tuple:
    """as_of (the metric's own observation period) AND created_at (when computed), both <= T0."""
    kept, fut, unverified = [], 0, 0
    for r in rows:
        obs_as_of = _to_date(r.get("as_of"))
        created = _to_date(r.get("created_at"))
        if obs_as_of is None:
            unverified += 1
            continue
        if obs_as_of > as_of or (created is not None and created > as_of):
            fut += 1
            continue
        if created is None:
            unverified += 1
            continue
        kept.append(r)
    return kept, fut, unverified


def _policy_passthrough_no_filter(rows: list, as_of: date) -> tuple:
    """No temporal filter at all. Reserved for tables that are the evaluation RUN's own
    bookkeeping (IOS session/candidate/design rows, Expert AI request/response logs) --
    these are not sources of world knowledge that could leak a future outcome; they are
    freshly created *by this very run*, today, about the T0 theme. Filtering them by T0
    would be a category error (their created_at is today, 2026-09-16, not part of the T0
    world-state being reconstructed) and would simply break the pipeline's own internal
    bookkeeping reads, as this harness's first BC Strategy run discovered (session/
    candidate rows it had just written became invisible to _require_session's own
    read-back). Everything actually gated for world-knowledge leakage is in the policies
    above this line."""
    return list(rows), 0, 0


# IOS's own operational/bookkeeping tables (this evaluation run's session, scenario
# candidates, design versions, and the underlying Expert AI request/response log rows it
# reads back). Explicitly listed rather than pattern-matched, so a new geo_ios_* table
# added later defaults to fail-closed (Section 6) until someone deliberately reviews it
# and adds it here.
_RUN_BOOKKEEPING_TABLES = (
    "geo_ios_analysis_sessions", "geo_ios_scenario_candidates", "geo_ios_analysis_designs",
    "geo_expert_requests", "geo_expert_responses",
)

TABLE_POLICIES = {
    **{t: _policy_passthrough_no_filter for t in _RUN_BOOKKEEPING_TABLES},
    "geo_documents": _policy_geo_documents,
    "geo_events": _policy_geo_events,
    "geo_knowledge_items": _policy_geo_knowledge_items,
    "geo_relationships": _policy_geo_relationships,
    "geo_entities": _policy_geo_entities,
    "geo_observations": _policy_geo_observations,
    "geo_evidence": _policy_created_at_only,
    "geo_claim_evidence": _policy_created_at_only,
    "geo_knowledge_item_claims": _policy_created_at_only,
    "geo_source_registry": _policy_created_at_only,
    "geo_indicator_definitions": _policy_created_at_only,
    "geo_cases": _policy_created_at_only,
    "geo_case_issues": _policy_created_at_only,
    "geo_themes": _policy_created_at_only,
}

# Tables known to hold ONLY current-state / no-history analytical fields that this harness
# cannot safely reconstruct as-of a past date at all (Phase I-B0.1 Section 9 inventory).
# These are not queried directly as their own table by the retrieval paths audited so far
# (their "current" fields live inside geo_knowledge_items.structured_content, e.g.
# current_posture / latest media perception) -- listed here for documentation, not code.
UNSUPPORTED_TEMPORAL_FIELDS = [
    {"field": "geo_entities.status", "classification": "temporal_unverified",
     "reason": "No historical versioning of entity status; only active_from/active_to (existence window) is filterable."},
    {"field": "geo_knowledge_items(actor_profile).structured_content.current_posture", "classification": "safe",
     "reason": "Lives inside a geo_knowledge_items row; gated correctly by that table's created_at<=T0 policy IF the profile is fully re-versioned (new row + supersedes_id) on update. NOT verified whether current_posture can be updated in-place without a version bump -- flagged as a residual assumption, not a confirmed guarantee."},
    {"field": "geo_knowledge_items(media_profile).structured_content.latest_perception ('latest media perception')",
     "classification": "temporal_unverified",
     "reason": "Same mechanism and same unverified in-place-update assumption as current_posture, plus not independently audited in this pass (media pack builder was not traced)."},
]


# ---------------------------------------------------------------------------
# policy_mode="content_date"（2026-10-15追加、既定の"strict"は変更なし）
#
# 「過去の事例の分析に、それより後の情報を使わない」という本旨は、情報の“内容の日付”（記事の公開日・出来事の発生日・
# 知識が述べている出来事の時期）が基準日以前であること。strictモードはそれに加えて「DBへ保存した日」も基準日以前を要求する
# （＝基準日時点でシステムが実際に持っていたか）。基盤を後から整備して過去の事例を評価し直す場合、strictでは全ての資料が
# 「未来」になるため、content_dateモードで「内容が基準日以前の資料だけ」を使う。内容の日付が分からないものは使わない
# （fail-closed）。年だけ分かる場合は、その年の12/31（＝最も遅い可能性）を内容の日付として扱い、基準日より後に
# 公表されたものを誤って含めない側に倒す。
# ---------------------------------------------------------------------------
import re as _re

_YEAR_RX = _re.compile(r"(?<![0-9])(19[5-9][0-9]|20[0-3][0-9])(?![0-9])")
_PRECEDENT_DATE_KEYS = ("period", "triggering_event", "outcome", "question_examined", "precedent_title")


def _policy_geo_documents_content(rows: list, as_of: date) -> tuple:
    """published_at <= T0 のみ（保存日は問わない）。published_atが無いものはexcluded_unverified。"""
    kept, fut, unverified = [], 0, 0
    for r in rows:
        pub = _to_date(r.get("published_at"))
        if pub is None:
            unverified += 1
        elif pub > as_of:
            fut += 1
        else:
            kept.append(r)
    return kept, fut, unverified


def _policy_geo_events_content(rows: list, as_of: date) -> tuple:
    """start_at <= T0 のみ（保存日は問わない）。"""
    kept, fut, unverified = [], 0, 0
    for r in rows:
        occurred = _to_date(r.get("start_at"))
        if occurred is None:
            unverified += 1
        elif occurred > as_of:
            fut += 1
        else:
            kept.append(r)
    return kept, fut, unverified


def knowledge_content_date(item: dict) -> date | None:
    """先例(precedent)が述べている出来事の時期の上限日。title/period/triggering_event/outcome等に現れる年の最大値の
    12/31。年が読み取れない・先例以外の種類は None（=使わない）。人物プロファイル等は「現在の姿」を含み、
    内容の日付を一意に決められないため対象外。"""
    if item.get("knowledge_type") != "precedent":
        return None
    sc = item.get("structured_content") or {}
    text = " ".join([str(item.get("title") or "")] + [str(sc.get(k) or "") for k in _PRECEDENT_DATE_KEYS])
    years = [int(y) for y in _YEAR_RX.findall(text)]
    today_year = date.today().year
    years = [y for y in years if y <= today_year]
    return date(max(years), 12, 31) if years else None


def _policy_geo_knowledge_items_content(rows: list, as_of: date) -> tuple:
    """先例のみ、内容の日付(knowledge_content_date) <= T0。valid_from/valid_toがあれば基準日を含むことも要求する。"""
    kept, fut, unverified = [], 0, 0
    for r in rows:
        content = knowledge_content_date(r)
        if content is None:
            unverified += 1
            continue
        if content > as_of:
            fut += 1
            continue
        valid_from, valid_to = _to_date(r.get("valid_from")), _to_date(r.get("valid_to"))
        if (valid_from is not None and valid_from > as_of) or (valid_to is not None and valid_to <= as_of):
            fut += 1
            continue
        kept.append(r)
    return kept, fut, unverified


def _policy_geo_relationships_content(rows: list, as_of: date) -> tuple:
    """valid_from <= T0 かつ T0 < valid_to（登録日は問わない）。valid_fromが無いものは使わない。"""
    kept, fut, unverified = [], 0, 0
    for r in rows:
        valid_from, valid_to = _to_date(r.get("valid_from")), _to_date(r.get("valid_to"))
        if valid_from is None:
            unverified += 1
        elif valid_from > as_of or (valid_to is not None and valid_to <= as_of):
            fut += 1
        else:
            kept.append(r)
    return kept, fut, unverified


TABLE_POLICIES_CONTENT_DATE = {
    **TABLE_POLICIES,
    "geo_documents": _policy_geo_documents_content,
    "geo_events": _policy_geo_events_content,
    "geo_knowledge_items": _policy_geo_knowledge_items_content,
    "geo_relationships": _policy_geo_relationships_content,
}
POLICY_MODES = {"strict": TABLE_POLICIES, "content_date": TABLE_POLICIES_CONTENT_DATE}


class AsOfEvaluationClient:
    """Read-only, as-of-date proxy for geo_db_client.SupabaseClient.

    Usage: pass an instance of this class wherever production code expects a
    SupabaseClient-shaped `client` argument (e.g. geo_ios_expert_ai_adapter functions).
    Only .select() is implemented; every write-shaped method raises WriteBlockedError.
    """

    def __init__(self, real_client, as_of_date: str, *, allow_write_passthrough: bool = False,
                 policy_mode: str = "strict"):
        if policy_mode not in POLICY_MODES:
            raise ValueError(f"policy_mode must be one of {sorted(POLICY_MODES)}, got: {policy_mode!r}")
        self.policy_mode = policy_mode
        self._policies = POLICY_MODES[policy_mode]
        self._client = real_client
        self.as_of = _to_date(as_of_date)
        if self.as_of is None:
            raise ValueError(f"as_of_date must be a valid ISO date, got: {as_of_date!r}")
        self._audit: dict[str, TableAudit] = {}
        # generously over-fetch before post-filtering so a caller-supplied `limit` doesn't
        # silently starve the filtered result of otherwise-valid past rows (see harness
        # completion report Section 1, "known limitation: approximate limit fidelity").
        self._overfetch_multiplier = 8
        # Default is write-blocked (Section 3: "reject write operations"). A retrospective
        # RUN (not just a leakage audit) needs somewhere to persist the new analysis
        # session/scenario candidates it generates -- those are brand-new rows about a new
        # evaluation run, not reads of old/future data, so they carry no temporal-leakage
        # risk. allow_write_passthrough=True is an explicit, logged opt-in for that case
        # only; the default constructor call stays fully read-only for leakage-audit use.
        self._allow_write_passthrough = allow_write_passthrough
        self._write_passthrough_log: list[tuple] = []

    def select(self, table: str, params: dict) -> list:
        policy = self._policies.get(table)
        if policy is None:
            self._record(table, "UNDEFINED_POLICY_FAIL_CLOSED", 0, 0, 0)
            return []
        query_params = dict(params or {})
        original_limit = query_params.get("limit")
        if original_limit is not None:
            try:
                query_params["limit"] = str(int(original_limit) * self._overfetch_multiplier)
            except (TypeError, ValueError):
                pass
        rows = self._client.select(table, query_params)
        kept, fut, unverified = policy(rows, self.as_of)
        if original_limit is not None:
            try:
                kept = kept[: int(original_limit)]
            except (TypeError, ValueError):
                pass
        self._record(table, policy.__name__, len(kept), fut, unverified)
        return kept

    def _record(self, table: str, policy_name: str, included: int, fut: int, unverified: int):
        entry = self._audit.setdefault(table, TableAudit(policy_name))
        entry.included += included
        entry.excluded_future += fut
        entry.excluded_unverified += unverified

    def audit_report(self) -> dict:
        return {"as_of_date": self.as_of.isoformat(), "policy_mode": self.policy_mode,
                "tables": {t: a.as_dict() for t, a in self._audit.items()}}

    # ---- write operations: blocked by default, explicit logged passthrough only ------
    def _write(self, op: str, *a, **kw):
        if not self._allow_write_passthrough:
            raise WriteBlockedError(f"AsOfEvaluationClient is read-only: {op}() is not permitted "
                                     f"(construct with allow_write_passthrough=True for a retrospective RUN)")
        self._write_passthrough_log.append((op, a, kw))
        return getattr(self._client, op)(*a, **kw)

    def insert(self, *a, **kw):
        return self._write("insert", *a, **kw)

    def update(self, *a, **kw):
        return self._write("update", *a, **kw)

    def delete(self, *a, **kw):
        return self._write("delete", *a, **kw)

    def rpc(self, *a, **kw):
        return self._write("rpc", *a, **kw)

    def storage_upload(self, *a, **kw):
        return self._write("storage_upload", *a, **kw)
